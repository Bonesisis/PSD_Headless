# Changelog

## Unreleased

- Added Excel import, bounded JSON planning and optional provider-independent
  planners using Ollama or a chat-completions-compatible API.
- Added an optional offline Docker adapter for operator-installed PhotoCraft,
  with embedded Smart Object text edits and artboard JPG/PNG exports.
- Added explicit approved font-name mappings with font-file hash validation
  and discovery of Windows per-user fonts.
- Added weekly delivery packages, optional platform/category/week/order layout,
  revision reservation, per-image review, manifests and review reports.
- Rendering remains limited to supported template features; PhotoCraft and
  Photoshop output are not guaranteed to be pixel-identical. PhotoCraft binaries,
  licensed fonts and private templates are not included.

- Added reusable field profiles, native-size artboard PNG/JPEG exports and explicit
  rectangular image replacement with focal crop and ICC conversion.
- Added a persistent local review queue, duplicate detection, restart recovery,
  retries, before/after previews and approval of exact output bytes.
- Added scheduled and recurring date-field jobs, with explicit series approval.
- Added CLI/API queue access and simplified dashboard with contextual help.
- Added synthetic artboard, image, approval, recurrence and 110-job workflow tests.
- Added persistent Docker queue storage. Windows Docker Desktop remains unverified.

## 1.0.0 — 2026-10-12

- Added recursive text-layer inspection and strict RGB8 text rendering.
- Added JSON/YAML/CSV batches, per-job error isolation, bounded process workers,
  dry-run validation, JSON reports and overwrite protection.
- Added PNG/JPG/PSD export with DPI/ICC preservation; replacement PSD text is rasterized.
- Added a local dashboard with directory inputs, progress and report downloads.
- Added authenticated background API, root containment and origin/host checks.
- Added PowerShell and shell entry points, UNC handling and platform CI.
- Added synthetic PSD/font integration fixtures and 110-job batch regression tests.
- Added Docker publishing, Pages documentation and agent development guidance.
- Replaced the former private-use license with MIT, copyright Luca-Gene Bonzel.

### Compatibility and limits

- CLI/API schemas changed from the experimental 0.x interface. Use job files and
  `/api/batches`; old Python helper APIs remain experimental.
- Not a Photoshop-equivalent renderer. Paragraph text requires explicit box bounds;
  mixed/complex text styles are rejected, and replaced text is rasterized in PSD.
- Docker Desktop on a Windows host requires a separate manual validation.
