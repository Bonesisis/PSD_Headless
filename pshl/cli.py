"""Kommandozeile für lokale Jobs und den HTTP-Server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .template_renderer import PSDTemplateRenderer


def _texts(value: str) -> dict[str, str]:
    candidate = Path(value)
    try:
        raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise argparse.ArgumentTypeError(
            "--texts erwartet ein JSON-Objekt oder den Pfad zu einer JSON-Datei"
        ) from exc
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in data.items()
    ):
        raise argparse.ArgumentTypeError("--texts muss ausschließlich Textwerte enthalten")
    return data


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--template", required=True, help="Pfad zur PSD-Vorlage")
    parser.add_argument("--config", required=True, help="Pfad zur Template-JSON")
    parser.add_argument("--output", required=True, help="Ausgabewurzel, z. B. NAS-Mount")
    parser.add_argument("--font", help="Pfad zu einer TrueType- oder OpenType-Schrift")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="psd-headless")
    commands = parser.add_subparsers(dest="command", required=True)

    render = commands.add_parser("render", help="Einen Auftrag sofort rendern")
    _add_common_arguments(render)
    render.add_argument(
        "--texts",
        required=True,
        type=_texts,
        help="JSON-Objekt oder JSON-Datei mit den konfigurierten Textfeldern",
    )
    render.add_argument("--job-id", help="Optionaler Ausgabeordner")

    serve = commands.add_parser("serve", help="HTTP-API starten")
    _add_common_arguments(serve)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "render":
        renderer = PSDTemplateRenderer(
            args.template, args.config, args.output, args.font
        )
        result = renderer.render(texts=args.texts, job_id=args.job_id)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(
            create_app(args.template, args.config, args.output, args.font),
            host=args.host,
            port=args.port,
        )
        return 0
    return 2
