"""Portable weekly delivery layout, independent of rendering and AI planning."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path


def filename_component(value: str, limit: int = 110) -> str:
    """Keep PSD names readable; replace only filesystem-invalid characters."""
    name = unicodedata.normalize("NFC", value)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
    if not name or name in {".", ".."}:
        name = "Artboard"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", name):
        name = "_" + name
    if len(name) > limit:
        suffix = hashlib.sha256(value.encode()).hexdigest()[:8]
        name = name[: limit - 10].rstrip(" .") + "--" + suffix
    return name


def week_folder(value: str) -> str:
    try:
        day = date.fromisoformat(value)
        if day.isoformat() != value:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValueError("Wochenablage benötigt ein ISO-Datum JJJJ-MM-TT") from exc
    year, week, _ = day.isocalendar()
    return f"VOD_{year}-KW{week:02}"


def arrange_exports(exports: list[dict], job: dict) -> list[dict]:
    """A profile invocation corresponds to one editorial order, with multiple exports."""
    spec = job.get("delivery")
    if not spec:
        return exports
    if not isinstance(spec, dict) or spec.get("version") != 1:
        raise ValueError("delivery benötigt version: 1")
    number = spec.get("order_number")
    if type(number) is not int or not 1 <= number <= 1000:
        raise ValueError("Auftragsnummer muss zwischen 1 und 1000 liegen")
    platform = spec.get("platform")
    if not isinstance(platform, str) or filename_component(platform) != platform:
        raise ValueError("Plattform muss ein gültiger einzelner Ordnername sein")
    field = spec.get("date_field", "date")
    value = job.get("fields", {}).get(field, spec.get("date"))
    week = week_folder(value)
    category = spec.get("category")
    if category is not None and (
        not isinstance(category, str) or filename_component(category) != category
    ):
        raise ValueError("Kategorie muss ein gültiger einzelner Ordnername sein")
    package = (Path(platform) / category / week).as_posix() if category else week
    title = spec.get("title", "")
    if not isinstance(title, str) or len(title) > 2000:
        raise ValueError("Auftragstitel ist ungültig")
    order_id = f"Auftrag-{number:03}"
    short_title = re.sub(r"[^\w-]+", "-", title, flags=re.UNICODE).strip("-_")
    folder = order_id + ("-" + filename_component(short_title, 55) if short_title else "")
    names = [
        filename_component(e.get("artboard_name") or e.get("artboard", "Canvas").rsplit("/", 1)[-1])
        for e in exports
    ]
    suffixes = [Path(e["output"]).suffix.lower() for e in exports]
    counts = Counter((n.casefold(), s) for n, s in zip(names, suffixes))
    result = []
    for entry, name, suffix in zip(exports, names, suffixes):
        original_name = (
            entry.get("artboard_name") or entry.get("artboard", "Canvas").rsplit("/", 1)[-1]
        )
        if counts[name.casefold(), suffix] > 1:
            identity = entry.get("artboard", original_name)
            name += "--" + hashlib.sha256(identity.encode()).hexdigest()[:8]
        relative = (
            Path(package) / folder / (name + suffix)
            if category
            else Path(package) / platform / folder / (name + suffix)
        )
        result.append(
            {
                **entry,
                "output": relative.as_posix(),
                "artboard_name": original_name,
                "delivery": {
                    "version": 1,
                    "week": week,
                    **({"category": category} if category else {}),
                    "package": package,
                    "platform": platform,
                    "order_id": order_id,
                    "folder": folder,
                    "title": title,
                    "date": value,
                    "fields": dict(job["fields"]),
                    "row": spec.get("source_row"),
                    "profile": job["profile"],
                },
            }
        )
    if len({e["output"].casefold() for e in result}) != len(result):
        raise ValueError("Doppelte Artboard-/Format-Ausgabe innerhalb eines Auftrags")
    return result


def report_documents(state: dict, package: str) -> dict[str, dict]:
    """Portable reports contain relative delivery paths, never local host paths or tokens."""
    root = Path(state["output"]) / package
    results = {r["index"]: r for r in state["results"]}
    orders, checks = {}, []
    for index, job in enumerate(state["jobs"]):
        delivery = job.get("delivery", {})
        if delivery.get("package") != package:
            continue
        result = results[index]
        key = (delivery["platform"], delivery["order_id"])
        order = orders.setdefault(
            key,
            {
                "id": delivery["order_id"],
                "platform": delivery["platform"],
                "title": delivery["title"],
                "excel_row": delivery.get("row"),
                "date": delivery["date"],
                "fields": delivery["fields"],
                "template": Path(job["template"]).name,
                "template_sha256": state["input_hashes"].get(job["template"]),
                "exports": [],
            },
        )
        relative = Path(job["output"]).relative_to(root).as_posix()
        published = bool(result.get("published"))
        review = state.get("reviews", {}).get(str(index), {})
        order["exports"].append(
            {
                "artboard": job["artboard_name"],
                "file": relative,
                "format": Path(relative).suffix[1:],
                "size": result.get("size"),
                "sha256": result.get("sha256") if published else None,
                "status": "published" if published else "excluded",
            }
        )
        checks.append(
            {
                "file": relative,
                "decision": "approved" if published else "rejected",
                "reason": review.get("reason", ""),
                "warnings": result.get("warnings", []),
                "changed_box": result.get("changed_box"),
                "size": result.get("size"),
                "approved_sha256": result.get("sha256") if published else None,
            }
        )
    total_published = sum(c["decision"] == "approved" for c in checks)
    common = {
        "version": 1,
        "batch_id": state["id"],
        "package": package,
        "created": state["created"],
        "published_at": state["publication_time"],
        "repackaged_from": state.get("repackaged_from"),
    }
    return {
        "manifest.json": {**common, "orders": list(orders.values())},
        "pruefbericht.json": {
            **common,
            "summary": {
                "total": len(checks),
                "published": total_published,
                "excluded": len(checks) - total_published,
            },
            "approval": "series" if state["series_approved"] else "manual",
            "checks": checks,
        },
    }
