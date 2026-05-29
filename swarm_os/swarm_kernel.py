# swarm_os/kernel/swarm_kernel.py
"""
SwarmKernel — the evolutionary loop.
Upgrade: elitism no longer freezes organisms — elites are COPIED into
next generation, not the same object. This lets the original compete
normally and potentially be replaced while the copy survives.
Also adds population diversity tracking to the summary.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Dict, List, Optional

from swarm_os.config.settings import settings
from .brain import registry as brain_registry
from .environment import Environment
from .genetics import Genome, crossover, mutate, normalize_affinities
from .organism import Organism
from .selection import SelectionEngine
from .snapshot import save_snapshot

log = logging.getLogger(__name__)

_CONCURRENCY     = 1
_MAX_LOG_ENTRIES = 200


def _make_organism(org_id: str, genome: Genome, task_domain: str = "general") -> Organism:
    brain = brain_registry.make("swarm", genome, task_domain)
    return Organism(id=org_id, brain=brain, genome=genome)


def _clone_organism(org: Organism, new_id: str, task_domain: str = "general") -> Organism:
    """
    Clone an organism for elitism — COPY the genome, don't reuse the object.
    The clone starts with the same genome but fresh fitness history so it
    competes on equal footing with children in the next generation.
    The original organism can still be culled and replaced.
    """
    genome_copy = org.genome.copy(new_parent_id=org.id)
    # Preserve fitness history so elites don't lose their average_fitness advantage
    genome_copy.lifetime_fitness = org.genome.lifetime_fitness
    genome_copy.evaluations      = org.genome.evaluations
    return _make_organism(new_id, genome_copy, task_domain)


async def _act_async(organism: Organism, env_state: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, organism.act, env_state)


def _population_diversity(organisms: List[Organism]) -> Dict:
    """Measure how diverse the population is across domain affinities and model tiers."""
    if not organisms:
        return {}

    def dominant(o):
        return max(["coding", "research", "upwork"],
                   key=lambda d: getattr(o.genome, f"{d}_affinity"))

    dom_counts = {}
    tiers = []
    for o in organisms:
        d = dominant(o)
        dom_counts[d] = dom_counts.get(d, 0) + 1
        tiers.append(o.genome.model_tier)

    avg_tier = sum(tiers) / len(tiers)
    n = len(organisms)
    return {
        "domain_distribution": {k: round(v/n, 2) for k, v in dom_counts.items()},
        "avg_model_tier":      round(avg_tier, 3),
        "n_unique_dominant":   len(dom_counts),
    }


class SwarmKernel:
    def __init__(
        self,
        organisms:      List[Organism],
        env:            Environment,
        snapshot_every: int   = 5,
        elite_count:    int   = 2,
        fitness_decay:  float = 0.85,
    ):
        self.organisms      = organisms
        self.env            = env
        self.selector       = SelectionEngine()
        self.generation     = 0
        self.snapshot_every = snapshot_every
        self.elite_count    = elite_count
        self.fitness_decay  = fitness_decay
        self._generation_log: List[Dict] = []

    async def step_async(
        self,
        human_scores: Optional[Dict[str, float]] = None,
    ) -> Dict:
        t0 = time.perf_counter()

        # 1. Tick environment
        self.env.tick()
        task = self.env.current_task
        log.info("generation=%d domain=%s task_id=%s",
                 self.generation,
                 task.domain  if task else "?",
                 task.task_id if task else "?")

        env_state = self.env.state()
        if task:
            env_state["task"] = task.prompt

        # 2. Elitism — CLONE top organisms, don't freeze them
        #    Clones carry fitness history but are new objects
        #    Originals remain in population and can be culled normally
        elites    = self.selector.top_organisms(self.organisms, n=self.elite_count)
        elite_ids = {o.id for o in elites}

        # 3. Concurrent organism actions
        sem     = asyncio.Semaphore(_CONCURRENCY)
        actions = await asyncio.gather(
            *[_act_async(o, env_state, sem) for o in self.organisms]
        )

        # 4. Apply resource costs
        self.env.apply(list(actions))

        # 5. Score all actions
        self.selector.evaluate(
            self.organisms, self.env,
            actions=list(actions),
            human_scores=human_scores,
        )

        # 6. Fitness decay
        for o in self.organisms:
            o.fitness *= self.fitness_decay

        # 7. Select parents
        parents = self.selector.select(
            self.organisms, k=max(2, len(self.organisms))
        )

        # 8. Breed children — wraparound pairing
        children = []
        pairs = list(range(0, len(parents) - 1, 2))
        if len(parents) % 2 == 1:
            pairs.append(len(parents) - 1)

        for i in pairs:
            a = parents[i]
            b = parents[(i + 1) % len(parents)]
            child_genome           = crossover(a.genome, b.genome)
            mutate(child_genome)
            child_genome.parent_id = a.id
            child = _make_organism(
                org_id      = f"g{self.generation}_c{random.randint(0, 9999)}",
                genome      = child_genome,
                task_domain = task.domain if task else "general",
            )
            children.append(child)

        # 9. Add elite CLONES to next generation pool
        elite_clones = [
            _clone_organism(e, f"elite_{e.id}_g{self.generation}",
                            task.domain if task else "general")
            for e in elites
        ]

        self.organisms.extend(children)
        self.organisms.extend(elite_clones)

        # 10. Cull to population_max
        self.organisms = self.selector.cull(
            self.organisms, max_size=settings.population_max
        )

        elapsed   = time.perf_counter() - t0
        top       = self.selector.top_organisms(self.organisms, n=3)
        step_seed = self.generation
        diversity = _population_diversity(self.organisms)

        summary = {
            "generation":    self.generation,
            "population":    len(self.organisms),
            "task_domain":   task.domain   if task else "unknown",
            "task_id":       task.task_id  if task else "unknown",
            "task_prompt":   (task.prompt[:80] + "…") if task else "",
            "children_bred": len(children),
            "elapsed_s":     round(elapsed, 2),
            "elite_ids":     list(elite_ids),
            "diversity":     diversity,
            "top_organisms": [
                {
                    "id":           o.id,
                    "fitness":      round(o.fitness, 4),
                    "avg_fitness":  round(o.genome.average_fitness, 4),
                    "model_weights":getattr(o.genome, "model_weights", {}),
                    "tools":        o.genome.active_tools(seed=step_seed),
                    "generation":   o.genome.generation,
                    "evaluations":  o.genome.evaluations,
                    "domain_affinities": {
                        "coding":   round(o.genome.coding_affinity,   3),
                        "research": round(o.genome.research_affinity, 3),
                        "upwork":   round(o.genome.upwork_affinity,   3),
                    },
                    "cognition": {
                        "decomposition":            round(o.genome.cognition.decomposition_bias,        2),
                        "reflection":               round(o.genome.cognition.reflection_depth,          2),
                        "verification":             round(o.genome.cognition.verification_bias,         2),
                        "hallucination_sensitivity":round(o.genome.cognition.hallucination_sensitivity, 2),
                    },
                }
                for o in top
            ],
        }

        self._generation_log.append(summary)
        if len(self._generation_log) > _MAX_LOG_ENTRIES:
            self._generation_log = self._generation_log[-_MAX_LOG_ENTRIES:]

        log.info(
            "generation=%d pop=%d top=%s fit=%.4f avg=%.4f diversity=%s elapsed=%.2fs",
            self.generation, len(self.organisms),
            top[0].id              if top else "none",
            top[0].fitness         if top else 0.0,
            top[0].genome.average_fitness if top else 0.0,
            diversity.get("domain_distribution", {}),
            elapsed,
        )

        if self.generation % self.snapshot_every == 0:
            save_snapshot(self.organisms, self.generation)

        self.generation += 1
        return summary

    def step(self, human_scores: Optional[Dict[str, float]] = None) -> Dict:
        return asyncio.run(self.step_async(human_scores=human_scores))

    def run(self, generations: int = 10) -> List[Dict]:
        summaries = []
        for _ in range(generations):
            summaries.append(self.step())
        return summaries

    async def run_async(self, generations: int = 10) -> List[Dict]:
        summaries = []
        for _ in range(generations):
            summaries.append(await self.step_async())
        return summaries

    def status(self) -> Dict:
        top       = self.selector.top_organisms(self.organisms, n=5)
        diversity = _population_diversity(self.organisms)
        return {
            "generation": self.generation,
            "population": len(self.organisms),
            "entropy":    round(self.env.entropy, 4),
            "resources":  self.env.resources,
            "diversity":  diversity,
            "top_organisms": [
                {
                    "id":           o.id,
                    "fitness":      round(o.fitness, 4),
                    "avg_fitness":  round(o.genome.average_fitness, 4),
                    "model_weights":getattr(o.genome, "model_weights", {}),
                    "tools":        o.genome.active_tools(seed=self.generation),
                    "generation":   o.genome.generation,
                }
                for o in top
            ],
        }

