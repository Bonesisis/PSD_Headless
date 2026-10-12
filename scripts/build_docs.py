"""Render the public README; never copy local assets or configuration."""

from pathlib import Path

import markdown

root = Path(__file__).resolve().parent.parent
source = (root / "README.md").read_text(encoding="utf-8")
source = source.replace(
    "(AGENTS.md)", "(https://github.com/Bonesisis/PSD_Headless/blob/main/AGENTS.md)"
)
body = markdown.markdown(source, extensions=["fenced_code", "tables", "toc"])
body = body.replace(
    'href="examples/', 'href="https://github.com/Bonesisis/PSD_Headless/blob/main/examples/'
)
page = (
    """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PSD Headless · Dokumentation</title>
<style>body{font:16px/1.65 system-ui,sans-serif;color:#203045;max-width:940px;margin:50px auto;padding:0 24px}
a{color:#087d72}h1,h2,h3{line-height:1.25}h2{margin-top:2.5em}pre{padding:20px;background:#f2f5f7;overflow:auto;border-radius:8px}
code{font-size:.88em}table{border-collapse:collapse}td,th{border:1px solid #dde3e8;padding:10px;text-align:left}img{max-width:100%}</style>
<main>"""
    + body
    + "</main></html>"
)
destination = root / "site"
destination.mkdir(exist_ok=True)
(destination / "index.html").write_text(page, encoding="utf-8")
