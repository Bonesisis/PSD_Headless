"""FastAPI-Schnittstelle für den konfigurierbaren Headless-Renderer."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .template_renderer import PSDTemplateRenderer, TemplateRenderError


class RenderRequest(BaseModel):
    texts: dict[str, str] = Field(min_length=1)
    job_id: str | None = Field(default=None, max_length=100)


class RenderResponse(BaseModel):
    job_id: str
    output_directory: str
    files: dict[str, str]
    elapsed_seconds: float


def create_app(
    template_path: str | None = None,
    config_path: str | None = None,
    output_root: str | None = None,
    font_path: str | None = None,
) -> FastAPI:
    template_path = template_path or os.getenv("PSHL_TEMPLATE_PATH", "")
    config_path = config_path or os.getenv("PSHL_CONFIG_PATH", "")
    output_root = output_root or os.getenv("PSHL_OUTPUT_DIR", "out/jobs")
    font_path = font_path or os.getenv("PSHL_FONT_PATH")
    render_lock = asyncio.Lock()

    app = FastAPI(
        title="PSD Headless",
        version="0.3.0",
        description="Rendert konfigurierbare PSD-Vorlagen ohne Photoshop.",
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        template_ready = bool(template_path and Path(template_path).is_file())
        config_ready = bool(config_path and Path(config_path).is_file())
        return {
            "status": "ok" if template_ready and config_ready else "configuration_required",
            "template_configured": template_ready,
            "configuration_loaded": config_ready,
            "output_root": str(Path(output_root).expanduser()),
        }

    @app.post("/render", response_model=RenderResponse)
    async def render(request: RenderRequest) -> dict[str, object]:
        try:
            renderer = PSDTemplateRenderer(
                template_path, config_path, output_root, font_path
            )
            async with render_lock:
                result = await run_in_threadpool(
                    renderer.render,
                    texts=request.texts,
                    job_id=request.job_id,
                )
            return result.as_dict()
        except (ValueError, TemplateRenderError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Dateisystemfehler: {exc}") from exc

    return app


app = create_app()
