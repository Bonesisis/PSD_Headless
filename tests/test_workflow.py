import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import Artboard
from psd_tools.constants import Tag
from psd_tools.psd.descriptor import Descriptor, DescriptorBlock, Double
from psd_tools.psd.tagged_blocks import TaggedBlock

from pshl.engine import render_job
from pshl.jobs import parse_jobs, run_batch
from pshl.profiles import expand_profile
from pshl.queue import ReviewQueue, digest
from tests.fixtures import fixture


def profile_fixture(tmp_path):
    job, _ = fixture(tmp_path)
    profile = {
        "version": 1,
        "template": "template.psd",
        "defaults": {"font": "synthetic.ttf"},
        "fields": {
            "title": {"layers": ["Card/Content/Title"]},
            "date": {
                "layers": ["Background"],
                "style": {
                    "font": "synthetic.ttf",
                    "size": 12,
                    "color": "white",
                    "box": [0, 90, 240, 120],
                },
            },
        },
        "exports": [{"output": "{name}-{date}.png"}],
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    return job, {
        "profile": "profile.json",
        "name": "episode",
        "fields": {"title": "Hello", "date": "2026-01-01"},
    }


def test_profiles_and_validation(tmp_path):
    _, invocation = profile_fixture(tmp_path)
    report = run_batch([invocation], tmp_path, tmp_path)
    assert report["success"] == 1, report
    assert (tmp_path / "episode-2026-01-01.png").exists()
    with pytest.raises(ValueError, match="Felder"):
        expand_profile({**invocation, "fields": {}}, tmp_path)
    with pytest.raises(ValueError, match="Pfadzeichen"):
        expand_profile({**invocation, "name": "../outside"}, tmp_path)
    with pytest.raises(ValueError, match="Vorlagenordner"):
        expand_profile({"profile": "../outside.json"}, tmp_path, True)


def test_artboard_native_size_and_image_crop(tmp_path):
    job, _ = fixture(tmp_path)
    psd = PSDImage.open(job["template"])
    group = psd[1]
    rect = Descriptor()
    for key, value in [(b"Left", 10), (b"Top ", 10), (b"Rght", 230), (b"Btom", 100)]:
        rect[key] = Double(value)
    data = DescriptorBlock()
    data[b"artboardRect"] = rect
    group.tagged_blocks[Tag.ARTBOARD_DATA1] = TaggedBlock(key=Tag.ARTBOARD_DATA1, data=data)
    Artboard._move(group)
    psd.save(job["template"])
    original = digest(job["template"])
    result = render_job({**job, "artboard": "Card"})
    assert result["status"] == "success"
    with Image.open(job["output"]) as image:
        assert image.size == (220, 90)
    assert digest(job["template"]) == original
    photo = tmp_path / "photo.png"
    Image.new("RGB", (500, 500), "red").save(photo)
    image_job = {
        "template": job["template"],
        "output": str(tmp_path / "photo-out.jpg"),
        "images": {"Background": {"source": str(photo), "focus": [1, 0]}},
    }
    assert render_job(image_job)["status"] == "success"
    with Image.open(image_job["output"]) as image:
        assert image.size == (240, 120)
        assert image.getpixel((0, 0))[0] > 240
    with pytest.raises(ValueError, match="focus"):
        render_job(
            {
                **image_job,
                "output": str(tmp_path / "bad.png"),
                "images": {"Background": {"source": str(photo), "focus": [2, 0]}},
            }
        )


def test_review_restart_approval_and_duplicate(tmp_path):
    job, _ = fixture(tmp_path / "templates")
    job["output"] = str(tmp_path / "out" / "result.png")
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job], tmp_path / "templates", tmp_path / "out")
    assert store.submit([job], tmp_path / "templates", tmp_path / "out")["duplicate"]
    assert store.tick()
    state = store.get(state["id"])
    assert state["status"] == "review", state
    assert not Path(job["output"]).exists()
    store = ReviewQueue(tmp_path / "state")
    assert not store.tick()
    store.decide(state["id"], "approve")
    assert store.tick()
    assert store.get(state["id"])["status"] == "finished"
    assert digest(job["output"]) == state["results"][0]["sha256"]


