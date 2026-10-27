import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pshl import controller
from pshl.bundles import arrange_exports, filename_component, week_folder
from pshl.profiles import expand_profile
from pshl.queue import ReviewQueue, digest
from tests.test_controller import setup_catalog, workbook


def prepared(root, rows=None):
    profiles = setup_catalog(root)
    table = controller.read_excel(workbook(rows))
    plan = controller.rule_plan(table, profiles)
    assert not plan.pop("errors")
    return controller.compile_plan(plan, table, profiles, root)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        ("2026-09-15", "VOD_2026-KW38"),
        ("2026-10-26", "VOD_2026-KW44"),
        ("2021-01-01", "VOD_2020-KW53"),
        ("2025-12-29", "VOD_2026-KW01"),
    ],
)
def test_iso_week_uses_excel_date_including_year_boundary(day, expected):
    assert week_folder(day) == expected


def test_artboard_names_and_windows_collisions(tmp_path):
    job = prepared(tmp_path)[0]
    exports = [
        {"artboard": "Artwork (1920x2880px)", "output": "x.jpg"},
        {"artboard": "Logo ", "output": "x.png"},
        {"artboard": "Cover?", "output": "x.png"},
        {"artboard": "Cover*", "output": "x.png"},
        {"artboard": "CON", "output": "x.png"},
    ]
    result = arrange_exports(exports, job)
    names = [Path(e["output"]).name for e in result]
    assert names[:2] == ["Artwork (1920x2880px).jpg", "Logo.png"]
    assert names[2] != names[3]
    assert names[4] == "_CON.png"
    assert result[1]["artboard_name"] == "Logo "
    assert filename_component("x" * 400) != filename_component("x" * 399 + "y")
    with pytest.raises(ValueError, match="Doppelte"):
        arrange_exports([exports[0], exports[0]], job)


def test_sorted_excel_rows_keep_order_ids_across_platforms_and_weeks(tmp_path):
    jobs = prepared(
        tmp_path, [["Platform", "First", "15.09.2026"], ["Platform", "Second", "22.09.2026"]]
    )
    first, second = (expand_profile(j, str(tmp_path))[0] for j in jobs)
    assert first["output"].startswith("VOD_2026-KW38/Platform/Auftrag-001-First/")
    assert second["output"].startswith("VOD_2026-KW39/Platform/Auftrag-002-Second/")
    table = controller.read_excel(
        workbook([["Platform", "First", "15.09.2026"], ["Platform", "Second", "22.09.2026"]])
    )
    profiles = controller.catalog(tmp_path)
    plan = controller.rule_plan(table, profiles)
    plan.pop("errors")
    plan["assignments"].reverse()
    assert controller.compile_plan(plan, table, profiles, tmp_path) == jobs


def test_weekly_package_manifest_exact_bytes_and_exclusions(tmp_path):
    templates = tmp_path / "templates"
    jobs = prepared(templates)
    queue = ReviewQueue(tmp_path / "state")
    state = queue.submit(jobs, templates, tmp_path / "out")
    assert state["packages"] == ["VOD_2026-KW43"]
    assert queue.submit(jobs, templates, tmp_path / "out")["duplicate"]
    queue.tick()
    assert not (tmp_path / "out").exists()
    queue.review_item(state["id"], 0, "approved")
    queue.review_item(state["id"], 1, "rejected", "Not needed")
    queue.decide(state["id"], "publish-reviewed")
    queue.tick()
    state = queue.get(state["id"])
    assert state["status"] == "finished", state.get("error")
    package = tmp_path / "out" / state["packages"][0]
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((package / "pruefbericht.json").read_text(encoding="utf-8"))
    assert len(manifest["orders"]) == 1
    assert manifest["orders"][0]["title"] == "Hello"
    published, excluded = manifest["orders"][0]["exports"]
    assert digest(package / published["file"]) == published["sha256"]
    assert excluded["status"] == "excluded" and not (package / excluded["file"]).exists()
    assert report["summary"] == {"total": 2, "published": 1, "excluded": 1}
    assert report["checks"][1]["reason"] == "Not needed"
    assert str(tmp_path) not in json.dumps(manifest)
    assert not (package / ".pshl-reservation").exists()
    assert queue.submit(jobs, templates, tmp_path / "out")["id"] == state["id"]


