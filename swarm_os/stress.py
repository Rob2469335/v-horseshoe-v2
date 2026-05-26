# swarm_os/scenarios/stress.py
"""
Stress scenario — larger randomized population, higher mutation rates.
normalize_affinities() called after genome construction.
"""
from __future__ import annotations
import random
from typing import List

from swarm_os.kernel.brain import registry as brain_registry
from swarm_os.kernel.genetics import CognitivePolicy, Genome, MCP_TOOL_REGISTRY, normalize_affinities
from swarm_os.kernel.organism import Organism


def _random_cognition() -> CognitivePolicy:
    return CognitivePolicy(**{f: random.random() for f in CognitivePolicy.__dataclass_fields__})


def _random_genome() -> Genome:
    g = Genome(
        model_tier          = random.random(),
        temperature         = random.random(),
        reasoning_depth     = random.random(),
        verbosity           = random.random(),
        coding_affinity     = random.random(),
        research_affinity   = random.random(),
        upwork_affinity     = random.random(),
        context_budget      = random.random(),
        retrieval_top_k     = random.random(),
        memory_window       = random.random(),
        mutation_rate       = random.uniform(0.1, 0.3),
        crossover_stability = random.random(),
        cognition           = _random_cognition(),
        tool_genes          = {t: random.random() for t in MCP_TOOL_REGISTRY},
    )
    normalize_affinities(g)
    return g


def build(population_max: int = 10) -> List[Organism]:
    organisms = []
    for i in range(population_max):
        genome = _random_genome()
        brain  = brain_registry.make("swarm", genome, task_domain="general")
        organisms.append(Organism(id=f"stress_{i}_gen0", brain=brain, genome=genome))
    return organisms
