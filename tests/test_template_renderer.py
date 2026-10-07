import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pshl.template_renderer import (
    TemplateDefinition,
    TemplateRenderError,
    TextStyle,
    _fitted_text_image,
    resolve_font_path,
    safe_job_id,
)


EXAMPLE_CONFIG = {
    "text_fields": {
        "headline": {
            "layer_name": "TEXT_HEADLINE",
            "color": "#ffffff",
            "max_lines": 2,
            "uppercase": True,
            "max_length": 120,
        }
    },
    "artboards": {"Square": "square.png"},
}


class TemplateRendererTests(unittest.TestCase):
    def test_safe_job_id(self):
        self.assertEqual(safe_job_id("Example Job 42"), "Example-Job-42")
        with self.assertRaises(ValueError):
            safe_job_id("///")

    def test_definition_is_loaded_from_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template.json"
            path.write_text(json.dumps(EXAMPLE_CONFIG), encoding="utf-8")
            definition = TemplateDefinition.from_file(path)
            self.assertEqual(definition.text_fields["headline"].layer_name, "TEXT_HEADLINE")
            self.assertTrue(definition.text_fields["headline"].uppercase)
            self.assertEqual(definition.artboards, {"Square": "square.png"})

    def test_definition_rejects_output_paths(self):
        config = dict(EXAMPLE_CONFIG)
        config["artboards"] = {"Square": "../outside.png"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(TemplateRenderError):
                TemplateDefinition.from_file(path)

    def test_text_is_fitted_inside_box(self):
        font = resolve_font_path()
        image = _fitted_text_image(
            "A longer example headline",
            (240, 70),
            font,
            TextStyle(color="#ffffff", max_lines=2),
        )
        self.assertEqual(image.size, (240, 70))
        self.assertIsNotNone(image.getbbox())

    def test_png_round_trip_is_rgba(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            Image.new("RGBA", (12, 8), "white").save(path)
            with Image.open(path) as loaded:
                self.assertEqual(loaded.mode, "RGBA")


if __name__ == "__main__":
    unittest.main()
