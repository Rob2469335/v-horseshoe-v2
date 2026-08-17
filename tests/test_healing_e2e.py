from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.repair.repair_executor import RepairExecutor
from swarm_os.adaptation.verification.repair_verifier import RepairVerifier
from swarm_os.adaptation.policy.policy_engine import RemediationPolicyEngine
from swarm_os.adaptation.observability.healing_metrics import HealingMetrics
from swarm_os.adaptation.observability.healing_audit import HealingAudit
from swarm_os.adaptation.escalation.escalation_service import EscalationService
from swarm_os.app.services.learning_service import LearningService


def test_healing_exec_success(tmp_path):
    # setup components
    metrics = HealingMetrics(store_path=None)
    audit = HealingAudit(store_path=None)
    policy = RemediationPolicyEngine()
    escalation = EscalationService()
    learning = LearningService(store_path=None)

    # executor that returns successful execution for chat_model retry_request
    def chat_exec(component, action):
        assert component == "chat_model"
        assert action in ("retry_request", "rotate_model_provider")
        return True, "repaired"

    executor = RepairExecutor(action_map={("chat_model", "retry_request"): chat_exec})
    verifier = RepairVerifier(probe=None)

    engine = HealingEngine(
        executor=executor,
        verifier=verifier,
        metrics=metrics,
        audit=audit,
        policy_engine=policy,
        escalation=escalation,
        learning=learning,
    )

    # simulate a failure on chat_model
    payload = {"component": "chat_model", "status": "failed"}
    res = engine.execute(payload)

    assert res.get("executed") is True
    assert res.get("repair", {}).get("status") == "success"
    # learning should have recorded a recent repair
    profile = learning.get_component_profile("chat_model")
    assert "recent_repairs" in profile
    assert len(profile["recent_repairs"]) >= 1


def test_healing_policy_requires_approval(tmp_path):
    # system restart should be policy gated
    metrics = HealingMetrics()
    audit = HealingAudit()
    policy = RemediationPolicyEngine()
    escalation = EscalationService()
    learning = LearningService()

    # executor that would succeed if called
    executor = RepairExecutor(
        action_map={("system", "restart_component"): lambda c, a: (True, "restarted")}
    )
    verifier = RepairVerifier(probe=None)

    engine = HealingEngine(
        executor=executor,
        verifier=verifier,
        metrics=metrics,
        audit=audit,
        policy_engine=policy,
        escalation=escalation,
        learning=learning,
    )

    payload = {"component": "system", "status": "failed"}
    res = engine.execute(payload)

    # policy should block and request approval
    assert res.get("executed") is False
    assert res.get("repair", {}).get("status") == "approval_required"
