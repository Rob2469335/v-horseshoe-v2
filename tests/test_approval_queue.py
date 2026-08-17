from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.adaptation.approval.approval_queue import ApprovalQueue
from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.observability.healing_audit import HealingAudit
from swarm_os.adaptation.observability.healing_metrics import HealingMetrics
from swarm_os.adaptation.policy.policy_engine import RemediationPolicyEngine
from swarm_os.api.api_features import router as features_router


def test_approval_queue_create_and_decide(tmp_path: Path):
    queue = ApprovalQueue(store_path=tmp_path / "approvals.json")
    request = queue.create_request(
        component="system", action="restart_component", reason="needs approval"
    )

    assert request["status"] == "pending"

    approved = queue.decide(
        request_id=request["request_id"], approved=True, note="approved by operator"
    )
    assert approved["status"] == "approved"
    assert approved["decision_note"] == "approved by operator"


def test_healing_engine_creates_approval_request_for_policy_gated_action(
    tmp_path: Path,
):
    approvals = ApprovalQueue(store_path=tmp_path / "approvals.json")
    engine = HealingEngine(
        state_path=tmp_path / "state.json",
        metrics=HealingMetrics(store_path=tmp_path / "metrics.json"),
        audit=HealingAudit(store_path=tmp_path / "audit.json"),
        policy_engine=RemediationPolicyEngine(),
        approval_queue=approvals,
    )

    result = engine.execute({"component": "system", "status": "failed"})

    assert result["executed"] is False
    assert result["repair"]["status"] == "approval_required"
    assert "approval_request" in result
    assert len(approvals.list_requests()) == 1


def test_approval_endpoints_support_create_list_and_decide():
    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    create_response = client.post(
        "/features/healing-approvals",
        json={
            "component": "system",
            "action": "restart_component",
            "reason": "manual review required",
        },
    )
    assert create_response.status_code == 200
    request_id = create_response.json()["request"]["request_id"]

    list_response = client.get("/features/healing-approvals")
    assert list_response.status_code == 200
    assert list_response.json()["status"] == "ok"

    approve_response = client.post(
        f"/features/healing-approvals/{request_id}/approve",
        json={"note": "approved for maintenance window"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["request"]["status"] == "approved"

    reject_response = client.post(
        f"/features/healing-approvals/{request_id}/reject",
        json={"note": "should stay approved state or be explicitly overwritten"},
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["request"]["status"] == "rejected"
