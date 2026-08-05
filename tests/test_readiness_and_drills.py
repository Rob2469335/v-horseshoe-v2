from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.adaptation.drills.chaos_drill_service import ChaosDrillService
from swarm_os.adaptation.escalation.escalation_service import EscalationService
from swarm_os.adaptation.observability.healing_audit import HealingAudit
from swarm_os.adaptation.observability.healing_metrics import HealingMetrics
from swarm_os.adaptation.readiness.readiness_service import HealingReadinessService
from swarm_os.adaptation.runbooks.runbook_service import RunbookService
from swarm_os.api.api_features import router as features_router


def test_readiness_score_calculates_from_operational_state(tmp_path: Path):
    metrics = HealingMetrics(store_path=tmp_path / "metrics.json")
    audit = HealingAudit(store_path=tmp_path / "audit.json")
    escalation = EscalationService(store_path=tmp_path / "escalations.json")
    runbooks = RunbookService()

    metrics.record(component="vector_store", action="restart_vector_layer", executed=True, verified=True, escalated=False)
    metrics.record(component="chat_model", action="rotate_model_provider", executed=True, verified=False, escalated=True)

    audit.record({
        "component": "vector_store",
        "action": "restart_vector_layer",
        "executed": True,
        "verified": True,
        "escalated": False,
    })
    audit.record({
        "component": "chat_model",
        "action": "rotate_model_provider",
        "executed": True,
        "verified": False,
        "escalated": True,
    })
    escalation.escalate(component="chat_model", action="rotate_model_provider", detail="provider still unstable")

    result = HealingReadinessService(
        metrics=metrics,
        audit=audit,
        escalation=escalation,
        runbooks=runbooks,
    ).calculate()

    assert result["status"] == "ok"
    assert 0 <= result["score"] <= 100
    assert result["rating"] in {"low", "moderate", "high"}
    assert "factors" in result


def test_chaos_drill_summary_lists_defined_drills():
    summary = ChaosDrillService().summary()

    assert summary["status"] == "ok"
    assert summary["total_drills"] >= 2
    assert "vector_store" in summary["coverage_components"]
    assert "chat_model" in summary["coverage_components"]


def test_readiness_and_drill_endpoints():
    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    readiness_response = client.get("/features/healing-readiness")
    assert readiness_response.status_code == 200
    assert readiness_response.json()["status"] == "ok"

    drills_response = client.get("/features/healing-drills")
    assert drills_response.status_code == 200
    assert drills_response.json()["status"] == "ok"
