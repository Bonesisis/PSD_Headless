"""PSD-Import: liest eine .psd in ein Document mit erhaltenen Ebenen.

Nutzt psd-tools (reines Lesen). PSD-Schreiben kommt in einer späteren Ausbaustufe.
Blend-Modi aus der PSD werden – soweit unterstützt – auf unsere Namen gemappt.
"""
from __future__ import annotations

from .document import Document

# PSD-Blendmodus-Schlüssel -> unsere Namen (siehe blend.BLEND_MODES)
_PSD_BLEND_MAP = {
    "normal": "normal",
    "multiply": "multiply",
    "screen": "screen",
    "overlay": "overlay",
    "darken": "darken",
    "lighten": "lighten",
    "color dodge": "color_dodge",
    "color burn": "color_burn",
    "hard light": "hard_light",
    "soft light": "soft_light",
    "difference": "difference",
    "exclusion": "exclusion",
    "linear dodge": "linear_dodge",
    "linear burn": "linear_burn",
    "vivid light": "vivid_light",
    "linear light": "linear_light",
    "pin light": "pin_light",
}


def open_psd(path: str) -> Document:
    try:
        from psd_tools import PSDImage
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "psd-tools fehlt. Installieren mit: pip install psd-tools"
        ) from e

    psd = PSDImage.open(path)
    doc = Document.new(psd.width, psd.height)

    for layer in psd:  # unterste zuerst
        if not layer.is_visible():
            continue
        pil = layer.composite()  # rendert die (ggf. verschachtelte) Ebene zu PIL
        if pil is None:
            continue
        x, y = max(0, layer.left), max(0, layer.top)
        ly = doc.add_image_from_pil(pil, name=layer.name, x=x, y=y) \
            if hasattr(doc, "add_image_from_pil") else doc.add_layer(pil, name=layer.name, x=x, y=y)
        mode = str(getattr(layer, "blend_mode", "normal")).split(".")[-1].lower().replace("_", " ")
        ly.blend_mode = _PSD_BLEND_MAP.get(mode, "normal")
        ly.opacity = float(getattr(layer, "opacity", 255)) / 255.0 * 100.0

    return doc
