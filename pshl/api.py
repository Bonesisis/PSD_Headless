"""Local dashboard and authenticated asynchronous batch API."""

from __future__ import annotations

import asyncio
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

from .jobs import parse_jobs, run_batch


class BatchRequest(BaseModel):
    jobs: list | None = None
    content: str | None = None
    extension: str = ".json"
    templates: str | None = None
    output: str | None = None
    font: str | None = None
    workers: int = Field(default=1, ge=1, le=32)
    dry_run: bool = False


def create_app(template_root=None, output_root=None, font=None):
    template_root = str(Path(template_root or os.getenv("PSHL_TEMPLATE_DIR", ".")).resolve())
    output_root = str(Path(output_root or os.getenv("PSHL_OUTPUT_DIR", "out")).resolve())
    api_key = os.getenv("PSHL_API_KEY", "")
    token = api_key or secrets.token_urlsafe(32)
    executor = ThreadPoolExecutor(max_workers=1)
    lock, states = threading.Lock(), {}
    max_bytes = 5 * 1024 * 1024

    @asynccontextmanager
    async def lifespan(app):
        yield
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
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
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
        }

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
                    states[batch_id].update(status="finished", done=len(jobs), report=report)
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
