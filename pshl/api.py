"""Local dashboard and authenticated asynchronous batch API."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import controller, planners
from .jobs import parse_jobs, run_batch
from .queue import ReviewQueue


class BatchRequest(BaseModel):
    jobs: list | None = None
    content: str | None = None
    extension: str = ".json"
    templates: str | None = None
    output: str | None = None
    font: str | None = None
    workers: int = Field(default=1, ge=1, le=32)
    dry_run: bool = False
    schedule: dict | None = None


def create_app(template_root=None, output_root=None, font=None, state_root=None):
    template_root = str(Path(template_root or os.getenv("PSHL_TEMPLATE_DIR", ".")).resolve())
    output_root = str(Path(output_root or os.getenv("PSHL_OUTPUT_DIR", "out")).resolve())
    api_key = os.getenv("PSHL_API_KEY", "")
    token = api_key or secrets.token_urlsafe(32)
    executor = ThreadPoolExecutor(max_workers=1)
    lock, states = threading.Lock(), {}
    max_bytes = 5 * 1024 * 1024
    queue = ReviewQueue(state_root or os.getenv("PSHL_STATE_DIR", ".pshl-state"))
    drafts = queue.root / "imports"
    drafts.mkdir(exist_ok=True)
    stop = asyncio.Event()

    async def queue_worker():
        while not stop.is_set():
            try:
                worked = await asyncio.to_thread(queue.tick)
            except Exception:
                # Keep the service alive on transient disk errors; persisted jobs remain retryable.
                import logging

                logging.getLogger(__name__).exception("Queue worker failed")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass

    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(queue_worker())
        yield
        stop.set()
        await worker
        await asyncio.to_thread(executor.shutdown, wait=True)

    app = FastAPI(title="PSD Headless", version="1.0.0", lifespan=lifespan)
    allowed = ["localhost", "127.0.0.1", "[::1]", "testserver"]
    allowed += [h.strip() for h in os.getenv("PSHL_ALLOWED_HOSTS", "").split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)

    @app.middleware("http")
    async def protect(request, call_next):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin requests are disabled"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        )
        return response

    def authenticate(request: Request):
        if not secrets.compare_digest(request.headers.get("X-PSHL-Token", ""), token):
            raise HTTPException(401, "X-PSHL-Token fehlt oder ist ungültig")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.get("/api/config")
    def config():
        return {
            "templates": template_root,
            "output": output_root,
            "token": None if api_key else token,
            "locked_roots": bool(api_key),
            "planner": planners.settings(),
        }

    async def read_payload(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise HTTPException(413, "Maximal 5 MiB")
        return raw

    async def read_object(request: Request) -> dict:
        data = json.loads(await read_payload(request))
        if not isinstance(data, dict):
            raise ValueError("JSON-Anfrage muss ein Objekt sein")
        return data

    def load_draft(identifier: str):
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Ungültige Import-ID")
        return json.loads((drafts / (identifier + ".json")).read_text(encoding="utf-8"))

    @app.get("/api/controller/catalog", dependencies=[Depends(authenticate)])
    def profile_catalog():
        try:
            return controller.catalog(template_root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/controller/import", dependencies=[Depends(authenticate)])
    async def import_excel(request: Request):
        try:
            raw = bytes(await read_payload(request))
            table = await asyncio.to_thread(controller.read_excel, raw)
            profiles = controller.catalog(template_root)
            proposed = controller.rule_plan(table, profiles)
            plan = {"assignments": proposed["assignments"]}
            jobs = (
                controller.compile_plan(plan, table, profiles, template_root)
                if not proposed["errors"]
                else []
            )
            identifier = uuid4().hex
            with (drafts / (identifier + ".json")).open("x", encoding="utf-8") as stream:
                json.dump(table, stream, ensure_ascii=False)
            return {
                "id": identifier,
                "table": table,
                "plan": plan,
                "jobs": jobs,
                "delivery": controller.delivery_preview(jobs, template_root),
                "export_count": sum(
                    profiles[a["profile_id"]]["exports"] for a in plan["assignments"]
                ),
                "errors": proposed["errors"],
                "planner_input": controller.planner_input(table, profiles),
            }
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/controller/{identifier}/plan", dependencies=[Depends(authenticate)])
    async def plan_excel(identifier: str, request: Request):
        try:
            data = await read_object(request)
            table, profiles = load_draft(identifier), controller.catalog(template_root)
            if data.get("mode") == "model":
                plan = await asyncio.to_thread(
                    planners.propose, controller.planner_input(table, profiles)
                )
            elif "plan" in data:
                plan = data["plan"]
            else:
                raise ValueError("mode: model oder plan erforderlich")
            jobs = controller.compile_plan(plan, table, profiles, template_root)
            return {
                "plan": plan,
                "jobs": jobs,
                "delivery": controller.delivery_preview(jobs, template_root),
            }
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post(
        "/api/controller/{identifier}/submit", dependencies=[Depends(authenticate)], status_code=202
    )
    async def submit_excel(identifier: str, request: Request):
        try:
            data = await read_object(request)
            jobs = controller.compile_plan(
                data["plan"],
                load_draft(identifier),
                controller.catalog(template_root),
                template_root,
            )
            state = await asyncio.to_thread(
                queue.submit, jobs, template_root, output_root, font, data.get("schedule")
            )
            return {
                "id": state["id"],
                "status": state["status"],
                "duplicate": state.get("duplicate", False),
                "packages": state.get("packages", []),
            }
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/queue/{identifier}/items/{index}/review", dependencies=[Depends(authenticate)])
    async def review_image(identifier: str, index: int, request: Request):
        try:
            data = await read_object(request)
            return await asyncio.to_thread(
                queue.review_item, identifier, index, data["decision"], data.get("reason", "")
            )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/queue", dependencies=[Depends(authenticate)], status_code=202)
    async def enqueue(request: Request):
        try:
            data = BatchRequest.model_validate_json(await read_payload(request))
            templates = str(Path(data.templates or template_root).expanduser().resolve())
            output = str(Path(data.output or output_root).expanduser().resolve())
            if api_key and (templates != template_root or output != output_root):
                raise ValueError("Ordner im Netzwerkmodus können nur am Server geändert werden")
            jobs = (
                data.jobs
                if data.jobs is not None
                else parse_jobs(data.content or "", data.extension)
            )
            state = await asyncio.to_thread(
                queue.submit, jobs, templates, output, data.font or font, data.schedule
            )
            return {
                "id": state["id"],
                "status": state["status"],
                "duplicate": state.get("duplicate", False),
            }
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/queue", dependencies=[Depends(authenticate)])
    def queue_list():
        return [
            {k: s.get(k) for k in ("id", "status", "created", "done", "total", "schedule", "error")}
            for s in queue.list()
        ]

    @app.get("/api/queue/{identifier}", dependencies=[Depends(authenticate)])
    def queue_status(identifier: str):
        try:
            return queue.get(identifier)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/queue/{identifier}/{action}", dependencies=[Depends(authenticate)])
    async def queue_decide(identifier: str, action: str, request: Request):
        import json

        try:
            data = json.loads(await read_payload(request))
            return await asyncio.to_thread(
                queue.decide, identifier, action, data.get("reason", ""), data.get("series", False)
            )
        except (ValueError, OSError, TypeError, AttributeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/queue/{identifier}/images/{index}/{side}", dependencies=[Depends(authenticate)])
    def queue_image(identifier: str, index: int, side: str):
        try:
            if side not in {"before", "preview"} or index < 0:
                raise ValueError("Ungültige Vorschau")
            state = queue.get(identifier)
            item = next((r for r in state["results"] if r["index"] == index), {})
            path = Path(item[side]).resolve()
            if not path.is_relative_to(queue.root):
                raise ValueError("Ungültiger Vorschaupfad")
            return FileResponse(path)
        except (ValueError, IndexError, KeyError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/batches", dependencies=[Depends(authenticate)], status_code=202)
    async def start(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise HTTPException(413, "Maximal 5 MiB; große Batches über CLI ausführen")
        try:
            data = BatchRequest.model_validate_json(raw)
            jobs = (
                data.jobs
                if data.jobs is not None
                else parse_jobs(data.content or "", data.extension)
            )
            if not jobs:
                raise ValueError("Batch ist leer")
            templates = str(Path(data.templates or template_root).expanduser().resolve())
            output = str(Path(data.output or output_root).expanduser().resolve())
            if api_key and (templates != template_root or output != output_root):
                raise ValueError("Ordner im Netzwerkmodus können nur am Server geändert werden")
        except (ValueError, ValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        batch_id = uuid4().hex
        report_path = str(Path(output) / f"report-{batch_id}.json")
        with lock:
            if any(s["status"] == "running" for s in states.values()):
                raise HTTPException(409, "Ein Batch läuft bereits")
            while len(states) >= 20:
                del states[next(iter(states))]
            states[batch_id] = {
                "id": batch_id,
                "status": "running",
                "done": 0,
                "total": len(jobs),
                "events": [],
                "report": None,
            }

        def progress(done, total, result):
            with lock:
                state = states[batch_id]
                state["done"] = done
                state["total"] = total
                state["events"] = (state["events"] + [result])[-50:]

        def work():
            try:
                report = run_batch(
                    jobs,
                    templates,
                    output,
                    data.workers,
                    data.dry_run,
                    data.font or font,
                    report_path,
                    progress,
                    restricted=True,
                )
                with lock:
                    states[batch_id].update(
                        status="finished",
                        done=report["total"],
                        total=report["total"],
                        report=report,
                    )
            except Exception as exc:
                with lock:
                    states[batch_id].update(status="error", error=str(exc))

        executor.submit(work)
        return {"id": batch_id, "status": "running"}

    @app.get("/api/batches/{batch_id}", dependencies=[Depends(authenticate)])
    def status(batch_id: str):
        with lock:
            if batch_id not in states:
                raise HTTPException(404, "Unbekannter oder abgelaufener Batch")
            state = states[batch_id]
            return {k: v for k, v in state.items() if k != "report"} | {
                "summary": {k: v for k, v in (state["report"] or {}).items() if k != "results"}
            }

    @app.get("/api/batches/{batch_id}/report", dependencies=[Depends(authenticate)])
    def report(batch_id: str):
        with lock:
            data = states.get(batch_id, {}).get("report")
            if data is None:
                raise HTTPException(404, "Report noch nicht verfügbar")
            return JSONResponse(
                data,
                headers={"Content-Disposition": f'attachment; filename="report-{batch_id}.json"'},
            )

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
