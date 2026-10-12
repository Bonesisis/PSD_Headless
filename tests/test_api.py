import time

from fastapi.testclient import TestClient

from pshl.api import create_app
from tests.fixtures import fixture


def test_dashboard_auth_and_batch(tmp_path):
    job, _ = fixture(tmp_path)
    with TestClient(create_app(str(tmp_path), str(tmp_path))) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        token = client.get("/api/config").json()["token"]
        assert client.post("/api/batches", json={"jobs": [job]}).status_code == 401
        headers = {"X-PSHL-Token": token}
        assert (
            client.post(
                "/api/batches",
                json={"jobs": [job]},
                headers={**headers, "Origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        response = client.post("/api/batches", json={"jobs": [job]}, headers=headers)
        assert response.status_code == 202
        endpoint = "/api/batches/" + response.json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = client.get(endpoint, headers=headers).json()
            if state["status"] != "running":
                break
            time.sleep(0.02)
        assert state["status"] == "finished", state
        assert state["summary"]["success"] == 1
        assert client.get(endpoint + "/report", headers=headers).json()["success"] == 1


def test_network_key_locks_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("PSHL_API_KEY", "test-only-not-a-real-credential")
    with TestClient(create_app(str(tmp_path), str(tmp_path))) as client:
        assert client.get("/api/config").json()["token"] is None
        response = client.post(
            "/api/batches",
            json={"jobs": [{}], "output": str(tmp_path.parent)},
            headers={"X-PSHL-Token": "test-only-not-a-real-credential"},
        )
        assert response.status_code == 400
        assert client.get("/health", headers={"Host": "untrusted.example"}).status_code == 400