def test_preview_tamper_rejected_and_lock(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job], tmp_path, tmp_path)
    with store.worker_lock() as locked:
        assert locked
        assert not ReviewQueue(tmp_path / "state").tick()
    store.tick()
    state = store.get(state["id"])
    Path(state["results"][0]["preview"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="verändert"):
        store.decide(state["id"], "approve")
    assert store.decide(state["id"], "reject", "Bad crop")["reason"] == "Bad crop"


def test_weekly_schedule(tmp_path):
    _, job = profile_fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    state = store.submit(
        [job], tmp_path, tmp_path, schedule={"at": at, "every_days": 7, "date_fields": ["date"]}
    )
    store.tick()
    store.decide(state["id"], "approve", series=True)
    store.tick()
    state = store.get(state["id"])
    assert state["status"] == "finished", state
    child = store.get(state["next_id"])
    assert child["original_jobs"][0]["fields"]["date"] == "2026-01-08"
    assert child["series_approved"]
    store.tick()
    assert store.get(child["id"])["status"] == "approved"
    assert not store.tick()  # next week, not now


def test_review_batch_110_jobs(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    jobs = [{**job, "output": str(tmp_path / f"out-{i}.png")} for i in range(110)]
    state = store.submit(jobs, tmp_path, tmp_path)
    store.tick()
    assert store.get(state["id"])["status"] == "review"
    store.decide(state["id"], "approve")
    store.tick()
    assert store.get(state["id"])["status"] == "finished"
    assert len(list(tmp_path.glob("out-*.png"))) == 110


def test_errors_isolated_and_retry_reuses_successes(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    bad = {**job, "output": str(tmp_path / "second.png"), "layers": {"Missing": "Hi"}}
    state = store.submit([bad, job], tmp_path, tmp_path)
    store.tick()
    state = store.get(state["id"])
    assert state["status"] == "error"
    assert state["done"] == 2
    good_preview = state["results"][1]["preview"]
    with pytest.raises(ValueError, match="Status"):
        store.decide(state["id"], "approve")
    store.decide(state["id"], "retry")
    store.tick()
    assert store.get(state["id"])["results"][1]["preview"] == good_preview
    assert not Path(job["output"]).exists()


def test_publish_interruption_and_collision(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job], tmp_path, tmp_path)
    store.tick()
    store.decide(state["id"], "approve")
    Path(job["output"]).write_bytes(b"existing user data")
    store.tick()
    assert store.get(state["id"])["status"] == "error"
    assert Path(job["output"]).read_bytes() == b"existing user data"
    Path(job["output"]).rename(tmp_path / "preserved.bin")
    store.decide(state["id"], "retry")
    store.tick()
    assert store.get(state["id"])["status"] == "finished"


def test_recurring_outputs_must_change(tmp_path):
    _, job = profile_fixture(tmp_path)
    path = tmp_path / "profile.json"
    profile = json.loads(path.read_text())
    profile["exports"] = [{"output": "fixed.png"}]
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="Datumsfeld"):
        ReviewQueue(tmp_path / "state").submit(
            [job],
            tmp_path,
            tmp_path,
            schedule={
                "at": datetime.now(timezone.utc).isoformat(),
                "every_days": 7,
                "date_fields": ["date"],
            },
        )


def test_future_approval_waits_and_can_be_stopped(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit(
        [job],
        tmp_path,
        tmp_path,
        schedule={"at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    )
    store.tick()
    store.decide(state["id"], "approve")
    assert not store.tick()
    store.decide(state["id"], "reject")
    assert not store.tick()
    assert not Path(job["output"]).exists()


def test_running_preview_resumes_after_restart(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job], tmp_path, tmp_path)
    state["status"] = "rendering"
    store.save(state)
    restarted = ReviewQueue(tmp_path / "state")
    assert restarted.tick()
    assert restarted.get(state["id"])["status"] == "review"


def test_csv_image_and_profile_fields():
    import csv
    import io

    content = io.StringIO()
    writer = csv.DictWriter(content, fieldnames=["profile", "fields", "images"])
    writer.writeheader()
    writer.writerow(
        {
            "profile": "example.json",
            "fields": json.dumps({"title": "Hello"}),
            "images": json.dumps({"photo": "photo.jpg"}),
        }
    )
    job = parse_jobs(content.getvalue(), ".csv")[0]
    assert job["fields"] == {"title": "Hello"}
    assert job["images"] == {"photo": "photo.jpg"}