def test_changed_batches_use_new_revision_and_reserve_whole_package(tmp_path):
    templates = tmp_path / "templates"
    jobs = prepared(templates)
    queue = ReviewQueue(tmp_path / "state")
    output = tmp_path / "out"
    (output / "VOD_2026-KW43").mkdir(parents=True)
    (output / "VOD_2026-KW43" / "manifest.json").write_text("user content")
    first = queue.submit(jobs, templates, output)
    assert first["packages"] == ["VOD_2026-KW43-R02"]
    changed = json.loads(json.dumps(jobs))
    changed[0]["fields"]["date"] = "2026-10-21"
    second = queue.submit(changed, templates, output)
    assert second["packages"] == ["VOD_2026-KW43-R03"]
    assert queue.submit(changed, templates, output)["id"] == second["id"]
    assert (output / "VOD_2026-KW43" / "manifest.json").read_text() == "user content"


def test_report_write_failure_resumes_without_rerender(tmp_path, monkeypatch):
    from pshl import queue as module

    templates = tmp_path / "templates"
    queue = ReviewQueue(tmp_path / "state")
    state = queue.submit(prepared(templates), templates, tmp_path / "out")
    queue.tick()
    queue.decide(state["id"], "approve")
    real_copy = module.shutil.copyfileobj

    def fail_report(source, destination, *args, **kwargs):
        if str(destination.name).endswith("pruefbericht.json"):
            raise OSError("simulated NAS outage")
        return real_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "copyfileobj", fail_report)
    queue.tick()
    state = queue.get(state["id"])
    assert state["status"] == "error"
    before = state["results"][0]["sha256"]
    monkeypatch.setattr(module.shutil, "copyfileobj", real_copy)
    queue.decide(state["id"], "retry")
    queue.tick()
    assert queue.get(state["id"])["status"] == "error"  # partial copy must never be overwritten
    package = tmp_path / "out" / state["packages"][0]
    (package / "pruefbericht.json").rename(package / "operator-preserved-partial.json")
    queue.decide(state["id"], "retry")
    queue.tick()
    state = queue.get(state["id"])
    assert state["status"] == "finished"
    assert digest(state["results"][0]["output"]) == before


def test_recurring_date_advances_week_folder(tmp_path):
    templates = tmp_path / "templates"
    queue = ReviewQueue(tmp_path / "state")
    state = queue.submit(
        prepared(templates),
        templates,
        tmp_path / "out",
        schedule={
            "at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "every_days": 7,
            "date_fields": ["date"],
        },
    )
    queue.tick()
    queue.decide(state["id"], "approve", series=True)
    queue.tick()
    state = queue.get(state["id"])
    child = queue.get(state["next_id"])
    assert child["packages"] == ["VOD_2026-KW44"]
    assert child["original_jobs"][0]["fields"]["date"] == "2026-10-27"


def test_category_layout_reservation_and_reports(tmp_path):
    jobs = prepared(tmp_path)
    jobs[0]["delivery"]["category"] = "Kompakt"
    queue = ReviewQueue(tmp_path / "state")
    state = queue.submit([jobs[0]], str(tmp_path), str(tmp_path / "output"))
    assert state["packages"][0].startswith("Platform/Kompakt/VOD_")
    assert Path(state["jobs"][0]["output"]).relative_to(tmp_path / "output").parts[:2] == (
        "Platform",
        "Kompakt",
    )
    queue.tick()
    state = queue.get(state["id"])
    assert state["status"] == "review"
    queue.decide(state["id"], "approve")
    queue.tick()
    state = queue.get(state["id"])
    assert state["status"] == "finished"
    assert (tmp_path / "output" / state["packages"][0] / "manifest.json").exists()
