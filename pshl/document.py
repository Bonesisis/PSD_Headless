"""Document / Layer — die Photoshop-artige Scripting-Oberfläche.

Beispiel:
    from pshl import Document
    doc = Document.new(1920, 1080, background="#202020")
    logo = doc.add_image("assets/logo.png", x=100, y=100)
    logo.opacity = 70
    logo.blend_mode = "screen"
    doc.add_text("FLYT", x=960, y=540, size=160, color="#ffffff", anchor="mm")
    doc.save("out/result.png")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter

from .blend import composite
from .text import render_text


def _hex_to_rgba(color: str, size: tuple[int, int]) -> Image.Image:
    """Erzeugt eine gefüllte RGBA-Fläche aus '#rrggbb' / '#rrggbbaa' / 'transparent'."""
    if color in (None, "transparent", "none"):
        return Image.new("RGBA", size, (0, 0, 0, 0))
    c = color.lstrip("#")
    if len(c) == 6:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        a = 255
    elif len(c) == 8:
        r, g, b, a = (int(c[i : i + 2], 16) for i in (0, 2, 4, 6))
    else:
        raise ValueError(f"Ungültige Farbe: {color!r}")
    return Image.new("RGBA", size, (r, g, b, a))


def _to_float(img: Image.Image) -> np.ndarray:
    return (np.asarray(img.convert("RGBA"), dtype=np.float32)) / 255.0


def _to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(arr, 0, 1) * 255).round().astype(np.uint8), "RGBA")


@dataclass
class Layer:
    """Eine Ebene. Hält ein RGBA-PIL-Bild plus Photoshop-Attribute."""

    name: str
    image: Image.Image  # immer RGBA, in voller Canvas-Größe positioniert
    _doc: "Document" = field(repr=False, default=None)
    opacity: float = 100.0  # 0..100 wie in Photoshop
    blend_mode: str = "normal"
    visible: bool = True

    # --- Transformationen (verändern das Ebenenbild in Canvas-Größe) ---
    def move(self, dx: int, dy: int) -> "Layer":
        """Verschiebt den Inhalt um (dx, dy) Pixel."""
        self.image = self.image.transform(
            self.image.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy),
            resample=Image.BILINEAR,
        )
        return self

    def opacity_pct(self, value: float) -> "Layer":
        self.opacity = max(0.0, min(100.0, value))
        return self

    def blur(self, radius: float) -> "Layer":
        """Gaußscher Weichzeichner (wie Filter > Weichzeichnungsfilter)."""
        self.image = self.image.filter(ImageFilter.GaussianBlur(radius))
        return self

    def set_blend(self, mode: str) -> "Layer":
        self.blend_mode = mode
        return self


class Document:
    def __init__(self, width: int, height: int, background: Optional[str] = None):
        self.width = width
        self.height = height
        self.layers: list[Layer] = []
        if background not in (None, "transparent", "none"):
            self.add_layer(_hex_to_rgba(background, (width, height)), name="Hintergrund")

    # ---------- Konstruktoren ----------
    @classmethod
    def new(cls, width: int, height: int, background: Optional[str] = None) -> "Document":
        return cls(width, height, background)

    @classmethod
    def open(cls, path: str) -> "Document":
        """Öffnet ein Bild (PNG/JPG/TIFF/…) als Ein-Ebenen-Dokument."""
        img = Image.open(path).convert("RGBA")
        doc = cls(img.width, img.height)
        doc.add_layer(img, name="Ebene 0")
        return doc

    # ---------- Ebenen hinzufügen ----------
    def _fit(self, img: Image.Image, x: int, y: int) -> Image.Image:
        """Legt img an Position (x, y) auf eine transparente Canvas-große Fläche."""
        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        canvas.alpha_composite(img.convert("RGBA"), (x, y))
        return canvas

    def add_layer(self, img: Image.Image, name: str = "Ebene", x: int = 0, y: int = 0) -> Layer:
        placed = self._fit(img, x, y) if img.size != (self.width, self.height) or (x, y) != (0, 0) else img.convert("RGBA")
        layer = Layer(name=name, image=placed, _doc=self)
        self.layers.append(layer)
        return layer

    def add_image(self, path: str, name: Optional[str] = None, x: int = 0, y: int = 0) -> Layer:
        img = Image.open(path).convert("RGBA")
        return self.add_layer(img, name=name or path, x=x, y=y)

    def add_text(self, text: str, *, x: int = 0, y: int = 0, font: Optional[str] = None,
                 size: int = 48, color: str = "#000000", anchor: str = "la",
                 align: str = "left", name: Optional[str] = None) -> Layer:
        img = render_text((self.width, self.height), text, x=x, y=y, font=font,
                          size=size, color=color, anchor=anchor, align=align)
        return self.add_layer(img, name=name or f"Text: {text[:20]}")

    def add_fill(self, color: str, name: str = "Füllung") -> Layer:
        return self.add_layer(_hex_to_rgba(color, (self.width, self.height)), name=name)

    # ---------- Dokument-Operationen ----------
    def resize(self, width: int, height: int, resample=Image.LANCZOS) -> "Document":
        for ly in self.layers:
            ly.image = ly.image.resize((width, height), resample)
        self.width, self.height = width, height
        return self

    def flatten(self) -> Image.Image:
        """Rechnet alle sichtbaren Ebenen mit Blend-Modus & Deckkraft zusammen."""
        base = np.zeros((self.height, self.width, 4), dtype=np.float32)
        for ly in self.layers:
            if not ly.visible or ly.opacity <= 0:
                continue
            src = _to_float(ly.image)
            base = composite(base, src, ly.blend_mode, ly.opacity / 100.0)
        return _to_pil(base)

    def save(self, path: str, **kwargs) -> str:
        """Flacht ab und speichert. Format wird aus der Endung abgeleitet."""
        out = self.flatten()
        if path.lower().endswith((".jpg", ".jpeg")):
            out = out.convert("RGB")  # JPEG kennt keinen Alpha
            kwargs.setdefault("quality", 95)
        out.save(path, **kwargs)
        return path

    def __repr__(self) -> str:
        return f"<Document {self.width}x{self.height}, {len(self.layers)} Ebenen>"
