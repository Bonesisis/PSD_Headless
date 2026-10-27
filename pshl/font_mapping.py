"""Resolve explicitly requested font names through operator-approved local mappings."""

import hashlib
import json
from pathlib import Path

from .engine import RenderError


def resolve_style(style: dict, root: str) -> dict:
    result = dict(style)
    name = result.pop("font_postscript", None)
    if name is None:
        return result
    if not isinstance(name, str) or not name:
        raise RenderError("font_postscript benötigt einen Schriftnamen")
    root = Path(root).resolve()
    mapping_path = root / "fonts/font-map.json"
    if not mapping_path.resolve().is_relative_to(root):
        raise RenderError("Schriftzuordnung muss im Vorlagenordner liegen")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
    if mapping.get("version") != 1:
        raise RenderError("Schriftzuordnung benötigt version: 1")
    target = mapping.get("approved_aliases", {}).get(name, name)
    entry = mapping.get("exact_postscript_fonts", {}).get(target)
    if not entry:
        raise RenderError(f"Keine freigegebene Schriftzuordnung: {name}")
    path = (root / entry["font"]).resolve()
    if not path.is_relative_to(root):
        raise RenderError("Schriftdatei muss im Vorlagenordner liegen")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != entry["sha256"]:
        raise RenderError("Zugeordnete Schriftdatei wurde verändert")
    for key in ("font", "font_family", "font_style"):
        if key in result:
            raise RenderError(
                "font_postscript darf nicht mit expliziten Schriftangaben kombiniert werden"
            )
        result[key] = str(path) if key == "font" else entry[key]
    return result
