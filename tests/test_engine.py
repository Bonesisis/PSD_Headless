import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import Group, PixelLayer

from pshl.engine import RenderError, _prepare_smart_objects, font_file, inspect_psd, render_job
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


def test_rgb_16bit_template_text_render(tmp_path):
    job, _ = fixture(tmp_path, depth=16)
    assert render_job(job, True)["status"] == "checked"
    result = render_job(job)
    assert result["status"] == "success"
    with Image.open(job["output"]) as image:
        assert image.size == (240, 120)


def test_smart_object_job_validation_and_centered_layout(tmp_path):
    from pshl.engine import layout_text

    job, _ = fixture(tmp_path)
    job["layers"] = {}
    job["smart_objects"] = {
        "title": {
            "text": "New title",
            "targets": [{"layer": "Missing smart object", "text_layer": "Title"}],
        }
    }
    with pytest.raises(RenderError, match="0 Treffer"):
        render_job(job, True)
    font = str(tmp_path / "synthetic.ttf")
    image, _ = layout_text(
        "Hi",
        (100, 50),
        {
            "font": font,
            "size": 15,
            "color": (255, 0, 0, 255),
            "align": "center",
            "valign": "center",
        },
    )
    bbox = image.getbbox()
    assert bbox is not None
    assert abs((bbox[1] + bbox[3]) / 2 - 25) <= 1


def test_embedded_smart_object_text_fans_out_to_multiple_artboards(tmp_path, monkeypatch):
    from copy import deepcopy

    from psd_tools.api.layers import PixelLayer
    from psd_tools.constants import Tag

    job, _ = fixture(tmp_path)
    nested = PSDImage.open(job["template"])
    second = PixelLayer.frompil(
        Image.new("RGB", (200, 30), "white"), nested, name="Subtitle", left=10, top=80
    )
    second.tagged_blocks[Tag.TYPE_TOOL_OBJECT_SETTING] = deepcopy(
        nested[1][0][0].tagged_blocks[Tag.TYPE_TOOL_OBJECT_SETTING]
    )
    nested.save(job["template"])
    original_open = PSDImage.open
    monkeypatch.setattr(PSDImage, "open", lambda _source: original_open(job["template"]))

    class EmbeddedObject:
        kind = "data"
        data = b"synthetic embedded PSD"

        def __init__(self, left):
            self._placed_layer = SimpleNamespace(
                transform=(left, 0, left + 240, 0, left + 240, 120, left, 120)
            )

        def is_psd(self):
            return True

    class SmartLayer:
        kind = "smartobject"
        clipping = False

        def __init__(self, left, name):
            self.name = name
            self.smart_object = EmbeddedObject(left)

        def is_group(self):
            return False

        def is_visible(self):
            return True

        def has_mask(self):
            return False

        def has_vector_mask(self):
            return False

        def has_effects(self):
            return False

    class SmartDocument(list):
        width = 500
        height = 200

    root = SmartDocument(
        [SmartLayer(0, "Artwork Smart Object"), SmartLayer(250, "Cover Smart Object")]
    )
    specifications = {
        "headline": {
            "text": "A centered title",
            "font": job["font"],
            "size": 20,
            "color": "#ff0000",
            "targets": [
                {"layer": "Artwork Smart Object", "text_layer": "Card/Content/Title"},
                {"layer": "Cover Smart Object", "text_layer": "Card/Content/Title"},
            ],
        }
    }
    prepared, warnings = _prepare_smart_objects(root, specifications)
    assert len(prepared) == 2
    assert [(item[2], item[3]) for item in prepared] == [(0, 0), (250, 0)]
    assert all(item[1].size == (240, 120) for item in prepared)
    assert len([warning for warning in warnings if "ersetzt" in warning]) == 2
    specifications["subtitle"] = {
        "text": "A second field",
        "font": job["font"],
        "size": 15,
        "targets": [
            {"layer": "Artwork Smart Object", "text_layer": "Subtitle", "box": [10, 80, 230, 115]}
        ],
    }
    prepared, warnings = _prepare_smart_objects(root, specifications)
    assert len(prepared) == 2  # both fields merged into one replacement per outer object
    assert len([warning for warning in warnings if "ersetzt" in warning]) == 3


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


def test_duplicate_names_need_full_path(tmp_path):
    job, _ = fixture(tmp_path)
    psd = PSDImage.open(job["template"])
    group = Group.new(psd, name="Other")
    PixelLayer.frompil(Image.new("RGB", (20, 20), "white"), group, name="Title")
    psd.save(job["template"])
    with pytest.raises(RenderError, match="2 Treffer"):
        render_job(dict(job, layers={"Title": "Hello"}), True)
    assert render_job(job, True)["status"] == "checked"


def test_missing_glyph_and_mask_rejected(tmp_path):
    job, _ = fixture(tmp_path)
    with pytest.raises(RenderError, match="Zeichen nicht"):
        render_job(dict(job, layers={"Title": "🙂"}), True)
    psd = PSDImage.open(job["template"])
    psd[1][0][0].create_mask(Image.new("L", (210, 50), 255), left=15, top=20)
    psd.save(job["template"])
    with pytest.raises(RenderError, match="Maske"):
        render_job(job, True)


def test_reserved_outputs_survive_failure(tmp_path):
    job, _ = fixture(tmp_path)
    original = b"Existing output must remain unchanged"
    Path(job["output"]).write_bytes(original)
    report = run_batch([job])
    assert report["errors"] == 1
    assert Path(job["output"]).read_bytes() == original
    report_path = tmp_path / "existing-report.json"
    report_path.write_bytes(original)
    with pytest.raises(FileExistsError):
        run_batch([job], report_path=report_path)
    assert report_path.read_bytes() == original
