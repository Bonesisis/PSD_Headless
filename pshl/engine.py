"""Strikter PSD-Renderer. Quelldateien bleiben unverändert."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
from psd_tools.constants import ColorMode, Resource, TextType


class RenderError(ValueError):
    """Ein Auftrag kann nicht verlässlich ausgeführt werden."""


def walk(group, prefix=""):
    for layer in group:
        path = f"{prefix}/{layer.name}" if prefix else layer.name
        yield path, layer
        if layer.is_group():
            yield from walk(layer, path)


def font_file(name, explicit=None, fonts=None):
    """Keine Ersatzschrift: PostScript-Name oder explizite Datei erforderlich."""
    from fontTools.ttLib import TTFont

    chosen = explicit or (fonts or {}).get(name)
    if chosen:
        path = Path(chosen).expanduser()
        if not path.is_file():
            raise RenderError(f"Schriftdatei fehlt: {path}")
        ImageFont.truetype(str(path), 12)
        return path
    if not name:
        raise RenderError("Keine Schrift definiert. font oder fonts-Zuordnung angeben.")
    directories = [
        Path.home() / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
        Path("/usr/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts",
    ]
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            try:
                with TTFont(path, lazy=True) as font:
                    names = [n.toUnicode() for n in font["name"].names if n.nameID == 6]
                    if name in names:
                        return path
            except Exception:
                continue  # Defekte, nicht verwendete Systemfonts ignorieren.
    raise RenderError(f"Schrift fehlt: {name}. Eine exakte fonts-Zuordnung angeben.")


def metadata(psd):
    icc = psd.image_resources.get_data(Resource.ICC_PROFILE)
    resolution = psd.image_resources.get_data(Resource.RESOLUTION_INFO)
    dpi = (72.0, 72.0)
    if resolution:
        dpi = (resolution.horizontal / 65536, resolution.vertical / 65536)
    return {"icc_profile": icc, "dpi": dpi}


def text_style(layer):
    """Unterstützte einheitliche Schriftstile; komplexe Stile strikt ablehnen."""
    ts = layer.typesetting
    runs = [r for r in ts.runs if r.text.strip()]
    if not runs:
        raise RenderError("Textebene besitzt keine lesbaren Formatdaten")
    first = runs[0].style
    for run in runs:
        s = run.style
        if (
            s.stroke_flag
            or not s.fill_flag
            or s.underline
            or s.strikethrough
            or s.font_caps
            or s.font_baseline
            or s.kerning
            or s.no_break
            or s.tsume
            or s.tracking
            or s.baseline_shift
            or s.faux_bold
            or s.faux_italic
            or s.horizontal_scale != 1
            or s.vertical_scale != 1
        ):
            raise RenderError(
                "Zeicheneffekte, Sonderformatierung oder manuelles Kerning nicht unterstützt"
            )
    signatures = {
        (
            r.style.font_name,
            r.style.font_size,
            r.style.fill_color,
            r.style.leading,
            r.style.auto_leading,
        )
        for r in runs
    }
    if len(signatures) != 1:
        raise RenderError("Gemischte Textformatierung wird nicht unterstützt")
    if first.tracking or first.baseline_shift or first.faux_bold or first.faux_italic:
        raise RenderError(
            "Tracking, Grundlinienversatz oder künstlicher Schriftschnitt nicht unterstützt"
        )
    if first.horizontal_scale != 1 or first.vertical_scale != 1:
        raise RenderError("Nichtproportionale Zeichenskalierung nicht unterstützt")
    warp_style = layer._data.warp.get(b"warpStyle")
    if warp_style is not None and getattr(warp_style, "enum", None) != b"warpNone":
        raise RenderError("Textverformung (Warp) wird nicht unterstützt")
    if layer.typesetting.writing_direction not in (None, 0):
        raise RenderError("Vertikale Schreibrichtung wird nicht unterstützt")
    xx, xy, yx, yy, _, _ = layer.transform
    if abs(xy) > 1e-6 or abs(yx) > 1e-6 or xx <= 0 or abs(xx - yy) > 1e-5:
        raise RenderError("Gedrehte, gespiegelte oder verzerrte Textebene nicht unterstützt")
    paragraphs = list(ts)
    for p in paragraphs:
        if any(
            getattr(p.style, key)
            for key in (
                "first_line_indent",
                "start_indent",
                "end_indent",
                "space_before",
                "space_after",
            )
        ):
            raise RenderError("Absatzeinzüge und Absatzabstände nicht unterstützt")
    aligns = {p.style.justification.name.lower() for p in paragraphs}
    if len(aligns) > 1 or not aligns.issubset({"left", "right", "center"}):
        raise RenderError("Gemischte Ausrichtung oder Blocksatz nicht unterstützt")
    argb = first.fill_color
    if not argb or len(argb) != 4:
        raise RenderError("Nur RGB-Textfarben werden unterstützt")
    color = tuple(round(c * 255) for c in (*argb[1:], argb[0]))
    return {
        "font_name": first.font_name,
        "size": first.font_size * xx,
        "color": color,
        "align": next(iter(aligns), "left"),
        "spacing": max(0, (first.leading - first.font_size) * xx)
        if not first.auto_leading
        else first.font_size * xx * 0.2,
    }


def inspect_psd(path):
    psd = PSDImage.open(path)
    rows = []
    for address, layer in walk(psd):
        row = {
            "path": address,
            "name": layer.name,
            "kind": layer.kind,
            "visible": layer.is_visible(),
            "box": list(layer.bbox),
        }
        if layer.kind == "type":
            row["text"] = layer.text
            try:
                row["style"] = text_style(layer)
            except RenderError as exc:
                row["unsupported"] = str(exc)
        if layer.kind == "smartobject":
            # Eingebettete PSB-Inhalte nur lesen. Niemals externe Links öffnen.
            row["embedded_texts"] = []
            so = layer.smart_object
            if so.kind == "data" and so.is_psd():
                with so.open() as source:
                    nested = PSDImage.open(source)
                row["embedded_texts"] = [
                    {"path": p, "text": item.text}
                    for p, item in walk(nested)
                    if item.kind == "type"
                ]
        rows.append(row)
    info = metadata(psd)
    return {
        "width": psd.width,
        "height": psd.height,
        "depth": psd.depth,
        "color_mode": psd.color_mode.name,
        "dpi": info["dpi"],
        "has_icc_profile": bool(info["icc_profile"]),
        "layers": rows,
    }


def layout_text(text, box, style, shrink=False):
    width, height = box
    if width <= 0 or height <= 0:
        raise RenderError("Leere Textbox")
    if not isinstance(text, str):
        raise RenderError("Text muss eine Zeichenfolge sein")
    if len(text) > 10000:
        raise RenderError("Text überschreitet 10000 Zeichen")
    canvas = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(canvas)
    initial = max(1, round(float(style["size"])))
    minimum = max(1, int(style.get("min_size", 6)))
    for size in range(initial, min(initial, minimum) - 1, -1):
        font = ImageFont.truetype(str(style["font"]), size)
        lines = []
        spacing = round(float(style.get("spacing", 0)) * size / initial)
        for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = ""
            for word in paragraph.split(" "):
                proposed = (line + " " + word) if line else word
                bounds = draw.textbbox((0, 0), proposed, font=font)
                if bounds[2] - bounds[0] > width and line:
                    lines.append(line)
                    line = word
                else:
                    line = proposed
            lines.append(line)
        block = "\n".join(lines)
        bounds = draw.multiline_textbbox(
            (0, 0), block, font=font, spacing=spacing, align=style.get("align", "left")
        )
        bw, bh = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if bw <= width and bh <= height:
            align = style.get("align", "left")
            if align not in {"left", "center", "right"}:
                raise RenderError("align muss left, center oder right sein")
            x = {"left": 0, "center": (width - bw) / 2, "right": width - bw}[align]
            draw.multiline_text(
                (x - bounds[0], -bounds[1]),
                block,
                font=font,
                fill=style["color"],
                spacing=spacing,
                align=align,
            )
            return canvas, size
        if not shrink:
            break
    raise RenderError("Text passt nicht in die Box; shrink aktivieren oder Text kürzen")


def prepare(psd, layers, styles, font=None, fonts=None, shrink=False):
    if psd.color_mode != ColorMode.RGB or psd.depth != 8:
        raise RenderError("Nur RGB-Vorlagen mit 8 Bit werden verlustfrei unterstützt")
    if not layers or not isinstance(layers, dict):
        raise RenderError("layers muss ein nicht-leeres Objekt sein")
    all_layers = list(walk(psd))
    prepared, warnings, used = [], [], set()
    for selector, value in layers.items():
        matches = [(p, ly) for p, ly in all_layers if p == selector or ly.name == selector]
        if len(matches) != 1:
            raise RenderError(
                f"Ebene {selector!r}: {len(matches)} Treffer; eindeutigen Gruppenpfad verwenden"
            )
        address, layer = matches[0]
        if id(layer) in used:
            raise RenderError(f"Ebene mehrfach adressiert: {address}")
        used.add(id(layer))
        if not layer.is_visible() or layer.is_group():
            raise RenderError(f"Ebene ist ausgeblendet oder eine Gruppe: {address}")
        if layer.has_mask() or layer.has_vector_mask() or layer.has_effects() or layer.clipping:
            raise RenderError(
                f"Maske, Effekt oder Clipping am Textziel nicht unterstützt: {address}"
            )
        override = dict(styles.get(selector, {}))
        if layer.kind == "type":
            style = text_style(layer)
            # Paragraph-Textbox statt Glyphen-Bounds erfordert explizite Box.
            if layer.text_type != TextType.POINT and "box" not in override:
                raise RenderError(f"Absatztext benötigt eine explizite styles.box: {address}")
        else:
            style = {}
            if not {"size", "color", "box"}.issubset(override):
                raise RenderError(
                    f"Pixel-/Smart-Object-Platzhalter benötigt size, color, box: {address}"
                )
            warnings.append(f"{address}: Platzhalter wird mit explizitem Stil rasterisiert")
        style.update(override)
        style["font"] = font_file(style.get("font_name"), style.get("font") or font, fonts)
        from fontTools.ttLib import TTFont

        with TTFont(style["font"], lazy=True) as selected_font:
            cmap = selected_font.getBestCmap() or {}
            missing = sorted({c for c in value if not c.isspace() and ord(c) not in cmap})
            if missing:
                raise RenderError(f"Schrift enthält diese Zeichen nicht: {''.join(missing)}")
        color = style.get("color")
        style["color"] = (
            ImageColor.getcolor(color, "RGBA") if isinstance(color, str) else tuple(color)
        )
        box = style.get("box", layer.bbox)
        if len(box) != 4 or not all(isinstance(x, int) for x in box):
            raise RenderError("box muss vier ganzzahlige Canvas-Koordinaten enthalten")
        left, top, right, bottom = box
        if not (0 <= left < right <= psd.width and 0 <= top < bottom <= psd.height):
            raise RenderError("Textbox liegt außerhalb der Vorlage")
        image, size = layout_text(value, (right - left, bottom - top), style, shrink)
        if size < round(style["size"]):
            warnings.append(f"{address}: Schrift von {style['size']} auf {size} px verkleinert")
        if layer.kind == "type":
            warnings.append(f"{address}: Pillow-Typografie kann von Photoshop abweichen")
        prepared.append((layer, image, left, top))
    return prepared, warnings


def render_job(job, dry_run=False):
    source, target = Path(job["template"]), Path(job["output"])
    if source.resolve() == target.resolve():
        raise RenderError("Die Quelldatei darf nicht überschrieben werden")
    fmt = job.get("format", target.suffix.lstrip(".")).lower()
    if fmt not in {"png", "jpg", "jpeg", "psd"}:
        raise RenderError("Ausgabeformat muss png, jpg oder psd sein")
    if target.exists():
        raise RenderError(f"Ausgabe existiert bereits: {target}")
    psd = PSDImage.open(source)
    info = metadata(psd)
    prepared, warnings = prepare(
        psd,
        job["layers"],
        job.get("styles", {}),
        job.get("font"),
        job.get("fonts"),
        job.get("shrink", False),
    )
    if fmt == "psd":
        warnings.append("PSD-Ausgabe: ersetzte Texte sind Pixelebenen, keine editierbaren Texte")
    if dry_run:
        return {"status": "checked", "output": str(target), "warnings": warnings}
    for layer, image, left, top in prepared:
        parent = layer.parent
        index = parent.index(layer)
        replacement = PixelLayer.frompil(image, parent, name=layer.name, left=left, top=top)
        replacement.opacity = layer.opacity
        replacement.blend_mode = layer.blend_mode
        parent.remove(replacement)
        parent[index] = replacement
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".pshl-", suffix="." + fmt, dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        if fmt == "psd":
            psd.save(temp)
        else:
            # apply_icc=False: Original-RGB-Werte behalten, Originalprofil anhängen.
            image = psd.composite(apply_icc=False).convert("RGBA")
            options = {"dpi": info["dpi"]}
            if info["icc_profile"]:
                options["icc_profile"] = info["icc_profile"]
            if fmt in {"jpg", "jpeg"}:
                background = Image.new("RGB", image.size, job.get("background", "#ffffff"))
                background.paste(image, mask=image.getchannel("A"))
                image = background
                options["quality"] = 95
            image.save(temp, format="JPEG" if fmt in {"jpg", "jpeg"} else "PNG", **options)
        # Reservierung verhindert versehentliches Überschreiben auch durch konkurrierende Jobs.
        reserve = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(reserve)
        try:
            os.replace(temp, target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    finally:
        temp.unlink(missing_ok=True)
    return {"status": "success", "output": str(target), "warnings": warnings}
