"""Renderer selection is independent of the planner/provider."""

from .engine import render_job as render_python


def render_job(job: dict, dry_run: bool = False) -> dict:
    renderer = job.get("renderer", "python")
    if renderer == "python":
        return render_python(job, dry_run)
    if renderer == "photocraft":
        from .photocraft import render_job as render_photocraft

        return render_photocraft(job, dry_run)
    raise ValueError(f"Unbekannte Rendering-Engine: {renderer}")
