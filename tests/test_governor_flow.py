import os

from swarm_os.healing.diagnostician import Diagnostician
from swarm_os.healing.learner import Learner
from swarm_os.healing.governor import Governor
from swarm_os.healing.governor_models import gen_id


def test_governor_decide_and_persist(tmp_path):
    timeline = tmp_path / "timeline.json"
    learner = Learner(learning_service=None, timeline_path=str(timeline))
    diag = Diagnostician(memory=learner)
    gov = Governor(diagnostician=diag, learner=learner)

    # high-confidence symptom (OOM)
    sym = {"component": "worker", "detail": "Out of memory: OOM killed process", "metrics_before": {"health_score": 30}}
    decision = gov.decide(sym)
    # high-confidence should either auto-execute or at least sandbox first
    assert decision.get("mode") in ("auto_execute", "sandbox_first")
    inc = decision.get("incident_id")
    # failure persisted
    failures = learner.list_failures()
    assert any(f.get("incident_id") == inc for f in failures)

    # low-confidence symptom
    sym2 = {"component": "db", "detail": "unexpected error code 42", "metrics_before": {"health_score": 70}}
    decision2 = gov.decide(sym2)
    assert decision2.get("mode") in ("approval_required", "sandbox_first")

    # finalize incident with success outcome
    outcome = {"outcome": "SUCCESS", "repair": {"action": "restart", "result": "success"}, "metrics_after": {"health_score": 95}, "confidence": 0.9}
    gov.finalize(inc, outcome)
    # ensure learner record updated
    failures = learner.list_failures()
    updated = next((f for f in failures if f.get("incident_id") == inc), None)
    assert updated is not None
    assert updated.get("outcome") == "SUCCESS"
    assert updated.get("metrics_after", {}).get("health_score") == 95


def test_governor_policy_rejects():
    # custom policy engine that rejects
    class FakePolicy:
        def evaluate_policy_for_symptom(self, s):
            return {"action": "reject", "reason": "unsafe to act"}

    learner = Learner(learning_service=None, timeline_path=os.devnull)
    diag = Diagnostician(memory=None)
    gov = Governor(diagnostician=diag, policy_engine=FakePolicy(), learner=learner)

    sym = {"component": "db", "detail": "corrupt index detected"}
    decision = gov.decide(sym)
    assert decision.get("mode") == "reject"
