from __future__ import annotations

from typing import Callable

from swarm_os.scenarios.default import build_default_population
from swarm_os.scenarios.stress import build_stress_population

PopulationBuilder = Callable[[], list]

SCENARIOS: dict[str, PopulationBuilder] = {
    "default": build_default_population,
    "stress": build_stress_population,
}

def register_scenario(name: str, builder: PopulationBuilder) -> None:
    SCENARIOS[name] = builder

def build_population(name: str):
    return SCENARIOS.get(name, build_default_population)()
