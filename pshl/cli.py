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
    queue = commands.add_parser(
        "queue", help="Dauerhafte Vorschau-, Freigabe- und Zeitplanwarteschlange"
    )
    queue.add_argument(
        "action", choices=["submit", "list", "status", "approve", "reject", "retry", "work"]
    )
    queue.add_argument("id", nargs="?")
    queue.add_argument("--state", default=os.getenv("PSHL_STATE_DIR", ".pshl-state"))
    queue.add_argument("--jobs")
    queue.add_argument("--templates", default=os.getenv("PSHL_TEMPLATE_DIR", "."))
    queue.add_argument("--output", default=os.getenv("PSHL_OUTPUT_DIR", "out"))
    queue.add_argument("--font", default=os.getenv("PSHL_FONT_PATH"))
    queue.add_argument("--at", help="ISO-Zeit mit Offset, z. B. 2026-12-01T10:00:00+01:00")
    queue.add_argument("--every-days", type=int, default=0)
    queue.add_argument("--date-field", action="append", default=[])
    queue.add_argument(
        "--series",
        action="store_true",
        help="Auch unveränderte Folgeläufe mit Datumsfortschreibung freigeben",
    )
    queue.add_argument("--reason", default="")
    queue.add_argument(
        "--once", action="store_true", help="Nur einen anstehenden Verarbeitungsschritt ausführen"
    )
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
        elif args.command == "queue":
            import time

            from .queue import ReviewQueue

            store = ReviewQueue(args.state)
            if args.action == "submit":
                if not args.jobs:
                    raise ValueError("submit benötigt --jobs")
                file = Path(args.jobs)
                schedule = (
                    {"at": args.at, "every_days": args.every_days, "date_fields": args.date_field}
                    if args.at
                    else None
                )
                if args.every_days and not args.at:
                    raise ValueError("Wiederholung benötigt --at")
                result = store.submit(
                    parse_jobs(file.read_text(encoding="utf-8-sig"), file.suffix),
                    args.templates,
                    args.output,
                    args.font,
                    schedule,
                )
            elif args.action == "list":
                result = {"jobs": store.list()}
            elif args.action == "status":
                result = store.get(args.id)
            elif args.action == "work":
                try:
                    while True:
                        worked = store.tick()
                        if args.once:
                            break
                        if not worked:
                            time.sleep(1)
                except KeyboardInterrupt:
                    pass
                result = {"status": "worker stopped"}
            else:
                result = store.decide(args.id, args.action, args.reason, args.series)
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
        return 1 if result.get("errors", 0) or result.get("status") == "error" else 0
    except (ValueError, OSError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
