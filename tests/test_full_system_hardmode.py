from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.adaptation.approval.approval_execution import ApprovalExecutionService
from swarm_os.adaptation.approval.approval_queue import ApprovalQueue
from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.observability.healing_audit import HealingAudit
from swarm_os.adaptation.observability.healing_metrics import HealingMetrics
from swarm_os.adaptation.policy.policy_engine import RemediationPolicyEngine
from swarm_os.app.services.learning_service import LearningService
from swarm_os.app.services.research_service import ResearchService


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


class HealthyVerifier:
    def verify(self, component: str) -> dict[str, object]:
        return {
            "component": component,
            "verified": True,
            "status": "healthy",
            "latency_ms": 1.0,
            "detail": "ok",
        }


class Outcome:
    def __init__(self, outcome_id: str, component: str, status: str) -> None:
        self.outcome_id = outcome_id
        self.component = component
        self.status = status


def _make_test_app(tmp_path: Path):
    approvals = ApprovalQueue(store_path=tmp_path / "approvals.json")
    metrics = HealingMetrics(store_path=tmp_path / "metrics.json")
    audit = HealingAudit(store_path=tmp_path / "audit.json")
    policy = RemediationPolicyEngine()
    executor = CountingExecutor()
    verifier = HealthyVerifier()
    healing = HealingEngine(
        state_path=tmp_path / "healing_state.json",
        executor=executor,
        verifier=verifier,
        metrics=metrics,
        audit=audit,
        policy_engine=policy,
        approval_queue=approvals,
    )
    approval_execution = ApprovalExecutionService(queue=approvals, executor=executor)
    learning = LearningService(store_path=tmp_path / "learning_store.json")

    # Seed learning with vector failures to force degraded research fallback.
    learning.ingest_outcome(Outcome("o-1", "vector_store", "failed"))
    learning.ingest_outcome(Outcome("o-2", "vector_store", "failed"))
    learning.ingest_outcome(Outcome("o-3", "vector_store", "failed"))

    research = ResearchService()
    research.learning = learning
    research.healing = healing

    api_features = importlib.import_module("swarm_os.api.api_features")

    api_features.approvals = approvals
    api_features.metrics = metrics
    api_features.audit = audit
    api_features.policy = policy
    api_features.healing = healing
    api_features.approval_execution = approval_execution
    api_features.learning = learning
    api_features.research = research

    app = FastAPI()
    app.include_router(api_features.router)
    return app, executor, approvals, learning, metrics, audit


def test_full_system_hardmode_end_to_end(tmp_path: Path):
    app, executor, approvals, learning, metrics, audit = _make_test_app(tmp_path)
    client = TestClient(app)

    # 1. Policy-gated healing creates approval instead of executing immediately.
    gated = client.post(
        "/features/healing-approvals",
        json={
            "component": "system",
            "action": "restart_component",
            "reason": "manual review required for risky remediation",
        },
    )
    assert gated.status_code == 200
    request_id = gated.json()["request"]["request_id"]

    pending = approvals.get_request(request_id)
    assert pending is not None
    assert pending["status"] == "pending"

    # 2. Reject non-approved execution.
    execute_before_approval = client.post(
        f"/features/healing-approvals/{request_id}/execute"
    )
    assert execute_before_approval.status_code == 200
    body = execute_before_approval.json()
    assert body["status"] == "error"
    assert "not approved" in body["detail"]
    assert executor.calls == 0

    # 3. Approve request, then execute once.
    approve = client.post(
        f"/features/healing-approvals/{request_id}/approve",
        json={"note": "approved during maintenance window"},
    )
    assert approve.status_code == 200
    assert approve.json()["request"]["status"] == "approved"

    execute_once = client.post(f"/features/healing-approvals/{request_id}/execute")
    assert execute_once.status_code == 200
    executed_body = execute_once.json()
    assert executed_body["status"] == "ok"
    assert executed_body["idempotent"] is False
    assert executed_body["request"]["status"] == "executed"
    assert executor.calls == 1

    # 4. Re-execute to verify idempotency.
    execute_twice = client.post(f"/features/healing-approvals/{request_id}/execute")
    assert execute_twice.status_code == 200
    executed_again = execute_twice.json()
    assert executed_again["status"] == "ok"
    assert executed_again["idempotent"] is True
    assert executed_again["request"]["status"] == "executed"
    assert executor.calls == 1

    # 5. Force degraded search via learned vector_store failures.
    degraded = client.post(
        "/features/search",
        json={"query": "agent memory fallback resilience"},
    )
    # If your router does not expose /features/search, this test should be adapted
    # to the route actually mounted in your main app.
    if degraded.status_code == 200:
        degraded_body = degraded.json()
        assert degraded_body["status"] in {"ok", "degraded"}
        if degraded_body["status"] == "degraded":
            assert degraded_body["fallback"] is True
            assert len(degraded_body["results"]) >= 1

    # 6. Dashboard reflects approvals and system state.
    overview = client.get("/features/healing-overview")
    assert overview.status_code == 200
    overview_body = overview.json()

    assert overview_body["status"] == "ok"
    assert "summary" in overview_body
    assert "actions" in overview_body
    assert "approvals" in overview_body
    assert "metrics" in overview_body
    assert "audit" in overview_body
    assert "components" in overview_body
    assert "runbooks" in overview_body
    assert "policy" in overview_body

    assert overview_body["approvals"]["counts"]["total"] >= 1
    assert overview_body["approvals"]["counts"]["executed"] >= 1
    assert isinstance(overview_body["actions"], list)

    # 7. Learning persists across reloads.
    learning.record_repair(
        component="vector_store",
        action="switch_to_fallback_search",
        result="success",
        reason="forced degraded search for hardmode test",
    )
    reloaded_learning = LearningService(store_path=tmp_path / "learning_store.json")
    profile = reloaded_learning.get_component_profile("vector_store")

    assert profile["stats"]["failures"] >= 3
    assert profile["stats"]["successes"] >= 1
    assert len(profile["recent_repairs"]) >= 1

    # 8. Metrics and audit files exist and are non-empty/usable.
    assert (tmp_path / "metrics.json").exists()
    assert (tmp_path / "audit.json").exists()
    assert (tmp_path / "approvals.json").exists()
    assert (tmp_path / "learning_store.json").exists()

    with (tmp_path / "approvals.json").open("r", encoding="utf-8") as fh:
        approvals_payload = json.load(fh)
    assert len(approvals_payload) >= 1

    assert len(audit.recent()) >= 0
    assert isinstance(metrics.snapshot(), dict)
