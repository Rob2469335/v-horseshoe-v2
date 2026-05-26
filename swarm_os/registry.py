# swarm_os/scenarios/registry.py
"""
Scenario registry — maps scenario names to builder functions.
Add new scenarios here without touching the kernel.
"""
from __future__ import annotations
from typing import Callable, Dict, List

from swarm_os.kernel.organism import Organism

ScenarioBuilder = Callable[[int], List[Organism]]

_REGISTRY: Dict[str, ScenarioBuilder] = {}


def register(name: str, builder: ScenarioBuilder) -> None:
    _REGISTRY[name] = builder


def get(name: str) -> ScenarioBuilder:
    if name not in _REGISTRY:
        available = list(_REGISTRY.keys())
        raise KeyError(f"Unknown scenario: {name!r}. Available: {available}")
    return _REGISTRY[name]


def build(name: str, population_max: int) -> List[Organism]:
    return get(name)(population_max)


# ── Auto-register all known scenarios ─────────────────────────────────────────
from swarm_os.scenarios.default import build as _default
from swarm_os.scenarios.stress import build as _stress

register("default", _default)
register("stress", _stress)
