from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.adaptation.approval.approval_execution import ApprovalExecutionService
from swarm_os.adaptation.approval.approval_queue import ApprovalQueue
from swarm_os.api.api_features import router as features_router


class CountingExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, component: str, action: str):
        self.calls += 1
        return SimpleNamespace(
            status="success",
            component=component,
            action=action,
            detail=f"executed:{self.calls}",
        )


def test_execute_approved_request_is_idempotent(tmp_path: Path):
    queue = ApprovalQueue(store_path=tmp_path / "approvals.json")
    executor = CountingExecutor()
    service = ApprovalExecutionService(queue=queue, executor=executor)

    request = queue.create_request(component="system", action="restart_component", reason="manual remediation")
    queue.decide(request["request_id"], approved=True, note="approved")

    first = service.execute_approved(request["request_id"])
    second = service.execute_approved(request["request_id"])

    assert first["status"] == "ok"
    assert first["idempotent"] is False
    assert first["request"]["status"] == "executed"
    assert second["status"] == "ok"
    assert second["idempotent"] is True
    assert second["request"]["status"] == "executed"
    assert executor.calls == 1


def test_execute_rejects_nonapproved_request(tmp_path: Path):
    queue = ApprovalQueue(store_path=tmp_path / "approvals.json")
    executor = CountingExecutor()
    service = ApprovalExecutionService(queue=queue, executor=executor)

    request = queue.create_request(component="system", action="restart_component", reason="manual remediation")
    result = service.execute_approved(request["request_id"])

    assert result["status"] == "error"
    assert "not approved" in result["detail"]
    assert executor.calls == 0


def test_execute_endpoint_runs_approved_request():
    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    create_response = client.post(
        "/features/healing-approvals",
        json={"component": "system", "action": "restart_component", "reason": "manual review required"},
    )
    request_id = create_response.json()["request"]["request_id"]

    approve_response = client.post(
        f"/features/healing-approvals/{request_id}/approve",
        json={"note": "approved for execution"},
    )
    assert approve_response.status_code == 200

    execute_response = client.post(f"/features/healing-approvals/{request_id}/execute")
    assert execute_response.status_code == 200
    assert execute_response.json()["status"] == "ok"
