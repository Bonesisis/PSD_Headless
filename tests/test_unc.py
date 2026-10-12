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


def test_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "pshl", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "1.0.0"
