import hashlib
import json

import pytest

from pshl.engine import RenderError
from pshl.font_mapping import resolve_style
from pshl.jobs import normalize


def test_approved_font_mapping_and_integrity(tmp_path):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    font = fonts / "synthetic.ttf"
    font.write_bytes(b"synthetic fixture")
    data = {
        "version": 1,
        "approved_aliases": {"Old": "Static"},
        "exact_postscript_fonts": {
            "Static": {
                "font": "fonts/synthetic.ttf",
                "font_family": "Synthetic",
                "font_style": "Bold",
                "sha256": hashlib.sha256(font.read_bytes()).hexdigest(),
            }
        },
    }
    (fonts / "font-map.json").write_text(json.dumps(data))
    style = {"font_postscript": "Old", "box": [0, 0, 100, 100]}
    job = normalize(
        {
            "template": "test.psd",
            "output": "test.png",
            "styles": {"Title": style},
            "smart_objects": {"date": {"targets": [style]}},
        },
        tmp_path,
        tmp_path,
        restricted=True,
    )
    assert job["styles"]["Title"]["font"] == str(font.resolve())
    assert job["smart_objects"]["date"]["targets"][0]["font_style"] == "Bold"
    assert style["font_postscript"] == "Old"
    with pytest.raises(RenderError, match="Keine freigegebene"):
        resolve_style({"font_postscript": "Unknown"}, tmp_path)
    with pytest.raises(RenderError, match="kombiniert"):
        resolve_style({**style, "font": "other.ttf"}, tmp_path)
    font.write_bytes(b"changed")
    with pytest.raises(RenderError, match="verändert"):
        resolve_style(style, tmp_path)
