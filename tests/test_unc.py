import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pshl.jobs import run_batch
from tests.fixtures import fixture


@pytest.mark.skipif(not os.getenv("PSHL_TEST_UNC"), reason="Dedicated Windows SMB integration step")
def test_unc_share():
    root = Path(os.environ["PSHL_TEST_UNC"]) / "Vorlagen mit Umlaut ä"
    job, _ = fixture(root)
    report = run_batch([job], report_path=root / "report.json")
    assert report["success"] == 1, report
    assert Path(job["output"]).exists()
    job["output"] = str(root / "PowerShell Ergebnis.png")
    job_file = root / "Aufträge.json"
    job_file.write_text(json.dumps(job), encoding="utf-8")
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo / "render_windows.ps1"),
            "--jobs",
            str(job_file),
            "--templates",
            str(root),
            "--output",
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(job["output"]).exists()
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo / "start_server_windows.ps1"),
            "--help",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "pshl", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "1.0.0"
