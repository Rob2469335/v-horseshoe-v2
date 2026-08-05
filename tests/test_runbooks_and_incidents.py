from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.adaptation.escalation.escalation_service import EscalationService
from swarm_os.adaptation.incident.incident_summary import IncidentSummaryService
from swarm_os.adaptation.observability.healing_audit import HealingAudit
from swarm_os.adaptation.observability.healing_metrics import HealingMetrics
from swarm_os.adaptation.runbooks.runbook_service import RunbookService
from swarm_os.api.api_features import router as features_router


def test_runbook_service_returns_known_component_guidance():
    service = RunbookService()
    result = service.get_runbook("chat_model")

    assert result["component"] == "chat_model"
    assert "rotate_model_provider" in result["automated_actions"]
    assert len(result["manual_checks"]) >= 1


def test_incident_summary_builds_from_recent_state(tmp_path: Path):
    metrics = HealingMetrics(store_path=tmp_path / "metrics.json")
    audit = HealingAudit(store_path=tmp_path / "audit.json")
    escalation = EscalationService(store_path=tmp_path / "escalations.json")

    metrics.record(component="chat_model", action="rotate_model_provider", executed=True, verified=False, escalated=True)
    metrics.record(component="vector_store", action="restart_vector_layer", executed=True, verified=True, escalated=False)

    audit.record({
        "component": "chat_model",
        "action": "rotate_model_provider",
        "executed": True,
        "verified": False,
        "escalated": True,
    })
    audit.record({
        "component": "vector_store",
        "action": "restart_vector_layer",
        "executed": True,
        "verified": True,
        "escalated": False,
    })

    escalation.escalate(component="chat_model", action="rotate_model_provider", detail="provider still unhealthy")

    summary = IncidentSummaryService(metrics=metrics, audit=audit, escalation=escalation).build_summary()

    assert summary["status"] == "ok"
    assert summary["recent_escalation_count"] >= 1
    assert summary["recent_failed_verification_count"] >= 1
    assert any(item["component"] == "chat_model" for item in summary["top_failing_components"])


def test_runbook_and_incident_endpoints():
    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    runbook_response = client.get("/features/healing-runbook/chat_model")
    assert runbook_response.status_code == 200
    assert runbook_response.json()["status"] == "ok"

    incident_response = client.get("/features/healing-incidents")
    assert incident_response.status_code == 200
    assert incident_response.json()["status"] == "ok"
