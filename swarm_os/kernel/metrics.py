from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Iterable

@dataclass(frozen=True)
class RunMetrics:
    organism_count: int
    avg_fitness: float
    best_fitness: float
    worst_fitness: float
    generation: int

def summarize(organisms: Iterable, generation: int) -> RunMetrics:
    orgs = list(organisms)
    if not orgs:
        return RunMetrics(0, 0.0, 0.0, 0.0, generation)
    fitness = [float(o.fitness) for o in orgs]
    return RunMetrics(
        organism_count=len(orgs),
        avg_fitness=sum(fitness) / len(fitness),
        best_fitness=max(fitness),
        worst_fitness=min(fitness),
        generation=generation,
    )
