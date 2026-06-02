from swarm_os.services.control_plane.models import RouteDecision
from swarm_os.services.control_plane.strategy import RoutingStrategy
from swarm_os.services.control_plane.strategy_registry import StrategyRegistry


class DeepStrategy(RoutingStrategy):
    @property
    def name(self) -> str:
        return "deep"

    def select_model(
        self,
        *,
        router: object,
        candidates: list[str],
        role: str | None = None,
        allow_fallback: bool = True,
    ) -> RouteDecision:
        return RouteDecision(
            model=candidates[0] if candidates else "",
            role=role or "deep",
            reason="deep_strategy_selected",
            fallback=not bool(candidates),
            strategy=self.name,
            metadata={"strategy": self.name},
        )


def test_registry_returns_default_for_fast_context():
    registry = StrategyRegistry()
    from swarm_os.services.control_plane.strategy import DefaultStrategy
    registry.register(DefaultStrategy())
    registry.set_default("default")

    strategy = registry.get_active({"role": "fast"})
    assert strategy.name == "default"


def test_registry_returns_default_for_empty_context():
    registry = StrategyRegistry()
    from swarm_os.services.control_plane.strategy import DefaultStrategy
    registry.register(DefaultStrategy())
    registry.set_default("default")

    strategy = registry.get_active({})
    assert strategy.name == "default"


def test_registry_can_select_deep_strategy():
    registry = StrategyRegistry()
    from swarm_os.services.control_plane.strategy import DefaultStrategy
    registry.register(DefaultStrategy())
    registry.register(DeepStrategy())
    registry.set_default("default")

    strategy = registry.get_active({"role": "deep"})
    assert strategy.name == "deep"
