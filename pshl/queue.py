"""Single-worker durable review queue. Approved previews are the published bytes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops
from psd_tools import PSDImage

from .engine import _find_unique, metadata, render_job, walk
from .jobs import normalize
from .profiles import expand_profile


def digest(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewQueue:
    """SQLite must live on local disk, not a network share. Outputs may be on NAS."""

    def __init__(self, directory: str | Path):
        self.root = Path(directory).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, state TEXT NOT NULL)"
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / "queue.sqlite3", timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def worker_lock(self):
        """OS releases this lock after crashes; never reclaim a live worker's job."""
        with (self.root / "worker.lock").open("a+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                # Windows byte-range locks also forbid reading the locked byte.
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    yield False
                    return
            else:
                import fcntl

                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    yield False
                    return
            try:
                yield True
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def get(self, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Unbekannter Auftrag")
        return json.loads(row[0])

    def list(self) -> list[dict]:
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT state FROM jobs ORDER BY rowid DESC")
            ]

    def save(self, state: dict) -> None:
        state["updated"] = utcnow()
        with self.connection() as db:
            db.execute("UPDATE jobs SET state=? WHERE id=?", (json.dumps(state), state["id"]))

    def submit(
        self,
        jobs: list,
        templates: str,
        output: str,
        font: str | None = None,
        schedule: dict | None = None,
    ) -> dict:
        if not jobs or not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
            raise ValueError("Auftrag benötigt eine nicht-leere Liste von Objekten")
        schedule = dict(schedule or {})
        if schedule:
            at = datetime.fromisoformat(schedule["at"])
            if at.tzinfo is None:
                raise ValueError("Zeitplan benötigt eine ISO-Zeit mit UTC-Offset")
            schedule["at"] = at.isoformat()
            days = schedule.get("every_days", 0)
            if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 366:
                raise ValueError("every_days muss 0–366 sein")
            if days:
                if not schedule.get("date_fields") or any("profile" not in job for job in jobs):
                    raise ValueError("Wiederholung benötigt Profilaufträge und date_fields")
                for job in jobs:
                    for field in schedule["date_fields"]:
                        datetime.strptime(job["fields"][field], "%Y-%m-%d")
        expanded = []
        for job in jobs:
            expanded.extend(expand_profile(job, templates, restricted=True))
        normalized = [normalize(job, templates, output, font, restricted=True) for job in expanded]
        outputs = [job["output"] for job in normalized]
        if len(set(os.path.normcase(p) for p in outputs)) != len(outputs):
            raise ValueError("Doppelte Ausgaben im Auftrag")
        if any(Path(p).suffix.lower() not in {".png", ".jpg", ".jpeg"} for p in outputs):
            raise ValueError("Freigabeworkflow unterstützt PNG/JPEG")
        if any(
            job.get("format", Path(job["output"]).suffix[1:]).lower() not in {"png", "jpg", "jpeg"}
            for job in normalized
        ):
            raise ValueError("Freigabeworkflow unterstützt PNG/JPEG")
        state = {
            "id": uuid4().hex,
            "status": "queued",
            "created": utcnow(),
            "done": 0,
            "total": len(normalized),
            "jobs": normalized,
            "original_jobs": jobs,
            "templates": str(Path(templates).resolve()),
            "output": str(Path(output).resolve()),
            "font": font,
            "schedule": schedule,
            "results": [],
            "series_approved": False,
        }
        inputs = set()
        for raw in jobs:
            if raw.get("profile"):
                inputs.add(str((Path(templates) / raw["profile"]).resolve()))
        for job in normalized:
            inputs.add(job["template"])
            inputs.update(spec["source"] for spec in job["images"].values())
            inputs.update(job["fonts"].values())
            inputs.update(
                p
                for p in [
                    job.get("font"),
                    *(s.get("font") for s in job["styles"].values()),
                    *(s.get("font") for s in job["smart_objects"].values()),
                    *(t.get("font") for s in job["smart_objects"].values() for t in s["targets"]),
                ]
                if p
            )
        state["input_hashes"] = {path: digest(path) for path in inputs}
        fingerprint = hashlib.sha256(
            json.dumps(
                {"jobs": normalized, "schedule": schedule, "inputs": state["input_hashes"]},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if schedule.get("every_days"):
            following = json.loads(json.dumps(jobs))
            for job in following:
                for field in schedule["date_fields"]:
                    job["fields"][field] = (
                        datetime.strptime(job["fields"][field], "%Y-%m-%d")
                        + timedelta(days=schedule["every_days"])
                    ).strftime("%Y-%m-%d")
            next_outputs = {
                normalize(entry, templates, output, font, True)["output"]
                for raw in following
                for entry in expand_profile(raw, templates, True)
            }
            if next_outputs.intersection(outputs):
                raise ValueError(
                    "Wiederholte Exporte müssen ein fortgeschriebenes Datumsfeld im Dateinamen verwenden"
                )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT state FROM jobs WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if existing:
                return {**json.loads(existing[0]), "duplicate": True}
            if any(Path(p).exists() for p in outputs):
                raise ValueError("Mindestens eine Ausgabe existiert bereits")
            if any(Path(p).is_relative_to(self.root) for p in outputs):
                raise ValueError("Ausgaben dürfen nicht im internen Statusordner liegen")
            for row in db.execute("SELECT state FROM jobs"):
                other = json.loads(row[0])
                if other["status"] not in {"finished", "rejected"}:
                    reserved = {os.path.normcase(j["output"]) for j in other["jobs"]}
                    if reserved.intersection(os.path.normcase(p) for p in outputs):
                        raise ValueError("Ausgabepfad ist bereits in der Warteschlange reserviert")
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?)", (state["id"], fingerprint, json.dumps(state))
            )
        return state

    def decide(self, identifier: str, action: str, reason: str = "", series: bool = False) -> dict:
        if not isinstance(series, bool):
            raise ValueError("series muss true oder false sein")
        with self.worker_lock() as acquired:
            if not acquired:
                raise ValueError("Worker beschäftigt; bitte Freigabe kurz erneut versuchen")
            state = self.get(identifier)
            if action == "approve" and state["status"] == "review":
                for item in state["results"]:
                    if digest(item["preview"]) != item["sha256"]:
                        raise ValueError("Vorschau wurde verändert; erneut prüfen")
                state.update(status="approved", series_approved=bool(series))
            elif action == "reject" and state["status"] in {
                "review",
                "approved",
                "queued",
                "error",
            }:
                state.update(status="rejected", reason=reason or "Manuell abgelehnt")
            elif action == "retry" and state["status"] == "error":
                # Publication retries use the same approved bytes, never silently rerender.
                state["status"] = "approved" if state.get("approved_bytes") else "queued"
                state.pop("error", None)
            else:
                raise ValueError("Aktion im aktuellen Status nicht möglich")
            self.save(state)
            return state

    def tick(self) -> bool:
        """Process at most one item; safe to call from CLI or server concurrently."""
        with self.worker_lock() as acquired:
            if not acquired:
                return False
            for state in reversed(self.list()):
                status = state["status"]
                if status in {"queued", "rendering"}:
                    self._preview(state)
                    return True
                if status in {"approved", "publishing"}:
                    at = state["schedule"].get("at")
                    if at and datetime.fromisoformat(at) > datetime.now(timezone.utc):
                        continue
                    self._publish(state)
                    return True
        return False

    def _preview(self, state: dict) -> None:
        previous = {item["index"]: item for item in state["results"] if item["status"] == "success"}
        state.update(status="rendering", results=[], done=0)
        self.save(state)
        folder = self.root / state["id"] / uuid4().hex
        folder.mkdir(parents=True)
        try:
            for path, value in state["input_hashes"].items():
                if not Path(path).resolve().is_relative_to(Path(state["templates"])):
                    raise ValueError("Eingabepfad verlässt den Vorlagenordner")
                if state["series_approved"] and digest(path) != value:
                    raise ValueError("Eingabedateien der freigegebenen Serie wurden verändert")
        except (ValueError, OSError) as exc:
            state.update(status="error", error=str(exc))
            self.save(state)
            return
        for index, job in enumerate(state["jobs"]):
            try:
                old = previous.get(index)
                if (
                    old
                    and Path(old["preview"]).exists()
                    and digest(old["preview"]) == old["sha256"]
                ):
                    state["results"].append(old)
                    state["done"] += 1
                    self.save(state)
                    continue
                if Path(job["output"]).exists():
                    raise ValueError("Ziel existiert bereits")
                preview = folder / f"{index}{Path(job['output']).suffix}"
                result = render_job({**job, "output": str(preview)})
                psd = PSDImage.open(job["template"])
                layer = (
                    _find_unique(list(walk(psd)), job["artboard"], "artboard")[1]
                    if job.get("artboard")
                    else psd
                )
                before = layer.composite(apply_icc=False).convert("RGBA")
                before_path = folder / f"{index}-before.png"
                info = metadata(psd)
                before.save(before_path, dpi=info["dpi"], icc_profile=info["icc_profile"])
                with Image.open(preview) as after:
                    changed = ImageChops.difference(
                        before.convert("RGB"), after.convert("RGB")
                    ).getbbox()
                if changed is None:
                    result["warnings"].append(
                        "Keine sichtbare Änderung: Ebenenziele und Text prüfen"
                    )
                state["results"].append(
                    {
                        **result,
                        "index": index,
                        "output": job["output"],
                        "preview": str(preview),
                        "before": str(before_path),
                        "sha256": digest(preview),
                        "changed_box": changed,
                        "size": list(before.size),
                    }
                )
            except Exception as exc:
                state["results"].append(
                    {"index": index, "status": "error", "error": str(exc), "warnings": []}
                )
            state["done"] += 1
            self.save(state)
        failed = any(item["status"] == "error" for item in state["results"])
        state["status"] = (
            "error" if failed else "approved" if state["series_approved"] else "review"
        )
        if failed:
            state["error"] = "Vorschauprüfung fehlgeschlagen; Details im Report"
        self.save(state)

    def _publish(self, state: dict) -> None:
        state.update(status="publishing", approved_bytes=True)
        self.save(state)
        try:
            for item in state["results"]:
                if digest(item["preview"]) != item["sha256"]:
                    raise ValueError("Freigegebene Vorschau wurde verändert")
                target = Path(item["output"])
                if not target.resolve().is_relative_to(Path(state["output"])):
                    raise ValueError("Ausgabepfad verlässt den konfigurierten Ordner")
                if item.get("published"):
                    if not target.exists() or digest(target) != item["sha256"]:
                        raise ValueError("Bereits veröffentlichte Datei fehlt oder wurde verändert")
                    continue
                # Recovery after a crash between a complete copy and its DB commit.
                if item.get("publishing") and target.exists() and digest(target) == item["sha256"]:
                    item["published"] = True
                    self.save(state)
                    continue
                if target.exists():
                    raise ValueError(
                        f"Ausgabe existiert bereits (auch Teilkopien werden nicht überschrieben): {target}"
                    )
                item["publishing"] = True
                self.save(state)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as destination, Path(item["preview"]).open("rb") as source:
                    shutil.copyfileobj(source, destination)
                    destination.flush()
                    os.fsync(destination.fileno())
                item["published"] = True
                self.save(state)
            state["status"] = "finished"
            schedule = state["schedule"]
            if schedule.get("every_days") and not state.get("next_id"):
                days = schedule["every_days"]
                next_schedule = {
                    **schedule,
                    "at": (
                        datetime.fromisoformat(schedule["at"]) + timedelta(days=days)
                    ).isoformat(),
                }
                jobs = json.loads(json.dumps(state["original_jobs"]))
                for job in jobs:
                    for field in schedule["date_fields"]:
                        job["fields"][field] = (
                            datetime.strptime(job["fields"][field], "%Y-%m-%d")
                            + timedelta(days=days)
                        ).strftime("%Y-%m-%d")
                if any(digest(path) != value for path, value in state["input_hashes"].items()):
                    raise ValueError("Serieneingaben verändert; kein automatischer Folgeauftrag")
                child = self.submit(
                    jobs, state["templates"], state["output"], state["font"], next_schedule
                )
                child["series_approved"] = state["series_approved"]
                self.save(child)
                state["next_id"] = child["id"]
        except Exception as exc:
            state.update(status="error", error=str(exc))
        self.save(state)
