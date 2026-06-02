from __future__ import annotations

from .strategy import DefaultStrategy, DeepStrategy, RoutingStrategy


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, RoutingStrategy] = {}
        self._default_name: str = "default"

    def register(self, strategy: RoutingStrategy) -> None:
        self._strategies[strategy.name] = strategy

    def has(self, name: str) -> bool:
        return name in self._strategies

    def get(self, name: str) -> RoutingStrategy:
        return self._strategies[name]

    def set_default(self, name: str) -> None:
        if name not in self._strategies:
            raise KeyError(f"Unknown strategy: {name}")
        self._default_name = name

    def get_active(self, context: dict | None = None) -> RoutingStrategy:
        context = context or {}
        role = context.get("role")

        if role == "deep" and "deep" in self._strategies:
            return self._strategies["deep"]

        if self._default_name not in self._strategies:
            raise KeyError(f"Unknown strategy: {self._default_name}")

        return self._strategies[self._default_name]


strategy_registry = StrategyRegistry()
strategy_registry.register(DefaultStrategy())
strategy_registry.register(DeepStrategy())
strategy_registry.set_default("default")
