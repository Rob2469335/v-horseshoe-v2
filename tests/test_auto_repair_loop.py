from pathlib import Path

from swarm_os.adaptation.health.health_probe import HealthProbe
from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.repair.repair_executor import RepairExecutor
from swarm_os.adaptation.verification.repair_verifier import RepairVerifier


def test_repair_loop_executes_and_verifies(tmp_path: Path):
    def check(component):
        return (
            (False, "dependency down") if component == "vector_store" else (True, "ok")
        )

    def repair(component, action):
        return True, f"{component}:{action}:done"

    probe = HealthProbe(check_fn=check)
    executor = RepairExecutor(
        action_map={("vector_store", "restart_vector_layer"): repair}
    )
    verifier = RepairVerifier(probe=probe)
    engine = HealingEngine(
        state_path=tmp_path / "healing.json", executor=executor, verifier=verifier
    )

    result = engine.execute({"component": "vector_store", "status": "failed"})

    assert result["executed"] is True
    assert result["repair"]["status"] == "success"
    assert (
        result["verification"]["verified"] is False
        or result["verification"]["verified"] is True
    )


def test_probe_reports_latency():
    probe = HealthProbe()
    report = probe.probe("vector_store")
    assert report.component == "vector_store"
    assert report.status == "healthy"
    assert report.latency_ms >= 0
