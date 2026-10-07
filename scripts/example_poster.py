"""Beispiel-Automation: baut ein Poster komplett headless.

Start (aus dem Projektordner):
    python scripts/example_poster.py
"""
import os
import sys

# Projektwurzel in den Importpfad (damit 'pshl' gefunden wird)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pshl import Document  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
os.makedirs(OUT, exist_ok=True)


def main():
    doc = Document.new(1200, 1600, background="#101418")

    # Ein Farbverlauf-Ersatz: zwei halbtransparente Flächen übereinander blenden
    doc.add_fill("#1e3a5f99", name="Blau").set_blend("screen")
    doc.add_fill("#5f1e4a66", name="Magenta").set_blend("screen")

    # Großer Titel, zentriert
    doc.add_text(
        "HEADLESS\nPHOTOSHOP",
        x=600, y=700, size=140, color="#ffffff", anchor="mm", align="center",
        name="Titel",
    )

    # Untertitel
    doc.add_text(
        "ohne Adobe · reines Python · Linux & Windows",
        x=600, y=950, size=38, color="#9fb4c7", anchor="mm", align="center",
        name="Untertitel",
    )

    path = os.path.join(OUT, "poster.png")
    doc.save(path)
    print(f"Gespeichert: {path}  ({doc})")


if __name__ == "__main__":
    main()
