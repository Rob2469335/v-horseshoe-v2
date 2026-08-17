from pathlib import Path

from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.app.services.learning_service import LearningService


def test_learning_persists_to_disk(tmp_path: Path):
    store = tmp_path / "learning.json"
    service = LearningService(store_path=store)

    class Outcome:
        outcome_id = "o-1"
        component = "vector_store"
        status = "failed"

    service.ingest_outcome(Outcome())
    reloaded = LearningService(store_path=store)

    assert len(reloaded.list_outcomes()) == 1
    assert reloaded.list_outcomes()[0]["component"] == "vector_store"
    assert reloaded.list_outcomes()[0]["status"] == "failed"
    assert reloaded.get_component_profile("vector_store")["stats"]["failures"] >= 1


def test_healing_engine_enters_cooldown_after_repeated_failures(tmp_path: Path):
    state = tmp_path / "healing.json"
    engine = HealingEngine(state_path=state)

    first = engine.execute({"component": "vector_store", "status": "failed"})
    second = engine.execute({"component": "vector_store", "status": "failed"})
    third = engine.execute({"component": "vector_store", "status": "failed"})

    assert first["action"] == "restart_vector_layer"
    assert first["executed"] is False
    assert first["repair"]["status"] == "skipped"

    assert second["action"] in {
        "restart_vector_layer",
        "switch_to_fallback_search",
        "cooldown",
    }
    assert third["action"] in {"cooldown", "switch_to_fallback_search"}
