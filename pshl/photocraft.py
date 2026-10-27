"""Optional PhotoCraft 0.9 CLI adapter, isolated in an offline Docker container.

Only our fixed text/artboard commands are exposed. No commands from a model are executed.
The CLI and image are operator-installed; this module never downloads either.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fontTools.ttLib import TTFont
from PIL import Image


def file_hash(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def configuration() -> dict:
    binary = os.getenv("PSHL_PHOTOCRAFT_CLI", "")
    image = os.getenv("PSHL_PHOTOCRAFT_IMAGE", "")
    expected = os.getenv("PSHL_PHOTOCRAFT_SHA256", "")
    if not binary or not image or len(expected) != 64:
        raise ValueError("PhotoCraft benötigt PSHL_PHOTOCRAFT_CLI, IMAGE und SHA256 am Server")
    if file_hash(binary).lower() != expected.lower():
        raise ValueError("PhotoCraft-Binärdatei stimmt nicht mit der freigegebenen SHA256 überein")
    return {"binary": str(Path(binary).resolve()), "image": image}


def walk(layers, prefix=""):
    for layer in layers:
        path = f"{prefix}/{layer['name']}" if prefix else layer["name"]
        yield path, layer
        yield from walk(layer.get("children", []), path)


def select(layers, selector: str, kind: str | None = None) -> dict:
    candidates = [
        (path, layer) for path, layer in walk(layers) if kind is None or layer["kind"] == kind
    ]
    matches = [layer for path, layer in candidates if path == selector]
    if not matches:
        matches = [layer for _, layer in candidates if layer["name"] == selector]
    if len(matches) != 1:
        raise ValueError(f"PhotoCraft-Ziel nicht eindeutig: {selector}")
    return matches[0]


def _filename(name: str) -> str:
    # Match the pinned CLI's artboard export filenames; reject collisions before exporting.
    return (
        "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name).strip().strip(".")
        or "layer"
    )


def exclusive_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pshl-", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        shutil.copyfile(source, temporary)
        reserve = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(reserve)
        try:
            os.replace(temporary, target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    finally:
        temporary.unlink(missing_ok=True)


class Sandbox:
    def __init__(self):
        self.config = configuration()
        self.name = "pshl-pc-" + uuid4().hex
        self.process = None
        self.counter = 0
        self.lines = queue.Queue(maxsize=4)

    def docker(self, *args) -> str:
        try:
            result = subprocess.run(
                ["docker", *map(str, args)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("PhotoCraft/Docker: Zeitlimit überschritten") from exc
        if result.returncode:
            raise ValueError("PhotoCraft/Docker: " + (result.stderr or result.stdout)[-2000:])
        return result.stdout

    def __enter__(self):
        try:
            self.docker(
                "create",
                "--pull=never",
                "--name",
                self.name,
                "--network=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=256",
                "--cpus=4",
                "--memory=10g",
                "--user=1000:1000",
                "--env=HOME=/work",
                self.config["image"],
                "sleep",
                "infinity",
            )
            self.docker("start", self.name)
            self.docker("cp", self.config["binary"], f"{self.name}:/work/photocraft-cli")
            self.docker("exec", "--user=0", self.name, "chmod", "755", "/work/photocraft-cli")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process:
            self.process.kill()
            self.process.wait(timeout=10)
            self.process.stdin.close()
            self.process.stdout.close()
        # The exact UUID container belongs to this invocation, never a user container.
        self.docker("rm", "--force", self.name)

    def cli(self, *args):
        return self.docker("exec", self.name, "/work/photocraft-cli", *args)

    def start_server(self):
        self.process = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                self.name,
                "/work/photocraft-cli",
                "serve",
                "--automation-read-root",
                "/work",
                "--automation-write-root",
                "/work/out",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )

        def read():
            try:
                for line in self.process.stdout:
                    self.lines.put(line)
            finally:
                self.lines.put(None)

        threading.Thread(target=read, daemon=True).start()

    def call(self, method, params=None):
        self.counter += 1
        request = {"id": self.counter, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        try:
            line = self.lines.get(timeout=180)
        except queue.Empty as exc:
            raise ValueError("PhotoCraft antwortet nicht innerhalb des Zeitlimits") from exc
        if not line:
            raise ValueError("PhotoCraft-Sitzung wurde beendet")
        result = json.loads(line)
        if result.get("id") != self.counter or result.get("error") or result.get("ok") is False:
            raise ValueError(f"PhotoCraft: {str(result.get('error', 'Protokollfehler'))[:1500]}")
        return result["result"]

    def command(self, name, params=None):
        return self.call("engine.execute", {"command": name, "params": params or {}})


def _operations(job: dict) -> list[dict]:
    if job.get("images"):
        raise ValueError("PhotoCraft-Adapter unterstützt zunächst nur Text und Artboards")
    operations = []
    for selector, text in job.get("layers", {}).items():
        operations.append(
            {"inner": selector, "text": text, **job.get("styles", {}).get(selector, {})}
        )
    for spec in job.get("smart_objects", {}).values():
        for target in spec.get("targets", []):
            operations.append({**{k: v for k, v in spec.items() if k != "targets"}, **target})
    if not operations:
        raise ValueError("PhotoCraft-Auftrag benötigt explizite Textziele")
    allowed = {"outer", "inner", "text", "font", "font_family", "font_style", "box", "min_size"}
    for operation in operations:
        if set(operation) - allowed:
            raise ValueError(
                f"Nicht unterstützte PhotoCraft-Textoptionen: {sorted(set(operation) - allowed)}"
            )
        if not all(
            operation.get(k) for k in ("inner", "text", "font", "font_family", "font_style", "box")
        ):
            raise ValueError(
                "PhotoCraft-Text benötigt inner, text, font, font_family, font_style und box"
            )
        box = operation["box"]
        if (
            not isinstance(box, list)
            or len(box) != 4
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
            or box[2] <= box[0]
            or box[3] <= box[1]
        ):
            raise ValueError("Textfeld benötigt [links, oben, rechts, unten] mit positiver Größe")
        with TTFont(operation["font"]) as font:
            cmap = font.getBestCmap() or {}
            if any(ord(c) not in cmap for c in operation["text"] if not c.isspace()):
                raise ValueError("Schrift enthält nicht alle benötigten Zeichen")
    return operations


def _edit(sandbox: Sandbox, parent: dict, operations: list[dict]) -> list[str]:
    warnings, seen = [], set()
    # Resolve all IDs before names/text change; edit shared source content exactly once.
    groups = {}
    for op in operations:
        if op.get("outer"):
            outer = select(parent["layers"], op["outer"], "Smart Object")
            source = outer.get("smartSource", {})
            if source.get("kind") != "embedded" or not source.get("uuid"):
                raise ValueError("Nur eingebettete Smart Objects werden bearbeitet")
            key = source["uuid"]
            groups.setdefault(key, (outer["id"], []))[1].append(op)
        else:
            groups.setdefault("direct", (None, []))[1].append(op)
    for key, (outer_id, specs) in groups.items():
        sandbox.call("doc.select", {"index": 0})
        if outer_id is not None:
            opened = sandbox.command("layer.smartObjects.editContents", {"layer": outer_id})
            child_index = opened["document"]
        document = sandbox.call("doc.inspect")
        edits = {}
        for spec in specs:
            layer = select(document["layers"], spec["inner"], "Type")
            canonical = {k: v for k, v in spec.items() if k != "outer"}
            if layer["id"] in edits and edits[layer["id"]] != canonical:
                raise ValueError("Widersprüchliche Änderungen an gemeinsamem Smart-Object-Inhalt")
            edits[layer["id"]] = canonical
        for layer_id, spec in edits.items():
            if (key, layer_id) in seen:
                raise ValueError("Doppeltes Textziel")
            seen.add((key, layer_id))
            box = spec["box"]
            if not (
                0 <= box[0] < box[2] <= document["width"]
                and 0 <= box[1] < box[3] <= document["height"]
            ):
                raise ValueError("Textfeld liegt außerhalb des Dokuments")
            before = sandbox.command("type.info", {"layer": layer_id})
            if before.get("warp") or len(before.get("runs", [])) != 1:
                raise ValueError(
                    "Verzerrter oder gemischt formatierter Text wird noch nicht unterstützt"
                )
            sandbox.command(
                "type.setStyle",
                {"layer": layer_id, "font": spec["font_family"], "fontStyle": spec["font_style"]},
            )
            sandbox.command("type.edit", {"layer": layer_id, "text": spec["text"]})
            metrics = sandbox.command("type.info", {"layer": layer_id})
            x0, y0, x1, y1 = metrics["bounds"]
            size = metrics["runs"][0]["style"]["size_pt"]
            factor = min(
                1, (box[2] - box[0]) / max(1, x1 - x0), (box[3] - box[1]) / max(1, y1 - y0)
            )
            fitted = round(size * factor * 0.98, 2)
            if fitted < spec.get("min_size", 12):
                raise ValueError("Text passt nicht oberhalb der Mindestschriftgröße")
            sandbox.command("type.setStyle", {"layer": layer_id, "size": fitted})
            metrics = sandbox.command("type.info", {"layer": layer_id})
            x0, y0, x1, y1 = metrics["bounds"]
            sandbox.command(
                "type.edit",
                {
                    "layer": layer_id,
                    "move": [(box[0] + box[2] - x0 - x1) / 2, (box[1] + box[3] - y0 - y1) / 2],
                },
            )
            final = sandbox.command("type.info", {"layer": layer_id})
            x0, y0, x1, y1 = final["bounds"]
            if final.get("warnings"):
                raise ValueError("PhotoCraft-Schriftwarnung: " + str(final["warnings"]))
            if not (box[0] <= x0 < x1 <= box[2] and box[1] <= y0 < y1 <= box[3]):
                raise ValueError("Text passt nach dem Setzen nicht in das Textfeld")
            if fitted < size:
                warnings.append(
                    f"{spec['inner']}: Schrift von {size:g} auf {fitted:g} pt angepasst"
                )
        if outer_id is not None:
            sandbox.command("layer.smartObjects.saveContents")
            sandbox.call("doc.close", {"index": child_index})
    sandbox.call("doc.select", {"index": 0})
    return warnings


def render_group(jobs: list[dict], callback, dry_run: bool = False) -> None:
    """Edit once per identical template/text set, export all requested formats/artboards."""
    operations = _operations(jobs[0])
    source = Path(jobs[0]["template"])
    initial_hash = file_hash(source)
    for job in jobs:
        if Path(job["output"]).exists() or Path(job["output"]).resolve() == source.resolve():
            raise ValueError("Ausgabe existiert bereits oder entspricht der Vorlage")
        if not job.get("artboard") or Path(job["output"]).suffix.lower() not in {
            ".png",
            ".jpg",
            ".jpeg",
        }:
            raise ValueError("PhotoCraft-Adapter benötigt ein Artboard und PNG/JPEG-Ausgabe")
    with Sandbox() as sandbox, tempfile.TemporaryDirectory(prefix="pshl-pc-") as temp:
        local = Path(temp)
        sandbox.docker("cp", source, f"{sandbox.name}:/work/in/original.psd")
        sandbox.docker("exec", sandbox.name, "mkdir", "-p", "/work/.local/share/fonts")
        for i, spec in enumerate(operations):
            font_path = f"/work/.local/share/fonts/{i}{Path(spec['font']).suffix.lower()}"
            sandbox.docker("cp", spec["font"], f"{sandbox.name}:{font_path}")
        sandbox.docker("exec", sandbox.name, "fc-cache", "-f")
        for spec in operations:
            matched = sandbox.docker(
                "exec",
                sandbox.name,
                "fc-match",
                "-f",
                "%{file}",
                f"{spec['font_family']}:style={spec['font_style']}",
            ).strip()
            actual = sandbox.docker("exec", sandbox.name, "sha256sum", matched).split()[0]
            if actual != file_hash(spec["font"]):
                raise ValueError("PhotoCraft würde eine andere Schriftdatei verwenden")
        sandbox.start_server()
        opened = sandbox.call("doc.open", {"path": "in/original.psd"})
        if opened.get("warnings"):
            raise ValueError("PhotoCraft-Importwarnung: " + str(opened["warnings"]))
        parent = sandbox.call("doc.inspect")
        if any(
            layer.get("smartSource", {}).get("kind") == "linked"
            for _, layer in walk(parent["layers"])
        ):
            raise ValueError("Externe Smart Objects werden nicht automatisch geladen")
        boards = {job["artboard"]: select(parent["layers"], job["artboard"]) for job in jobs}
        if any(not b.get("artboard") or not b.get("visible") for b in boards.values()):
            raise ValueError("Auswahl muss ein sichtbares Artboard sein")
        names = [_filename(b["name"]) for b in boards.values()]
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError("PhotoCraft-Artboardnamen würden beim Export kollidieren")
        warnings = _edit(sandbox, parent, operations)
        if dry_run:
            for i, job in enumerate(jobs):
                callback(i, {"status": "checked", "output": job["output"], "warnings": warnings})
            return
        saved = sandbox.call("doc.save", {"path": "edited.psd", "format": "psd"})
        if saved.get("warnings"):
            raise ValueError("PhotoCraft-Speicherwarnung: " + str(saved["warnings"]))
        sandbox.call("doc.close", {"index": 0})

        def export(path, destination, fmt, selectors):
            info = json.loads(sandbox.cli("info", path, "--compact"))
            selected = {s: select(info["layers"], s) for s in selectors}
            directory = f"/work/out/{destination}"
            params = {
                "dir": directory,
                "format": fmt,
                "prefix": "",
                "quality": 12,
                "artboards": [b["id"] for b in selected.values()],
            }
            result = json.loads(
                sandbox.cli(
                    "run",
                    path,
                    "--cmd",
                    "file.export.artboardsToFiles",
                    "--params",
                    json.dumps(params),
                )
            )
            files = result["result"]["files"]
            expected = {s: f"{directory}/{_filename(b['name'])}.{fmt}" for s, b in selected.items()}
            if sorted(files) != sorted(expected.values()):
                raise ValueError("PhotoCraft lieferte unerwartete Artboard-Dateien")
            sandbox.docker("cp", f"{sandbox.name}:{directory}", local / destination)
            return {s: local / destination / PurePosixPath(p).name for s, p in expected.items()}

        baselines = export("/work/in/original.psd", "before", "png", boards)
        for fmt in ("png", "jpg"):
            indices = [
                i
                for i, j in enumerate(jobs)
                if ("png" if Path(j["output"]).suffix.lower() == ".png" else "jpg") == fmt
            ]
            if not indices:
                continue
            images = export(
                "/work/out/edited.psd",
                fmt,
                fmt,
                dict.fromkeys(jobs[i]["artboard"] for i in indices),
            )
            for i in indices:
                job = jobs[i]
                image, before = images[job["artboard"]], baselines[job["artboard"]]
                with Image.open(image) as rendered:
                    if list(rendered.size) != boards[job["artboard"]]["artboard"]["rect"][2:]:
                        raise ValueError("Artboard-Abmessungen stimmen nicht")
                exclusive_copy(image, Path(job["output"]))
                callback(
                    i,
                    {
                        "status": "success",
                        "output": job["output"],
                        "warnings": list(warnings),
                        "before_source": str(before),
                        "renderer": "photocraft",
                    },
                )
    if file_hash(source) != initial_hash:
        raise ValueError("Originaldatei wurde während der Verarbeitung extern verändert")


def render_job(job: dict, dry_run=False) -> dict:
    result = {}

    def capture(_, value):
        result.update({k: v for k, v in value.items() if k != "before_source"})

    render_group([job], capture, dry_run)
    return result
