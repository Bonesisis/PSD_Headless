# Development guide

## Scope and architecture

PSD Headless is a Python application for trusted local PSD templates, not a
Photoshop-compatible rendering engine. Never promise full fidelity or editable
replacement text in exported PSDs. Preserve source files and reject unsupported
styles rather than silently changing them.

- `pshl/engine.py`: recursive layer selection, strict typography, embedded
  Smart Object text replacement, and image export.
- `pshl/jobs.py`: JSON/YAML/CSV parsing, isolated jobs, bounded process workers.
- `pshl/profiles.py`: explicit editorial field mappings and per-artboard exports.
- `pshl/queue.py`: local SQLite review queue, OS worker lock, preview hashes,
  approval, scheduled publication and explicit recurring-series authorization.
- `pshl/cli.py`: inspect, render, batch, serve.
- `pshl/api.py`: authenticated background batches, status, reports, static UI.
- `pshl/static/`: plain HTML/CSS/JavaScript; no frontend build or external CDN.
- `pshl/document.py`, `blend.py`, `text.py`, `io_psd.py`, `template_renderer.py`:
  legacy experimental Python APIs, not used by the v1 batch/API workflow.
- `tests/fixtures.py`: synthetic PSDs and a synthetic font generated at test time.
- `.github/workflows/`: platform tests, container publishing, static documentation.

## Setup and validation

Python 3.11+; recommended 3.13. Run from the repository root:

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m build
python -m pshl serve
```

Windows: `powershell -File setup_windows.ps1`, then use
`.venv\Scripts\python.exe`. macOS/Linux: `bash setup.sh`, then `.venv/bin/python`.
Keep Windows spawn support (`__main__` guard), Unicode paths and pathlib UNC handling.
Run tests after each significant change. Verify the dashboard in a browser.

## Rules

- Use Python type hints for new public APIs where practical; Ruff formatting.
- No silent font fallback. Invalid or ambiguous targets must be actionable errors.
- No overwriting inputs or existing outputs. Write rendered files via temporary
  siblings and exclusive destination reservation. Reports use exclusive creation.
- Review publication copies the exact approved preview bytes with exclusive
  creation. Partial NAS copies require explicit operator cleanup, never overwrite.
  Keep queue databases on local disk; do not remove state automatically.
- API is for trusted operators. Keep authentication, root containment, host/origin
  validation and the 5 MiB request limit. Never expose the server unauthenticated.
- No automatic downloading of templates, fonts, scripts or external smart objects.
- Never commit real templates, exports, licensed fonts, secrets, private paths,
  client identities or logos. Use synthetic test fixtures and generic examples.
- Do not rewrite Git history without explicit approval. Inspect staged diffs and
  tracked file names before committing. Never force-push as part of routine release.
- Do not claim Windows Docker Desktop verification based only on Linux Docker CI.
- Public documentation and release notes must state rendering limitations.
- MIT copyright: Luca-Gene Bonzel. Do not add other copyright holders to LICENSE.
