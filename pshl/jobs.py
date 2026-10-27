"""Batch loading, bounded parallel execution and durable reports."""

from __future__ import annotations

import csv
import io
import json
import multiprocessing
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import yaml

from .engine import RenderError
from .profiles import expand_profile
from .renderers import render_job


def parse_jobs(raw, extension=".json"):
    if extension.lower() == ".csv":
        jobs = []
        for row in csv.DictReader(io.StringIO(raw)):
            try:
                if not any(
                    row.get(key) for key in ("layers", "smart_objects", "images", "profile")
                ):
                    raise ValueError("CSV row needs layers, smart_objects, images or profile")
                row["layers"] = json.loads(row.get("layers") or "{}")
                for field in ("styles", "fonts", "smart_objects", "images", "fields"):
                    if row.get(field):
                        row[field] = json.loads(row[field])
                    else:
                        row.pop(field, None)
                if "shrink" in row:
                    flag = (row["shrink"] or "").lower()
                    if flag not in {"", "true", "false", "1", "0", "yes", "no"}:
                        raise ValueError("shrink must be true or false")
                    row["shrink"] = flag in {"true", "1", "yes"}
                jobs.append(row)
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                jobs.append({"_error": f"Invalid CSV row: {exc}"})
        return jobs
    try:
        data = yaml.safe_load(raw) if extension.lower() in {".yaml", ".yml"} else json.loads(raw)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise RenderError(f"Ungültige Job-Datei: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("jobs", [data])
    if not isinstance(data, list) or not data:
        raise RenderError("Job-Datei muss einen Auftrag oder eine nicht-leere Liste enthalten")
    return data


def resolve_path(value, root):
    if not isinstance(value, str) or not value:
        raise RenderError("Pfad muss eine nicht-leere Zeichenfolge sein")
    if os.name != "nt" and (value.startswith("\\\\") or (len(value) > 1 and value[1] == ":")):
        raise RenderError("Windows-/UNC-Pfad auf diesem System nicht nutzbar; NAS zuerst mounten")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(root) / path).resolve()


def normalize(job, template_root, output_root, font=None, restricted=False):
    from .font_mapping import resolve_style

    if not isinstance(job, dict):
        raise RenderError("Auftrag muss ein Objekt sein")
    if "_error" in job:
        raise RenderError(job["_error"])
    job = dict(job)
    if job.get("renderer", "python") not in {"python", "photocraft"}:
        raise RenderError("Unbekannte Rendering-Engine")
    for key, root in [("template", template_root), ("output", output_root)]:
        path = resolve_path(job.get(key), root)
        if restricted and not path.is_relative_to(Path(root).resolve()):
            raise RenderError(f"{key}: Pfad verlässt den konfigurierten Ordner")
        job[key] = str(path)
    job["images"] = {k: dict(v) for k, v in job.get("images", {}).items()}
    for spec in job["images"].values():
        path = resolve_path(spec.get("source"), template_root)
        if restricted and not path.is_relative_to(Path(template_root).resolve()):
            raise RenderError("Bilddatei muss im Vorlagenordner liegen")
        spec["source"] = str(path)
    if job.get("font") or font:
        job["font"] = str(resolve_path(job.get("font") or font, template_root))
    job["fonts"] = {k: str(resolve_path(v, template_root)) for k, v in job.get("fonts", {}).items()}
    job["styles"] = {k: resolve_style(v, template_root) for k, v in job.get("styles", {}).items()}
    for style in job["styles"].values():
        if style.get("font"):
            style["font"] = str(resolve_path(style["font"], template_root))
    job["smart_objects"] = {
        k: resolve_style(v, template_root) for k, v in job.get("smart_objects", {}).items()
    }
    for spec in job["smart_objects"].values():
        if spec.get("font"):
            spec["font"] = str(resolve_path(spec["font"], template_root))
        spec["targets"] = [
            resolve_style(target, template_root) for target in spec.get("targets", [])
        ]
        for target in spec["targets"]:
            if target.get("font"):
                target["font"] = str(resolve_path(target["font"], template_root))
    if restricted:
        for path in [
            job.get("font"),
            *job["fonts"].values(),
            *(s.get("font") for s in job["styles"].values()),
            *(s.get("font") for s in job["smart_objects"].values()),
            *(t.get("font") for s in job["smart_objects"].values() for t in s["targets"]),
        ]:
            if path and not Path(path).is_relative_to(Path(template_root).resolve()):
                raise RenderError("Schriftdatei muss im Vorlagenordner liegen")
    if not isinstance(job.get("shrink", False), bool):
        raise RenderError("shrink muss true oder false sein")
    return job


