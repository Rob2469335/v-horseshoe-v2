from swarm_os.services.control_plane.strategy_registry import strategy_registry


def test_registry_returns_default_for_fast_context():
    strategy = strategy_registry.get_active({"role": "fast"})
    assert strategy.name == "default"


def test_registry_returns_default_for_empty_context():
    strategy = strategy_registry.get_active({})
    assert strategy.name == "default"


def test_registry_can_select_deep_strategy():
    strategy = strategy_registry.get_active({"role": "deep"})
    assert strategy.name == "deep"

