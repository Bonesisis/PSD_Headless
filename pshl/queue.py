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

from .bundles import report_documents, week_folder
from .engine import _find_unique, metadata, walk
from .jobs import normalize
from .profiles import expand_profile
from .renderers import render_job


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
            db.execute(
                "CREATE TABLE IF NOT EXISTS reviews (job TEXT, item INTEGER, sha256 TEXT, "
                "decision TEXT, reason TEXT, PRIMARY KEY(job, item))"
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
            decisions = db.execute(
                "SELECT item, sha256, decision, reason FROM reviews WHERE job=?", (identifier,)
            ).fetchall()
        if row is None:
            raise ValueError("Unbekannter Auftrag")
        state = json.loads(row[0])
        state["reviews"] = {
            str(i): {"sha256": sha, "decision": d, "reason": r} for i, sha, d, r in decisions
        }
        return state

    def review_item(self, identifier: str, index: int, decision: str, reason: str = "") -> dict:
        """Short DB transaction, independent of the renderer's long OS worker lock."""
        if decision not in {"approved", "rejected"}:
            raise ValueError("Entscheidung muss approved oder rejected sein")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("Unbekannter Auftrag")
            state = json.loads(row[0])
            if state["status"] not in {"rendering", "review", "error"} or state.get(
                "approved_bytes"
            ):
                raise ValueError("Auftrag ist nicht mehr in der Bildprüfung")
            item = next((r for r in state["results"] if r["index"] == index), None)
            if not item or item["status"] != "success":
                raise ValueError("Bild ist noch nicht erfolgreich gerendert")
            if digest(item["preview"]) != item["sha256"]:
                raise ValueError("Vorschau wurde verändert; erneut prüfen")
            db.execute(
                "INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                (identifier, index, item["sha256"], decision, reason),
            )
        return self.get(identifier)

    def list(self) -> list[dict]:
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT state FROM jobs ORDER BY rowid DESC")
            ]

    def save(self, state: dict) -> None:
        state["updated"] = utcnow()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                state.get("series_approved")
                and state["status"] == "approved"
                and not state.get("approved_bytes")
                and not state.get("selected_indices")
            ):
                if db.execute(
                    "SELECT 1 FROM reviews WHERE job=? AND decision='rejected'", (state["id"],)
                ).fetchone():
                    state["status"] = "review"
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
        packaged = [j for j in normalized if j.get("delivery")]
        if packaged and len(packaged) != len(normalized):
            raise ValueError("Wochenpakete und direkte Ausgaben nicht in einem Batch mischen")
        for job in packaged:
            delivery = job["delivery"]
            expected_week = week_folder(delivery.get("date"))
            relative = Path(job["output"]).relative_to(Path(output).resolve())
            category = delivery.get("category")
            if category:
                from .bundles import filename_component

                if not isinstance(category, str) or filename_component(category) != category:
                    raise ValueError("Ungültige Kategorie")
                expected_package = (
                    Path(delivery["platform"]) / category / expected_week
                ).as_posix()
                expected_parts = (delivery["platform"], category, expected_week, delivery["folder"])
            else:
                expected_package = expected_week
                expected_parts = (expected_week, delivery["platform"], delivery["folder"])
            if (
                delivery.get("package") != expected_package
                or delivery.get("week") != expected_week
                or relative.parts[:-1] != expected_parts
            ):
                raise ValueError("Ungültiger Pfad für Wochenpaket")
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
        if any(j.get("renderer") == "photocraft" for j in normalized):
            from .photocraft import configuration

            state["photocraft_runtime"] = configuration()
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
            if next_outputs.intersection(outputs) and not packaged:
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
            self._reserve_packages(db, state)
            outputs = [j["output"] for j in state["jobs"]]
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

    def _reserve_packages(self, db, state: dict) -> None:
        """Reserve whole package names in the same transaction as job submission."""
        weeks = sorted({j["delivery"]["package"] for j in state["jobs"] if j.get("delivery")})
        if not weeks:
            return
        root = Path(state["output"])
        reserved = set()
        for row in db.execute("SELECT state FROM jobs"):
            other = json.loads(row[0])
            reserved.update(
                os.path.normcase(str(Path(other["output"]) / p)) for p in other.get("packages", [])
            )
        state["packages"] = []
        for week in weeks:
            for revision in range(1, 1001):
                package = week if revision == 1 else f"{week}-R{revision:02}"
                path = root / package
                if not path.exists() and os.path.normcase(str(path)) not in reserved:
                    break
            else:
                raise ValueError("Zu viele Revisionen für dieselbe Kalenderwoche")
            state["packages"].append(package)
            for job in state["jobs"]:
                if job["delivery"]["package"] == week:
                    relative = Path(job["output"]).relative_to(root / week)
                    job["output"] = str(root / package / relative)
                    job["delivery"]["package"] = package

    def decide(self, identifier: str, action: str, reason: str = "", series: bool = False) -> dict:
        if not isinstance(series, bool):
            raise ValueError("series muss true oder false sein")
        with self.worker_lock() as acquired:
            if not acquired:
                raise ValueError("Worker beschäftigt; bitte Freigabe kurz erneut versuchen")
            state = self.get(identifier)
            if action in {"approve", "publish-reviewed"} and state["status"] == "review":
                selected = []
                for item in state["results"]:
                    if digest(item["preview"]) != item["sha256"]:
                        raise ValueError("Vorschau wurde verändert; erneut prüfen")
                    review = state["reviews"].get(str(item["index"]), {})
                    if action == "publish-reviewed":
                        if review.get("sha256") != item["sha256"]:
                            raise ValueError("Zuerst jedes Bild prüfen und bewerten")
                        if review["decision"] == "approved":
                            selected.append(item["index"])
                    else:
                        if review.get("decision") == "rejected":
                            raise ValueError(
                                "Abgelehnte Bilder vorhanden; geprüfte Auswahl verwenden"
                            )
                        selected.append(item["index"])
                if not selected:
                    raise ValueError("Mindestens ein Bild muss freigegeben sein")
                if len(selected) != len(state["results"]) and state["schedule"].get("every_days"):
                    raise ValueError("Teilfreigaben benötigen einen einmaligen Auftrag ohne Serie")
                state["selected_indices"] = selected
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
            # Freeze review decisions atomically with the transition to publication.
            # An item may have been rejected while the preview hashes were being read.
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if action in {"approve", "publish-reviewed"}:
                    decisions = {
                        str(i): {"sha256": sha, "decision": d, "reason": r}
                        for i, sha, d, r in db.execute(
                            "SELECT item, sha256, decision, reason FROM reviews WHERE job=?",
                            (identifier,),
                        )
                    }
                    if decisions != state["reviews"]:
                        raise ValueError("Bildbewertungen wurden geändert; Freigabe erneut prüfen")
                state["updated"] = utcnow()
                db.execute("UPDATE jobs SET state=? WHERE id=?", (json.dumps(state), identifier))
            return self.get(identifier)

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
                if digest(path) != value:
                    raise ValueError("Eingabedateien wurden seit Auftragserteilung verändert")
            if state.get("photocraft_runtime"):
                from .photocraft import configuration

                if configuration() != state["photocraft_runtime"]:
                    raise ValueError(
                        "PhotoCraft-Konfiguration wurde seit Auftragserteilung verändert"
                    )
        except (ValueError, OSError) as exc:
            state.update(status="error", error=str(exc))
            self.save(state)
            return

        def record(index, result):
            job = state["jobs"][index]
            preview = Path(result["output"])
            if result.get("before_source"):
                before = Image.open(result.pop("before_source")).convert("RGBA")
                info = {}
            else:
                psd = PSDImage.open(job["template"])
                layer = (
                    _find_unique(list(walk(psd)), job["artboard"], "artboard")[1]
                    if job.get("artboard")
                    else psd
                )
                before = layer.composite(apply_icc=False).convert("RGBA")
                info = metadata(psd)
            before_path = folder / f"{index}-before.png"
            before.save(
                before_path, **{k: v for k, v in info.items() if k in {"dpi", "icc_profile"} and v}
            )
            with Image.open(preview) as after:
                if after.size != before.size:
                    raise ValueError("Vorschau und Original haben verschiedene Abmessungen")
                changed = ImageChops.difference(
                    before.convert("RGB"), after.convert("RGB")
                ).getbbox()
            if changed is None:
                result["warnings"].append("Keine sichtbare Änderung: Ebenenziele und Text prüfen")
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
            state["done"] += 1
            self.save(state)

        groups = {}
        for index, job in enumerate(state["jobs"]):
            old = previous.get(index)
            if old and Path(old["preview"]).exists() and digest(old["preview"]) == old["sha256"]:
                state["results"].append(old)
                state["done"] += 1
                self.save(state)
                continue
            key = (
                json.dumps(
                    {
                        k: v
                        for k, v in job.items()
                        if k not in {"output", "artboard", "artboard_name", "delivery"}
                    },
                    sort_keys=True,
                )
                if job.get("renderer") == "photocraft"
                else str(index)
            )
            groups.setdefault(key, []).append((index, job))
        for entries in groups.values():
            state["active"] = {
                "index": entries[0][0],
                "output": entries[0][1]["output"],
                "exports": len(entries),
                "phase": "Text setzen und Artboards rendern",
            }
            self.save(state)
            try:
                if any(Path(j["output"]).exists() for _, j in entries):
                    raise ValueError("Ziel existiert bereits")
                previews = [
                    {**j, "output": str(folder / f"{i}{Path(j['output']).suffix}")}
                    for i, j in entries
                ]
                if entries[0][1].get("renderer") == "photocraft":
                    from .photocraft import render_group

                    render_group(previews, lambda i, r: record(entries[i][0], r))
                else:
                    record(entries[0][0], render_job(previews[0]))
            except Exception as exc:
                # A late source-integrity or sandbox error invalidates this whole render group,
                # including previews emitted before that error. Never approve partial trust.
                affected = {i for i, _ in entries}
                state["results"] = [r for r in state["results"] if r["index"] not in affected]
                for index, job in entries:
                    state["results"].append(
                        {
                            "index": index,
                            "status": "error",
                            "output": job["output"],
                            "error": str(exc),
                            "warnings": [],
                        }
                    )
                state["done"] = len(state["results"])
                state["error"] = str(exc)
                self.save(state)
        state.pop("active", None)
        state["results"].sort(key=lambda r: r["index"])
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
            self._claim_packages(state)
            for item in state["results"]:
                if item["index"] not in state.get("selected_indices", range(state["total"])):
                    item["excluded"] = True
                    continue
                self._publish_file(state, item)
            self._publish_reports(state)
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
                if state.get("photocraft_runtime"):
                    from .photocraft import configuration

                    if configuration() != state["photocraft_runtime"]:
                        raise ValueError("PhotoCraft-Konfiguration verändert; Serie erneut prüfen")
                child = self.submit(
                    jobs, state["templates"], state["output"], state["font"], next_schedule
                )
                child["series_approved"] = state["series_approved"]
                self.save(child)
                state["next_id"] = child["id"]
        except Exception as exc:
            state.update(status="error", error=str(exc))
        self.save(state)

    def _claim_packages(self, state: dict) -> None:
        for package in state.get("packages", []):
            root = Path(state["output"]) / package
            if not root.resolve().is_relative_to(Path(state["output"])):
                raise ValueError("Wochenordner verlässt den Ausgabeordner")
            if package in state.get("package_claims", []):
                if not root.is_dir():
                    raise ValueError("Reservierter Wochenordner fehlt")
                continue
            marker = root / ".pshl-reservation"
            if root.exists():
                if not marker.is_file() or marker.read_text(encoding="utf-8") != state["id"]:
                    raise ValueError(
                        "Wochenordner existiert bereits; fremde Dateien werden nicht ergänzt"
                    )
            else:
                root.parent.mkdir(parents=True, exist_ok=True)
                root.mkdir()
                with marker.open("x", encoding="utf-8") as stream:
                    stream.write(state["id"])
                    stream.flush()
                    os.fsync(stream.fileno())
            state.setdefault("package_claims", []).append(package)
            self.save(state)

    def _publish_file(self, state: dict, item: dict) -> None:
        if digest(item["preview"]) != item["sha256"]:
            raise ValueError("Freigegebene Vorschau oder Report wurde verändert")
        target = Path(item["output"])
        if not target.resolve().is_relative_to(Path(state["output"])):
            raise ValueError("Ausgabepfad verlässt den konfigurierten Ordner")
        if item.get("published"):
            if not target.exists() or digest(target) != item["sha256"]:
                raise ValueError("Bereits veröffentlichte Datei fehlt oder wurde verändert")
            return
        if item.get("publishing") and target.exists() and digest(target) == item["sha256"]:
            item["published"] = True
            self.save(state)
            return
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

    def _publish_reports(self, state: dict) -> None:
        if not state.get("packages"):
            return
        if not state.get("package_reports"):
            state["publication_time"] = utcnow()
            state["reviews"] = self.get(state["id"])["reviews"]
            folder = self.root / state["id"] / "reports" / uuid4().hex
            folder.mkdir(parents=True)
            reports = []
            for package in state["packages"]:
                for name, content in report_documents(state, package).items():
                    preview = folder / f"{hashlib.sha256(package.encode()).hexdigest()[:16]}-{name}"
                    with preview.open("x", encoding="utf-8") as stream:
                        json.dump(content, stream, ensure_ascii=False, indent=2)
                        stream.flush()
                        os.fsync(stream.fileno())
                    reports.append(
                        {
                            "preview": str(preview),
                            "output": str(Path(state["output"]) / package / name),
                            "sha256": digest(preview),
                        }
                    )
            state["package_reports"] = reports
            self.save(state)
        for report in state["package_reports"]:
            self._publish_file(state, report)
        for package in state["packages"]:
            marker = Path(state["output"]) / package / ".pshl-reservation"
            if marker.is_file() and marker.read_text(encoding="utf-8") == state["id"]:
                marker.unlink()
