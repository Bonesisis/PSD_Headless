"""Konfigurierbarer Headless-Renderer für Text-Platzhalter in PSD-Vorlagen.

Die PSD bleibt unverändert. Sichtbare Ebenen, deren Namen in einer
Template-Konfiguration stehen, werden beim Compositing ausgelassen und
anschließend mit Pillow neu gezeichnet. Benannte Artboards werden als einzelne
PNG-Dateien exportiert.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageColor, ImageDraw, ImageFont
from psd_tools import PSDImage


class TemplateRenderError(RuntimeError):
    """Verständlicher Fehler bei einer ungültigen Vorlage oder Konfiguration."""


@dataclass(frozen=True)
class TextStyle:
    color: str
    max_lines: int


@dataclass(frozen=True)
class TextField:
    layer_name: str
    color: str
    max_lines: int = 2
    uppercase: bool = False
    max_length: int = 400

    @property
    def style(self) -> TextStyle:
        return TextStyle(color=self.color, max_lines=self.max_lines)


@dataclass(frozen=True)
class TemplateDefinition:
    text_fields: dict[str, TextField]
    artboards: dict[str, str]

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "TemplateDefinition":
        config_path = Path(path).expanduser().resolve()
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TemplateRenderError(
                f"Template-Konfiguration nicht lesbar: {config_path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise TemplateRenderError(
                f"Ungültiges JSON in {config_path}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise TemplateRenderError("Template-Konfiguration muss ein JSON-Objekt sein")

        raw_fields = data.get("text_fields")
        raw_artboards = data.get("artboards")
        if not isinstance(raw_fields, dict) or not raw_fields:
            raise TemplateRenderError("text_fields muss ein nicht-leeres Objekt sein")
        if not isinstance(raw_artboards, dict) or not raw_artboards:
            raise TemplateRenderError("artboards muss ein nicht-leeres Objekt sein")

        text_fields: dict[str, TextField] = {}
        layer_names: set[str] = set()
        for field_name, raw in raw_fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                raise TemplateRenderError("Textfeld-Namen müssen nicht-leere Strings sein")
            if not isinstance(raw, dict):
                raise TemplateRenderError(f"Textfeld {field_name!r} muss ein Objekt sein")

            layer_name = raw.get("layer_name")
            color = raw.get("color", "#ffffff")
            max_lines = raw.get("max_lines", 2)
            uppercase = raw.get("uppercase", False)
            max_length = raw.get("max_length", 400)
            if not isinstance(layer_name, str) or not layer_name.strip():
                raise TemplateRenderError(
                    f"Textfeld {field_name!r}: layer_name fehlt"
                )
            if layer_name in layer_names:
                raise TemplateRenderError(
                    f"Ebenenname {layer_name!r} ist mehrfach konfiguriert"
                )
            try:
                ImageColor.getrgb(color)
            except (TypeError, ValueError) as exc:
                raise TemplateRenderError(
                    f"Textfeld {field_name!r}: ungültige Farbe {color!r}"
                ) from exc
            if not isinstance(max_lines, int) or not 1 <= max_lines <= 20:
                raise TemplateRenderError(
                    f"Textfeld {field_name!r}: max_lines muss zwischen 1 und 20 liegen"
                )
            if not isinstance(uppercase, bool):
                raise TemplateRenderError(
                    f"Textfeld {field_name!r}: uppercase muss true oder false sein"
                )
            if not isinstance(max_length, int) or not 1 <= max_length <= 5000:
                raise TemplateRenderError(
                    f"Textfeld {field_name!r}: max_length muss zwischen 1 und 5000 liegen"
                )

            text_fields[field_name] = TextField(
                layer_name=layer_name,
                color=color,
                max_lines=max_lines,
                uppercase=uppercase,
                max_length=max_length,
            )
            layer_names.add(layer_name)

        artboards: dict[str, str] = {}
        output_names: set[str] = set()
        for artboard_name, filename in raw_artboards.items():
            if not isinstance(artboard_name, str) or not artboard_name.strip():
                raise TemplateRenderError("Artboard-Namen müssen nicht-leere Strings sein")
            if (
                not isinstance(filename, str)
                or not filename
                or Path(filename).name != filename
                or "/" in filename
                or "\\" in filename
            ):
                raise TemplateRenderError(
                    f"Ausgabedatei für {artboard_name!r} muss nur ein Dateiname sein"
                )
            if not filename.lower().endswith(".png"):
                raise TemplateRenderError(
                    f"Ausgabedatei für {artboard_name!r} muss auf .png enden"
                )
            if filename in output_names:
                raise TemplateRenderError(f"Ausgabedatei {filename!r} ist mehrfach vergeben")
            artboards[artboard_name] = filename
            output_names.add(filename)

        return cls(text_fields=text_fields, artboards=artboards)


@dataclass(frozen=True)
class RenderResult:
    job_id: str
    output_directory: Path
    files: dict[str, Path]
    elapsed_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "output_directory": str(self.output_directory),
            "files": {name: str(path) for name, path in self.files.items()},
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def _font_candidates() -> tuple[Path, ...]:
    return (
        Path.home() / "Library/Fonts/Arial Bold Italic.ttf",
        Path("/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
        Path("C:/Windows/Fonts/arialbi.ttf"),
    )


def safe_job_id(value: str | None) -> str:
    """Erzeugt einen NAS-tauglichen Ordnernamen ohne Pfadanteile."""
    if not value:
        return uuid.uuid4().hex
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_value).strip(".-_")
    if not cleaned:
        raise ValueError("job_id enthält keine zulässigen Zeichen")
    return cleaned[:80]


def resolve_font_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Findet eine plattformübliche Schrift oder verwendet den expliziten Pfad."""
    candidates: Iterable[str | os.PathLike[str]] = (
        (explicit,) if explicit else _font_candidates()
    )
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    if explicit:
        raise TemplateRenderError(f"Schriftdatei nicht gefunden: {explicit}")
    raise TemplateRenderError(
        "Keine Standardschrift gefunden. Einen Pfad mit --font oder "
        "PSHL_FONT_PATH angeben."
    )


