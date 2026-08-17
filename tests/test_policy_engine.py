from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.adaptation.approval.approval_queue import ApprovalQueue
from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.observability.healing_audit import HealingAudit
from swarm_os.adaptation.observability.healing_metrics import HealingMetrics
from swarm_os.adaptation.policy.policy_engine import RemediationPolicyEngine
from swarm_os.api.api_features import router as features_router


class SuccessExecutor:
    def execute(self, component: str, action: str):
        return SimpleNamespace(
            status="success",
            component=component,
            action=action,
            detail="repair executed",
        )


class HealthyVerifier:
    def verify(self, component: str) -> dict[str, object]:
        return {
            "component": component,
            "verified": True,
            "status": "healthy",
            "latency_ms": 0.0,
            "detail": "ok",
        }


def test_policy_engine_denies_unknown_high_risk_action():
    engine = RemediationPolicyEngine()
    decision = engine.evaluate(
        component="system", action="restart_component", attempt_count=1
    )

    assert decision["permitted"] is False
    assert "approval required" in decision["reasons"]


def test_healing_engine_creates_approval_for_policy_denied_action(tmp_path: Path):
    metrics = HealingMetrics(store_path=tmp_path / "metrics.json")
    audit = HealingAudit(store_path=tmp_path / "audit.json")
    approvals = ApprovalQueue(store_path=tmp_path / "approvals.json")
    engine = HealingEngine(
        state_path=tmp_path / "state.json",
        executor=SuccessExecutor(),
        verifier=HealthyVerifier(),
        metrics=metrics,
        audit=audit,
        policy_engine=RemediationPolicyEngine(),
        approval_queue=approvals,
    )

    result = engine.execute({"component": "system", "status": "failed"})

    assert result["executed"] is False
    assert result["repair"]["status"] == "approval_required"
    assert result["policy"]["permitted"] is False
    assert "approval_request" in result
    assert len(approvals.list_requests()) == 1
    assert len(audit.recent()) >= 1


def test_policy_endpoints_return_policy_data():
    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    all_response = client.get("/features/healing-policy")
    assert all_response.status_code == 200
    assert all_response.json()["status"] == "ok"

    component_response = client.get("/features/healing-policy/chat_model")
    assert component_response.status_code == 200
    assert component_response.json()["status"] == "ok"

    check_response = client.get(
        "/features/healing-policy-check?component=chat_model&action=retry_request&attempt_count=1"
    )
    assert check_response.status_code == 200
    assert check_response.json()["decision"]["permitted"] is True
