import pytest

from pshl.photocraft import _operations, exclusive_copy, select
from tests.fixtures import fixture


def test_exact_artboard_path_wins_over_nested_name():
    layers = [
        {"name": "Text", "id": 1, "kind": "Group"},
        {
            "name": "Poster",
            "id": 2,
            "kind": "Group",
            "children": [{"name": "Text", "id": 3, "kind": "Group"}],
        },
    ]
    assert select(layers, "Text")["id"] == 1
    assert select(layers, "Poster/Text")["id"] == 3
    with pytest.raises(ValueError, match="nicht eindeutig"):
        select(layers, "Missing")


def test_no_silent_font_fallback_unknown_commands_or_missing_glyphs(tmp_path):
    job, _ = fixture(tmp_path)
    style = {
        "font": job["font"],
        "font_family": "Synthetic Test",
        "font_style": "Regular",
        "box": [0, 0, 100, 100],
    }
    target = {"inner": "Title", "outer": "Poster/Date"}
    job = {
        **job,
        "layers": {},
        "smart_objects": {"date": {**style, "text": "Hello", "targets": [target]}},
    }
    assert _operations(job)[0]["text"] == "Hello"
    job["smart_objects"]["date"]["command"] = "anything"
    with pytest.raises(ValueError, match="unterstützte"):
        _operations(job)
    job["smart_objects"]["date"].pop("command")
    job["smart_objects"]["date"]["text"] = "漢"
    with pytest.raises(ValueError, match="Zeichen"):
        _operations(job)


def test_export_never_replaces_existing_file(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"new")
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        exclusive_copy(source, target)
    assert target.read_bytes() == b"existing"
    assert source.read_bytes() == b"new"
    assert not list(tmp_path.glob(".pshl-*"))
