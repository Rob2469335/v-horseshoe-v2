from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.adaptation.escalation.escalation_service import EscalationService
from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.observability.healing_audit import HealingAudit
from swarm_os.adaptation.observability.healing_metrics import HealingMetrics
from swarm_os.api.api_features import router as features_router


class FailingVerifier:
    def verify(self, component: str) -> dict[str, object]:
        return {
            "component": component,
            "verified": False,
            "status": "unhealthy",
            "latency_ms": 0.0,
            "detail": "verification failed",
        }


class SuccessExecutor:
    def execute(self, component: str, action: str):
        return SimpleNamespace(
            status="success",
            component=component,
            action=action,
            detail="repair executed",
        )


def test_healing_engine_records_metrics_audit_and_escalation(tmp_path: Path):
    metrics = HealingMetrics(store_path=tmp_path / "metrics.json")
    audit = HealingAudit(store_path=tmp_path / "audit.json")
    escalation = EscalationService(store_path=tmp_path / "escalations.json")
    engine = HealingEngine(
        state_path=tmp_path / "state.json",
        executor=SuccessExecutor(),
        verifier=FailingVerifier(),
        metrics=metrics,
        audit=audit,
        escalation=escalation,
    )

    result = engine.execute({"component": "chat_model", "status": "failed"})

    assert result["executed"] is True
    assert result["verification"]["verified"] is False
    assert "escalation" in result

    metrics_snapshot = metrics.snapshot()
    assert metrics_snapshot["totals"]["attempts"] >= 1
    assert metrics_snapshot["totals"]["executed"] >= 1
    assert metrics_snapshot["totals"]["verified_failure"] >= 1
    assert metrics_snapshot["totals"]["escalations"] >= 1

    assert len(audit.recent()) >= 1
    assert len(escalation.recent()) >= 1


def test_healing_status_endpoint_returns_operational_data():
    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    response = client.get("/features/healing-status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "metrics" in body
    assert "recent_audit" in body
    assert "recent_escalations" in body
