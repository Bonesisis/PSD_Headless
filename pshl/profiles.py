"""Explicit, reusable mappings from editorial fields to PSD layer selectors."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from string import Formatter

import yaml


def expand_profile(job: dict, root: str, restricted: bool = False) -> list[dict]:
    """Expand a profile invocation into ordinary, independently reported jobs."""
    if not isinstance(job, dict) or "profile" not in job:
        return [job]
    path = (Path(root) / job["profile"]).resolve()
    if restricted and not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Profil muss im Vorlagenordner liegen")
    profile = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise ValueError("Profil benötigt version: 1")
    fields = job.get("fields", {})
    mappings = profile.get("fields", {})
    if not isinstance(fields, dict) or set(fields) != set(mappings):
        raise ValueError(f"Felder müssen exakt dem Profil entsprechen: {sorted(mappings)}")
    base = deepcopy(profile.get("defaults", {}))
    base.update(template=profile["template"], layers={}, styles={}, smart_objects={})
    for name, mapping in mappings.items():
        value = fields[name]
        if not isinstance(value, str):
            raise ValueError(f"Feld {name} muss Text sein")
        style = mapping.get("style", {})
        for selector in mapping.get("layers", []):
            if selector in base["layers"]:
                raise ValueError(f"Doppeltes Profilziel: {selector}")
            base["layers"][selector] = value
            base["styles"][selector] = deepcopy(style)
        if mapping.get("smart_objects"):
            base["smart_objects"][name] = {
                **deepcopy(style),
                "text": value,
                "targets": deepcopy(mapping["smart_objects"]),
            }
        if not mapping.get("layers") and not mapping.get("smart_objects"):
            raise ValueError(f"Feld {name} hat kein Ziel")
    images = job.get("images", {})
    image_mappings = profile.get("images", {})
    if set(images) != set(image_mappings):
        raise ValueError(f"Bildfelder müssen dem Profil entsprechen: {sorted(image_mappings)}")
    base["images"] = {}
    for name, mapping in image_mappings.items():
        for selector in mapping["layers"]:
            if selector in base["images"]:
                raise ValueError(f"Doppeltes Bildziel: {selector}")
            base["images"][selector] = {
                **deepcopy(mapping.get("options", {})),
                "source": images[name],
            }
    exports = profile.get("exports", [])
    if not exports:
        raise ValueError("Profil benötigt mindestens einen Export")
    tokens = {**fields, "name": job.get("name", "result")}
    result = []
    for export in exports:
        pattern = export["output"]
        for _, key, spec, conversion in Formatter().parse(pattern):
            if key is not None and (key not in tokens or spec or conversion):
                raise ValueError("Ausgabenamen erlauben nur einfache {Feldname}-Platzhalter")
        # Fields are labels, never directory selectors.
        used = [key for _, key, _, _ in Formatter().parse(pattern) if key is not None]
        if any(
            any(c in tokens[key] for c in "/\\\0:") or tokens[key] in {".", ".."} for key in used
        ):
            raise ValueError("Ausgabefelder dürfen keine Pfadzeichen enthalten")
        entry = deepcopy(base)
        entry.update(output=pattern.format_map(tokens))
        if export.get("artboard"):
            entry["artboard"] = export["artboard"]
        if Path(entry["output"]).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("Profile exportieren nur PNG/JPEG")
        result.append(entry)
    return result
