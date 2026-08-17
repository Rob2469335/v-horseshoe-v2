import json
from swarm_os.services.control_plane.router import Router
from swarm_os.services.control_plane.models import ModelProfile


def test_router_learning_persists_failures(tmp_path):
    state_file = tmp_path / "router_state.json"

    # 1. First instance: record a failure
    p1 = ModelProfile(name="model-a", role="fast")
    router1 = Router(profiles=[p1])
    router1.record_failure("model-a", cooldown_seconds=100)

    # 2. Persist state
    state_data = router1.export_states()
    state_file.write_text(json.dumps(state_data))

    # 3. Second instance: load state
    router2 = Router(profiles=[p1])
    loaded_data = json.loads(state_file.read_text())
    router2.import_states(loaded_data)

    # 4. Verify learning
    assert router2.get_state("model-a").failures == 1
    assert router2.is_in_cooldown("model-a") is True
