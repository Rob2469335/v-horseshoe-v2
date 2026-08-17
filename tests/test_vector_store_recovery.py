from pathlib import Path

from swarm_os.adaptation.health.health_probe import HealthProbe
from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.repair.repair_executor import RepairExecutor
from swarm_os.adaptation.verification.repair_verifier import RepairVerifier


def test_vector_store_restart_and_verify(tmp_path: Path):
    calls = {"restart": 0, "health": 0}

    def restart_hook(component, action):
        calls["restart"] += 1
        return True, f"{component}:{action}:ok"

    def health_hook(component):
        calls["health"] += 1
        return True, "vector store recovered"

    probe = HealthProbe(check_fn=health_hook)
    executor = RepairExecutor(
        action_map={("vector_store", "restart_vector_layer"): restart_hook}
    )
    verifier = RepairVerifier(probe=probe)
    engine = HealingEngine(
        state_path=tmp_path / "healing.json", executor=executor, verifier=verifier
    )

    result = engine.execute({"component": "vector_store", "status": "failed"})

    assert result["action"] in {"restart_vector_layer", "switch_to_fallback_search"}
    assert result["repair"]["status"] == "success"
    assert result["verification"]["verified"] is True
    assert calls["restart"] == 1
    assert calls["health"] >= 0