def execute(index, job, dry_run):
    started = time.monotonic()
    try:
        result = render_job(job, dry_run)
    except Exception as exc:
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "warnings": []}
    return {"index": index, "elapsed_seconds": round(time.monotonic() - started, 3), **result}


def run_batch(
    jobs,
    template_root=".",
    output_root=".",
    workers=1,
    dry_run=False,
    font=None,
    report_path=None,
    progress=None,
    restricted=False,
):
    if not 1 <= workers <= 32:
        raise RenderError("workers muss zwischen 1 und 32 liegen")
    if not jobs:
        raise RenderError("Batch ist leer")
    expanded = []
    for raw in jobs:
        try:
            expanded.extend(expand_profile(raw, template_root, restricted))
        except Exception as exc:
            expanded.append({"_error": str(exc)})
    jobs = expanded
    results, ready, sources, destinations = {}, [], set(), {}
    started = time.monotonic()
    for i, raw in enumerate(jobs):
        try:
            job = normalize(raw, template_root, output_root, font, restricted)
            ready.append((i, job))
            sources.add(os.path.normcase(job["template"]))
            destinations.setdefault(os.path.normcase(job["output"]), []).append(i)
        except Exception as exc:
            results[i] = {"index": i, "status": "error", "error": str(exc), "warnings": []}
    report_file = Path(report_path).resolve() if report_path else None
    if report_file and (
        os.path.normcase(str(report_file)) in sources
        or os.path.normcase(str(report_file)) in destinations
    ):
        raise RenderError("Report darf keine Vorlage oder Auftragsausgabe überschreiben")
    report_handle = None
    if report_file:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_handle = report_file.open("x", encoding="utf-8")

    def record(result):
        results[result["index"]] = result
        if progress:
            progress(len(results), len(jobs), result)

    try:
        runnable = []
        for i, job in ready:
            key = os.path.normcase(job["output"])
            if key in sources or len(destinations[key]) > 1:
                record(
                    {
                        "index": i,
                        "status": "error",
                        "warnings": [],
                        "error": "Ausgabekollision mit Vorlage oder anderem Auftrag",
                    }
                )
            else:
                runnable.append((i, job))
        if workers == 1:
            for i, job in runnable:
                record(execute(i, job, dry_run))
        else:
            with ProcessPoolExecutor(
                max_workers=workers, mp_context=multiprocessing.get_context("spawn")
            ) as pool:
                iterator = iter(runnable)
                pending = {}

                def submit_next():
                    item = next(iterator, None)
                    if item:
                        i, job = item
                        pending[pool.submit(execute, i, job, dry_run)] = i

                for _ in range(workers):
                    submit_next()
                while pending:
                    completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in completed:
                        i = pending.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = {
                                "index": i,
                                "status": "error",
                                "error": str(exc),
                                "warnings": [],
                            }
                        record(result)
                        try:
                            submit_next()
                        except Exception as exc:
                            for remaining_i, _ in runnable:
                                if (
                                    remaining_i not in results
                                    and remaining_i not in pending.values()
                                ):
                                    record(
                                        {
                                            "index": remaining_i,
                                            "status": "error",
                                            "error": f"Worker unavailable: {exc}",
                                            "warnings": [],
                                        }
                                    )
                            iterator = iter(())
        ordered = [results[i] for i in range(len(jobs))]
        report = {
            "total": len(jobs),
            "success": sum(r["status"] == "success" for r in ordered),
            "checked": sum(r["status"] == "checked" for r in ordered),
            "errors": sum(r["status"] == "error" for r in ordered),
            "warnings": sum(len(r["warnings"]) for r in ordered),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "results": ordered,
        }
        if report_handle:
            json.dump(report, report_handle, ensure_ascii=False, indent=2)
            report_handle.flush()
            os.fsync(report_handle.fileno())
        return report
    finally:
        if report_handle:
            report_handle.close()
