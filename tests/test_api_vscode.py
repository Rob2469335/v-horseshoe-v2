from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.api.api_features import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_vscode_status_reports_available_commands():
    with _client() as client:
        response = client.get("/features/vscode")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "available"
    assert {"list_files", "grep", "lint"} <= set(body["commands"])


def test_vscode_endpoint_executes_allowlisted_operation(tmp_path, monkeypatch):
    from swarm_os.capabilities import vscode_automation

    monkeypatch.setattr(
        vscode_automation.VSCodeAutomationHandler,
        "__init__",
        lambda self: setattr(self, "workspace_root", str(tmp_path)),
    )
    (tmp_path / "sample.py").write_text("value = 1\n", encoding="utf-8")

    with _client() as client:
        response = client.post(
            "/features/vscode",
            json={"command": "list_files", "args": []},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["stdout"] == "sample.py"


def test_vscode_endpoint_rejects_disallowed_operation():
    with _client() as client:
        response = client.post(
            "/features/vscode",
            json={"command": "rm_rf", "args": ["."]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert "safety allowlist" in body["error"]
