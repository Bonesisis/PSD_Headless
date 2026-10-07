"""Text-Layer-Rendering auf ein transparentes RGBA-Bild."""
from __future__ import annotations

import os
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# Kandidaten-Pfade pro Plattform. Erweiterbar — oder man übergibt font= direkt.
_FONT_SEARCH = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    # Linux (DejaVu ist fast überall)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    # Windows
    "C:\\Windows\\Fonts\\arial.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttf",
]


def resolve_font(font: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    """Lädt einen Font. font kann ein Pfad oder None sein (dann Auto-Fallback)."""
    if font and os.path.isfile(font):
        return ImageFont.truetype(font, size)
    if font:
        # Name ohne Pfad: versuchen, Pillow findet manche über den Namen.
        try:
            return ImageFont.truetype(font, size)
        except OSError:
            pass
    for path in _FONT_SEARCH:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    # Letzter Ausweg: Pillows eingebauter Bitmap-Font (ignoriert size).
    return ImageFont.load_default()


def render_text(
    canvas_size: tuple[int, int],
    text: str,
    *,
    x: int = 0,
    y: int = 0,
    font: Optional[str] = None,
    size: int = 48,
    color: str = "#000000",
    anchor: str = "la",
    align: str = "left",
    spacing: int = 4,
) -> Image.Image:
    """Rendert Text auf ein transparentes Bild in Canvas-Größe.

    anchor: Pillow-Anker (z. B. "la" = links/oben, "mm" = zentriert).
    """
    img = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fnt = resolve_font(font, size)
    draw.multiline_text(
        (x, y), text, font=fnt, fill=color, anchor=anchor, align=align, spacing=spacing
    )
    return img
