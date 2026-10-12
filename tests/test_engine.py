import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from psd_tools import PSDImage

from pshl.engine import RenderError, font_file, inspect_psd, render_job
from pshl.jobs import parse_jobs, run_batch
from tests.fixtures import fixture


def test_nested_type_inspection(tmp_path):
    job, _ = fixture(tmp_path)
    info = inspect_psd(job["template"])
    title = next(x for x in info["layers"] if x["kind"] == "type")
    assert title["path"] == "Card/Content/Title"
    assert title["text"].startswith("Example")
    assert title["style"]["size"] == 20
    assert title["style"]["color"] == (255, 0, 0, 255)
    assert info["dpi"] == (300, 300)


@pytest.mark.parametrize("format", ["png", "jpg", "psd"])
def test_render_profiles_and_original_unchanged(tmp_path, format):
    job, profile = fixture(tmp_path)
    job["output"] = str(tmp_path / f"ü result.{format}")
    original = Path(job["template"]).read_bytes()
    result = render_job(job)
    assert result["status"] == "success"
    assert Path(job["template"]).read_bytes() == original
    if format == "psd":
        info = inspect_psd(job["output"])
        assert info["dpi"] == (300, 300)
        assert next(x for x in info["layers"] if x["name"] == "Title")["kind"] == "pixel"
    else:
        with Image.open(job["output"]) as image:
            assert image.size == (240, 120)
            assert image.info["icc_profile"] == profile
            assert image.info["dpi"][0] == pytest.approx(300, abs=0.1)
            assert image.getpixel((0, 0))[:3] == pytest.approx((20, 41, 56), abs=3)
            pixels = np.asarray(image.convert("RGB"))
            assert ((pixels[:, :, 0] > 200) & (pixels[:, :, 1] < 40) & (pixels[:, :, 2] < 40)).any()
    with pytest.raises(RenderError, match="existiert"):
        render_job(job)


def test_dry_run_and_missing_font(tmp_path):
    job, _ = fixture(tmp_path)
    assert render_job(job, True)["status"] == "checked"
    assert not Path(job["output"]).exists()
    job["font"] = str(tmp_path / "absent.ttf")
    with pytest.raises(RenderError, match="Schriftdatei fehlt"):
        render_job(job, True)
    with pytest.raises(RenderError, match="Schrift fehlt"):
        font_file("NoSuchSyntheticFont123456")


def test_shrink_and_overflow(tmp_path):
    job, _ = fixture(tmp_path)
    job["layers"] = {"Title": "A" * 50}
    with pytest.raises(RenderError, match="passt nicht"):
        render_job(job, True)
    job["shrink"] = True
    assert any("verkleinert" in w for w in render_job(job, True)["warnings"])


@pytest.mark.parametrize("workers", [1, 2])
def test_110_job_batch_with_failure_isolation(tmp_path, workers):
    job, _ = fixture(tmp_path)
    digest = hashlib.sha256(Path(job["template"]).read_bytes()).digest()
    jobs = [dict(job, output=str(tmp_path / f"batch {i}.png")) for i in range(110)]
    jobs.insert(35, dict(job, layers={"Missing": "x"}, output=str(tmp_path / "missing.png")))
    jobs.insert(70, "invalid")
    report_path = tmp_path / "report.json"
    report = run_batch(jobs, workers=workers, report_path=report_path)
    assert report["success"] == 110
    assert report["errors"] == 2
    assert report == json.loads(report_path.read_text(encoding="utf-8"))
    assert digest == hashlib.sha256(Path(job["template"]).read_bytes()).digest()


def test_collisions_and_restricted_roots(tmp_path):
    job, _ = fixture(tmp_path)
    report = run_batch([job, job])
    assert report["errors"] == 2
    assert not Path(job["output"]).exists()
    report = run_batch([dict(job, output=job["template"])])
    assert report["errors"] == 1
    with pytest.raises(RenderError):
        run_batch([job], report_path=job["template"])
    report = run_batch(
        [dict(job, output=str(tmp_path.parent / "escape.png"))],
        template_root=tmp_path,
        output_root=tmp_path,
        restricted=True,
    )
    assert report["errors"] == 1


def test_formats():
    assert len(parse_jobs('[{"layers":{}}]')) == 1
    assert len(parse_jobs("jobs:\n - template: demo.psd", ".yaml")) == 1
    parsed = parse_jobs('template,output,layers\na.psd,a.png,"{""Title"":""Hi""}"', ".csv")
    assert parsed[0]["layers"] == {"Title": "Hi"}
    assert "_error" in parse_jobs("template,output,layers\na.psd,a.png,invalid", ".csv")[0]
    with pytest.raises(RenderError):
        parse_jobs("jobs: [invalid", ".yaml")
    assert "_error" in parse_jobs("template,output,layers\na.psd,a.png", ".csv")[0]


def test_ambiguous_and_hidden_targets(tmp_path):
    job, _ = fixture(tmp_path)
    psd = PSDImage.open(job["template"])
    psd[1][0][0].visible = False
    psd.save(job["template"])
    with pytest.raises(RenderError, match="ausgeblendet"):
        render_job(job, True)
