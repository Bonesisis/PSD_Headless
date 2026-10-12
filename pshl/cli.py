"""Cross-platform command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from .engine import inspect_psd
from .jobs import parse_jobs, run_batch


def main(argv=None):
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(prog="psd-headless")
    parser.add_argument("--version", action="version", version="1.0.0")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Ebenen und Textformatierung auflisten")
    inspect.add_argument("template")
    for command in ("batch", "render", "serve"):
        sub = commands.add_parser(command)
        sub.add_argument("--templates", default=os.getenv("PSHL_TEMPLATE_DIR", "."))
        sub.add_argument("--output", default=os.getenv("PSHL_OUTPUT_DIR", "out"))
        sub.add_argument("--font", default=os.getenv("PSHL_FONT_PATH"))
        if command != "serve":
            sub.add_argument(
                "--jobs", required=True, help="Job-Datei JSON/YAML/CSV (render: genau ein Job)"
            )
            sub.add_argument("--workers", type=int, default=int(os.getenv("PSHL_WORKERS", "1")))
            sub.add_argument("--report", help="Neuer JSON-Report; Standard im Ausgabeordner")
            sub.add_argument("--dry-run", action="store_true")
        else:
            sub.add_argument("--host", default=os.getenv("PSHL_HOST", "127.0.0.1"))
            sub.add_argument("--port", type=int, default=int(os.getenv("PSHL_PORT", "8000")))
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_psd(args.template)
        elif args.command == "serve":
            import uvicorn

            from .api import create_app

            if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("PSHL_API_KEY"):
                raise ValueError(
                    "Netzwerkbetrieb benötigt PSHL_API_KEY und einen TLS-Reverse-Proxy"
                )
            uvicorn.run(
                create_app(args.templates, args.output, args.font), host=args.host, port=args.port
            )
            return 0
        else:
            file = Path(args.jobs)
            jobs = parse_jobs(file.read_text(encoding="utf-8-sig"), file.suffix)
            if args.command == "render" and len(jobs) != 1:
                raise ValueError("render erwartet genau einen Auftrag; batch für mehrere verwenden")
            report = args.report or str(Path(args.output) / f"report-{uuid4().hex}.json")
            result = run_batch(
                jobs,
                args.templates,
                args.output,
                args.workers,
                args.dry_run,
                args.font,
                report,
                lambda done, total, r: print(f"[{done}/{total}] {r['status']}", file=sys.stderr),
            )
            print(f"Report: {report}", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("errors", 0) else 0
    except (ValueError, OSError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
