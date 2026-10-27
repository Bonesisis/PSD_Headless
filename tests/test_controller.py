import io
import json
import time
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from pshl import controller, planners
from pshl.api import create_app
from pshl.profiles import expand_profile
from pshl.queue import ReviewQueue, digest
from tests.fixtures import fixture


def workbook(rows=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "Aufträge"
    sheet.append(["Wöchentliche Aufträge"])
    for _ in range(3):
        sheet.append([])
    sheet.append(["Plattform", "Anzeigetitel", "Datum", "PSD-Datei", "Bilddatei", "Ausgabeordner"])
    for row in rows or [
        ["Platform", "Hello", datetime(2026, 10, 20), "ignored.psd", "missing.jpg", "../../outside"]
    ]:
        sheet.append(row)
    raw = io.BytesIO()
    book.save(raw)
    return raw.getvalue()


def setup_catalog(root):
    fixture(root)
    profile = {
        "version": 1,
        "template": "template.psd",
        "defaults": {"font": "synthetic.ttf", "shrink": True},
        "fields": {"date": {"layers": ["Card/Content/Title"], "display": "weekday_de"}},
        "exports": [
            {"output": "Platform/{name}-{date}.png"},
            {"output": "Platform/{name}-{date}.jpg"},
        ],
    }
    (root / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (root / "controller.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "platform": {
                        "platform": "Platform",
                        "profile": "profile.json",
                        "output_folder": "Platform",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return controller.catalog(root)


def test_excel_fixed_folders_date_display_and_stable_plan(tmp_path):
    profiles = setup_catalog(tmp_path)
    table = controller.read_excel(workbook())
    assert table["rows"][0]["row"] == 6
    assert table["rows"][0]["date"] == "2026-10-20"
    proposal = controller.rule_plan(table, profiles)
    assert not proposal.pop("errors")
    jobs = controller.compile_plan(proposal, table, profiles, tmp_path)
    assert jobs == controller.compile_plan(proposal, table, profiles, tmp_path)
    expanded = expand_profile(jobs[0], str(tmp_path), True)
    assert expanded[0]["layers"]["Card/Content/Title"] == "Dienstag, 20.10.26"
    assert expanded[0]["output"] == "VOD_2026-KW43/Platform/Auftrag-001-Hello/Canvas.png"
    assert expanded[0]["template"] == "template.psd"
    assert not expanded[0]["images"]
    assert "ignored.psd" not in json.dumps(jobs)


@pytest.mark.parametrize(
    "rows",
    [
        [["Platform", "Hello", "=TODAY()"]],
        [["Platform", "Hello", "not-a-date"]],
        [[None, "Hello", "20.10.2026"]],
    ],
)
def test_excel_errors_are_not_silently_dropped(rows):
    table = controller.read_excel(workbook(rows))
    assert table["errors"][0]["row"] == 6


def test_excel_limits_and_bad_file():
    with pytest.raises(ValueError, match="XLSX"):
        controller.read_excel(b"bad file")
    with pytest.raises(ValueError, match="5 MiB"):
        controller.read_excel(b"x" * (5 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1000"):
        controller.read_excel(workbook([["Platform", "Hello", "20.10.2026"]] * 1001))


def test_plan_has_no_commands_paths_missing_or_duplicate_rows(tmp_path):
    profiles = setup_catalog(tmp_path)
    table = controller.read_excel(workbook())
    entry = {"row": 6, "profile_id": "platform", "fields": {"date": "2026-10-20"}}
    for plan in (
        {"assignments": [entry, entry]},
        {"assignments": []},
        {"assignments": [{**entry, "command": "execute anything"}]},
        {"assignments": [{**entry, "profile_id": "../../outside"}]},
        {"assignments": [{**entry, "fields": {"date": "2026-10-20", "output": "elsewhere"}}]},
    ):
        with pytest.raises(ValueError):
            controller.compile_plan(plan, table, profiles, tmp_path)


@pytest.mark.parametrize("protocol", ["ollama", "chat-completions"])
def test_provider_transport_is_replaceable_and_does_not_render(monkeypatch, protocol):
    monkeypatch.setenv("PSHL_PLANNER_PROTOCOL", protocol)
    monkeypatch.setenv("PSHL_PLANNER_URL", "http://localhost:1234/plan")
    monkeypatch.setenv("PSHL_PLANNER_MODEL", "test-model")
    monkeypatch.delenv("PSHL_PLANNER_KEY", raising=False)
    expected = {
        "assignments": [{"row": 6, "profile_id": "platform", "fields": {"date": "2026-10-20"}}]
    }
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        assert "authorization" not in request.headers
        payload = {"content": json.dumps(expected)}
        return httpx.Response(200, json={"message": payload, "choices": [{"message": payload}]})

    client_type = httpx.Client
    monkeypatch.setattr(
        planners.httpx,
        "Client",
        lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw),
    )
    assert planners.propose({"rows": [], "profiles": [], "schema": {}}) == expected
    assert captured[0]["stream"] is False
    assert "tools" not in captured[0]


def test_disabled_provider_and_external_http(monkeypatch):
    monkeypatch.setenv("PSHL_PLANNER_PROTOCOL", "disabled")
    with pytest.raises(ValueError, match="nicht konfiguriert"):
        planners.propose({})
    monkeypatch.setenv("PSHL_PLANNER_PROTOCOL", "ollama")
    monkeypatch.setenv("PSHL_PLANNER_MODEL", "test")
    monkeypatch.setenv("PSHL_PLANNER_URL", "http://external.invalid/api/chat")
    with pytest.raises(ValueError, match="HTTPS"):
        planners.propose({})


def test_controller_api_upload_plan_start_and_collision(tmp_path):
    templates = tmp_path / "templates"
    setup_catalog(templates)
    with TestClient(
        create_app(templates, tmp_path / "out", state_root=tmp_path / "state")
    ) as client:
        headers = {"X-PSHL-Token": client.get("/api/config").json()["token"]}
        assert client.post("/api/controller/import", content=workbook()).status_code == 401
        uploaded = client.post("/api/controller/import", content=workbook(), headers=headers)
        assert uploaded.status_code == 200, uploaded.text
        data = uploaded.json()
        assert not data["errors"]
        assert data["export_count"] == 2
        endpoint = "/api/controller/" + data["id"]
        assert (
            client.post(
                endpoint + "/plan", json={"plan": data["plan"]}, headers=headers
            ).status_code
            == 200
        )
        started = client.post(endpoint + "/submit", json={"plan": data["plan"]}, headers=headers)
        assert started.status_code == 202, started.text
        assert client.post(
            endpoint + "/submit", json={"plan": data["plan"]}, headers=headers
        ).json()["duplicate"]
        status_url = "/api/queue/" + started.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = client.get(status_url, headers=headers).json()
            if state["status"] in {"review", "error"}:
                break
            time.sleep(0.02)
        assert state["status"] == "review", state
        assert state["total"] == 2
        assert not list((tmp_path / "out").glob("**/*"))
        assert (
            client.post(
                status_url + "/items/0/review", json={"decision": "approved"}, headers=headers
            ).status_code
            == 200
        )
        assert (
            client.post(
                status_url + "/items/1/review", json={"decision": "rejected"}, headers=headers
            ).status_code
            == 200
        )
        assert client.post(status_url + "/approve", json={}, headers=headers).status_code == 400
        assert (
            client.post(status_url + "/publish-reviewed", json={}, headers=headers).status_code
            == 200
        )


def test_individual_review_survives_renderer_save_and_publishes_only_approved(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job, {**job, "output": str(tmp_path / "second.jpg")}], tmp_path, tmp_path)
    store.tick()
    state = store.get(state["id"])
    state["status"] = "rendering"
    store.save(state)
    with store.worker_lock() as locked:
        assert locked
        store.review_item(state["id"], 0, "approved")
        store.review_item(state["id"], 1, "rejected", "Wrong size")
        store.save(state)  # stale renderer state must not lose independent decisions
    state["status"] = "review"
    store.save(state)
    store.decide(state["id"], "publish-reviewed")
    store.tick()
    published = store.get(state["id"])
    assert published["status"] == "finished"
    assert digest(job["output"]) == state["results"][0]["sha256"]
    assert not (tmp_path / "second.jpg").exists()
    assert published["results"][1]["excluded"]


def test_input_changes_stop_preview(tmp_path):
    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job], tmp_path, tmp_path)
    with open(job["template"], "ab") as stream:
        stream.write(b"changed")
    store.tick()
    assert store.get(state["id"])["status"] == "error"


def test_rejection_racing_with_bulk_approval_is_not_lost(tmp_path, monkeypatch):
    from pshl import queue as module

    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job], tmp_path, tmp_path)
    store.tick()
    real_digest = module.digest
    raced = False

    def reject_during_hash(path):
        nonlocal raced
        if not raced:
            raced = True
            store.review_item(state["id"], 0, "rejected")
        return real_digest(path)

    monkeypatch.setattr(module, "digest", reject_during_hash)
    with pytest.raises(ValueError, match="Bildbewertungen"):
        store.decide(state["id"], "approve")
    assert store.get(state["id"])["status"] == "review"
    assert not store.tick()


def test_live_rejection_stops_automatic_series_approval(tmp_path, monkeypatch):
    from pshl import queue as module

    job, _ = fixture(tmp_path)
    store = ReviewQueue(tmp_path / "state")
    state = store.submit([job, {**job, "output": str(tmp_path / "second.png")}], tmp_path, tmp_path)
    state["series_approved"] = True
    store.save(state)
    real_render = module.render_job
    calls = 0

    def render_and_review(job):
        nonlocal calls
        calls += 1
        if calls == 2:
            store.review_item(state["id"], 0, "rejected")
        return real_render(job)

    monkeypatch.setattr(module, "render_job", render_and_review)
    store.tick()
    assert store.get(state["id"])["status"] == "review"
    assert not store.tick()
