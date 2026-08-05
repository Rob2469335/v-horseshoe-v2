$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\rober\Projects\v-horseshoe-v2"
Set-Location $repoRoot

$targets = @(
    "swarm_os.foundation.events.event_record",
    "swarm_os.foundation.memory.outcome_record",
    "swarm_os.foundation.memory.experiment_record",
    "swarm_os.foundation.memory.policy_record",
    "swarm_os.foundation.memory.promotion_record",
    "swarm_os.foundation.memory.rollback_record",
    "swarm_os.foundation.events.event_types",
    "swarm_os.foundation.events.event_store",
    "swarm_os.foundation.events.event_bus",
    "swarm_os.foundation.audit.adaptive_audit_log",
    "swarm_os.cognition.evaluation.evaluator",
    "swarm_os.adaptation.experiments.experiment_runner",
    "swarm_os.adaptation.promotion.promotion_engine",
    "swarm_os.adaptation.rollback.rollback_engine",
    "swarm_os.adaptation.healing.healing_engine",
    "swarm_os.cognition.policy.policy_resolver",
    "swarm_os.governance.constraints.decision_gate",
    "swarm_os.execution.orchestrators.action_orchestrator",
    "swarm_os.execution.tools.tool_registry",
    "swarm_os.execution.tools.tool_contracts",
    "swarm_os.execution.agents.agent_runtime",
    "swarm_os.execution.agents.task_session",
    "swarm_os.organism.contracts.organism_metrics",
    "swarm_os.organism.contracts.organism_snapshot",
    "swarm_os.app.api.main",
    "swarm_os.app.api.routes.chat",
    "swarm_os.app.api.routes.search",
    "swarm_os.app.api.routes.admin",
    "swarm_os.app.api.routes.health",
    "swarm_os.app.services.session_service",
    "swarm_os.app.services.research_service",
    "swarm_os.app.services.learning_service",
    "swarm_os.app.services.status_service"
)

foreach ($target in $targets) {
    python -c "import importlib; importlib.import_module('$target'); print('$target OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "Import failed: $target"
    }
}
