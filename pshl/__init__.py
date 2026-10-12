"""pshl — PhotoShop HeadLess.

Eine kleine, headless Bild-Engine mit Photoshop-artiger Scripting-API.
Läuft ohne Adobe, plattformübergreifend (Linux/Windows/macOS), reines Python.

Schnellstart:
    from pshl import Document
    doc = Document.new(1920, 1080, background="#202020")
    doc.add_image("assets/logo.png", x=100, y=100).set_blend("screen").opacity_pct(70)
    doc.add_text("Hallo", x=960, y=540, size=160, color="#ffffff", anchor="mm")
    doc.save("out/result.png")
"""

from .blend import BLEND_MODES
from .document import Document, Layer

__all__ = ["Document", "Layer", "BLEND_MODES", "open_psd"]
__version__ = "1.0.0"


def open_psd(path: str) -> Document:
    """Lazy-Import, damit psd-tools nur bei Bedarf geladen wird."""
    from .io_psd import open_psd as _open_psd

    return _open_psd(path)