def _walk(group):
    for layer in group:
        yield layer
        if getattr(layer, "kind", None) in {"group", "artboard"}:
            yield from _walk(layer)


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\r", "\n").split()).strip()


def _wrap_for_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
) -> list[str] | None:
    words = _normalize_text(text).split(" ")
    if not words:
        return None
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font, anchor="lt")
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
            continue
        if not current:
            return None
        lines.append(current)
        current = word
        if len(lines) >= max_lines:
            return None
    if current:
        lines.append(current)
    return lines if len(lines) <= max_lines else None


def _fitted_text_image(
    text: str,
    size: tuple[int, int],
    font_path: Path,
    style: TextStyle,
    *,
    supersample: int = 4,
) -> Image.Image:
    """Rendert Text maximal groß in eine Box, mit automatischem Umbruch."""
    width, height = size
    if width <= 0 or height <= 0:
        raise TemplateRenderError(f"Ungültige Textbox: {size}")

    scaled_size = (width * supersample, height * supersample)
    canvas = Image.new("RGBA", scaled_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    selected: tuple[ImageFont.FreeTypeFont, list[str], int] | None = None
    max_font_size = max(4, height * supersample)

    for font_size in range(max_font_size, 3, -1):
        font = ImageFont.truetype(str(font_path), font_size)
        lines = _wrap_for_font(
            draw, text, font, width * supersample, style.max_lines
        )
        if not lines:
            continue
        spacing = max(0, round(font_size * 0.02))
        block = "\n".join(lines)
        bbox = draw.multiline_textbbox((0, 0), block, font=font, spacing=spacing)
        if bbox[2] - bbox[0] <= scaled_size[0] and bbox[3] - bbox[1] <= scaled_size[1]:
            selected = font, lines, spacing
            break

    if selected is None:
        raise TemplateRenderError("Text passt selbst in minimaler Schriftgröße nicht")

    font, lines, spacing = selected
    block = "\n".join(lines)
    bbox = draw.multiline_textbbox((0, 0), block, font=font, spacing=spacing)
    draw.multiline_text(
        (-bbox[0], -bbox[1]),
        block,
        font=font,
        fill=style.color,
        spacing=spacing,
        align="left",
    )
    return canvas.resize((width, height), Image.Resampling.LANCZOS)


def _atomic_save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    image.save(temp, format="PNG", optimize=True)
    temp.replace(path)


class PSDTemplateRenderer:
    """Rendert eine per JSON definierte PSD-Vorlage ohne Photoshop."""

    def __init__(
        self,
        template_path: str | os.PathLike[str],
        config_path: str | os.PathLike[str],
        output_root: str | os.PathLike[str],
        font_path: str | os.PathLike[str] | None = None,
    ):
        self.template_path = Path(template_path).expanduser().resolve()
        self.config_path = Path(config_path).expanduser().resolve()
        self.output_root = Path(output_root).expanduser().resolve()
        self.font_path = resolve_font_path(font_path)
        self.definition = TemplateDefinition.from_file(self.config_path)
        if not self.template_path.is_file():
            raise TemplateRenderError(f"PSD-Vorlage nicht gefunden: {self.template_path}")

    def render(
        self,
        *,
        texts: dict[str, str],
        job_id: str | None = None,
    ) -> RenderResult:
        started = time.perf_counter()
        expected = set(self.definition.text_fields)
        supplied = set(texts)
        if supplied != expected:
            missing = expected - supplied
            unknown = supplied - expected
            details = []
            if missing:
                details.append("fehlt: " + ", ".join(sorted(missing)))
            if unknown:
                details.append("unbekannt: " + ", ".join(sorted(unknown)))
            raise ValueError(
                "Textfelder stimmen nicht mit der Konfiguration überein ("
                + "; ".join(details)
                + ")"
            )

        prepared: dict[str, str] = {}
        for field_name, field in self.definition.text_fields.items():
            value = _normalize_text(texts[field_name])
            if not value:
                raise ValueError(f"Textfeld {field_name!r} darf nicht leer sein")
            if len(value) > field.max_length:
                raise ValueError(
                    f"Textfeld {field_name!r} ist länger als {field.max_length} Zeichen"
                )
            prepared[field_name] = value.upper() if field.uppercase else value

        resolved_job_id = safe_job_id(job_id)
        output_dir = self.output_root / resolved_job_id
        psd = PSDImage.open(self.template_path)

        by_layer_name = {
            field.layer_name: (field_name, field)
            for field_name, field in self.definition.text_fields.items()
        }

        def is_target(layer) -> bool:
            return (
                layer.is_visible()
                and getattr(layer, "kind", None) not in {"group", "artboard"}
                and layer.name in by_layer_name
            )

        targets = [layer for layer in _walk(psd) if is_target(layer)]
        found_fields = {by_layer_name[layer.name][0] for layer in targets}
        missing = expected - found_fields
        if missing:
            raise TemplateRenderError(
                "Sichtbare Text-Platzhalter fehlen: " + ", ".join(sorted(missing))
            )

        def layer_filter(layer) -> bool:
            return layer.is_visible() and not is_target(layer)

        try:
            composed = psd.composite(layer_filter=layer_filter).convert("RGBA")
        except ImportError as exc:
            raise TemplateRenderError(
                "PSD-Compositing benötigt psd-tools[composite] und aggdraw"
            ) from exc

        for layer in targets:
            field_name, field = by_layer_name[layer.name]
            left, top, right, bottom = map(int, layer.bbox)
            overlay = _fitted_text_image(
                prepared[field_name],
                (right - left, bottom - top),
                self.font_path,
                field.style,
            )
            composed.alpha_composite(overlay, (left, top))

        top_level = {layer.name: layer for layer in psd if layer.kind == "artboard"}
        files: dict[str, Path] = {}
        for artboard_name, filename in self.definition.artboards.items():
            layer = top_level.get(artboard_name)
            if layer is None:
                raise TemplateRenderError(f"Artboard fehlt: {artboard_name}")
            crop = composed.crop(tuple(map(int, layer.bbox)))
            destination = output_dir / filename
            _atomic_save_png(crop, destination)
            files[artboard_name] = destination

        result = RenderResult(
            job_id=resolved_job_id,
            output_directory=output_dir,
            files=files,
            elapsed_seconds=time.perf_counter() - started,
        )
        manifest = output_dir / "manifest.json"
        temp_manifest = output_dir / f".manifest.{uuid.uuid4().hex}.tmp"
        temp_manifest.write_text(
            json.dumps(
                {
                    **result.as_dict(),
                    "texts": prepared,
                    "template": self.template_path.name,
                    "configuration": self.config_path.name,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temp_manifest.replace(manifest)
        files["manifest"] = manifest
        return result
