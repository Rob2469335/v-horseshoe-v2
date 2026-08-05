# swarm_os/kernel/swarm_kernel.py
"""
SwarmKernel — the evolutionary loop.

This is the canonical evolutionary engine (previously split across the root
`swarm_os/swarm_kernel.py` and this placeholder). It implements a full
selection-fitness cycle per generation:

  env.tick() -> organisms act -> evaluate -> breed (crossover + mutate)
  -> elite-clone -> cull

Elites are COPIED into the next generation (not the same object), so the
original can compete normally while the copy survives. Population diversity is
tracked per generation.
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
from .genetics import Genome, crossover, mutate
from .metrics import summarize
from .organism import Organism
from .selection import SelectionEngine

log = logging.getLogger(__name__)

_CONCURRENCY     = 10
_MAX_LOG_ENTRIES = 200


def _make_organism(org_id: str, genome: Genome, task_domain: str = "general", generate_fn=None) -> Organism:
    brain = brain_registry.make("swarm", genome, task_domain, generate_fn=generate_fn)
    return Organism(id=org_id, brain=brain, genome=genome)


def _clone_organism(org: Organism, new_id: str, task_domain: str = "general", generate_fn=None) -> Organism:
    """
    Clone an organism for elitism — COPY the genome, don't reuse the object.
    The clone starts with the same genome but fresh fitness history so it
    competes on equal footing with children in the next generation.
    The original organism can still be culled and replaced.
    """
    genome_copy = org.genome.copy(new_parent_id=org.id)
    # Preserve fitness history so elites don't lose their average_fitness advantage
    # NOTE: copy the dict, don't alias it — the clone records its own evaluations,
    # and sharing the parent's dict would mutate the parent's lifetime history too.
    genome_copy.lifetime_fitness = dict(org.genome.lifetime_fitness)
    genome_copy.evaluations      = org.genome.evaluations
    clone = _make_organism(new_id, genome_copy, task_domain, generate_fn=generate_fn)
    clone.is_elite_clone = True
    return clone


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

    avg_tier = sum(tiers) / len(tiers) if tiers else 0.0
    n = len(organisms)
    return {
        "domain_distribution": {k: round(v / n, 2) for k, v in dom_counts.items()},
        "avg_model_tier":      round(avg_tier, 3),
        "n_unique_dominant":   len(dom_counts),
    }


class SwarmKernel:
    """
    Main orchestration engine for Swarm OS.
    Handles environment ticking, action evaluation, selection, and breeding of organisms.
    """
    def __init__(
        self,
        organisms:      List[Organism],
        env:            Environment,
        snapshot_every: int   = 5,
        elite_count:    int   = 2,
        fitness_decay:  float = 0.85,
        generate_fn     = None,
        snapshot_repo   = None,
    ):
        self.organisms      = organisms
        self.env            = env
        self.selector       = SelectionEngine()
        self.generation     = 0
        self.snapshot_every = snapshot_every
        self.elite_count    = elite_count
        self.fitness_decay  = fitness_decay
        self._generation_log: List[Dict] = []
        self.generate_fn     = generate_fn
        self.snapshot_repo   = snapshot_repo


    async def _run_actions(self, env_state: dict) -> list:
        sem = asyncio.Semaphore(_CONCURRENCY)
        return await asyncio.gather(
            *[_act_async(o, env_state, sem) for o in self.organisms]
        )

    def _breed_children(self, task) -> list:
        for o in self.organisms:
            o.fitness *= self.fitness_decay

        parents = self.selector.select(
            self.organisms, k=max(2, len(self.organisms))
        )

        children = []
        pairs = list(range(0, len(parents) - 1, 2))
        if len(parents) % 2 == 1:
            pairs.append(len(parents) - 1)

        for i in pairs:
            a = parents[i]
            b = parents[(i + 1) % len(parents)]
            child_genome = crossover(a.genome, b.genome)
            mutate(child_genome)
            child_genome.parent_id = a.id
            child = _make_organism(
                org_id      = f"g{self.generation}_c{random.randint(0, 9999)}",
                genome      = child_genome,
                task_domain = task.domain if task else "general",
                generate_fn = self.generate_fn,
            )
            children.append(child)
        return children

    def _save_snapshot(self, generation: int) -> None:
        """Persist the current population. Prefers the injected repo (live API path),
        falling back to the module-level file writer (CLI path)."""
        if self.snapshot_repo is not None:
            payload = {
                "snapshot_version": 4,
                "generation": generation,
                "organisms": [
                    {
                        "id": o.id,
                        "fitness": getattr(o, "fitness", 0.0),
                        "genome": o.genome.to_dict(),
                    }
                    for o in self.organisms
                ],
            }
            self.snapshot_repo.save(payload, generation)
        else:
            try:
                from swarm_os.snapshot import save_snapshot
                save_snapshot(self.organisms, generation)
            except Exception as e:
                log.warning("module snapshot failed generation=%d: %s", generation, e)

    def _create_summary(self, t0, task, children, elite_ids) -> Dict:
        elapsed = time.perf_counter() - t0
        top = self.selector.top_organisms(self.organisms, n=3)
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
                    "model":        getattr(o.genome, "dominant_model", "unknown"),
                    "tools":        o.genome.active_tools(seed=self.generation),
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
        return summary

    async def step_async(
        self,
        human_scores: Optional[Dict[str, float]] = None,
    ) -> Dict:
        """Execute one asynchronous step of the evolutionary kernel."""
        t0 = time.perf_counter()

        self.env.tick()
        task = self.env.current_task
        log.info("generation=%d domain=%s task_id=%s", self.generation, task.domain if task else "?", task.task_id if task else "?")

        env_state = self.env.state()
        if task: env_state["task"] = task.prompt

        elites = self.selector.top_organisms(
            [o for o in self.organisms if not getattr(o, "is_elite_clone", False)],
            n=self.elite_count,
        ) or self.selector.top_organisms(self.organisms, n=self.elite_count)
        elite_ids = {o.id for o in elites}

        actions = await self._run_actions(env_state)
        self.env.apply(list(actions))

        self.selector.evaluate(
            self.organisms, self.env,
            actions=list(actions),
            human_scores=human_scores,
        )

        children = self._breed_children(task)
        elite_clones = [
            _clone_organism(e, f"elite_{e.id}_g{self.generation}",
                            task.domain if task else "general",
                            generate_fn=self.generate_fn)
            for e in elites
        ]

        self.organisms.extend(children)
        self.organisms.extend(elite_clones)

        self.organisms = self.selector.cull(
            self.organisms, max_size=settings.population_max
        )

        summary = self._create_summary(t0, task, children, elite_ids)

        if self.generation % self.snapshot_every == 0:
            self._save_snapshot(self.generation)

        self.generation += 1
        return summary

    def step(self, human_scores: Optional[Dict[str, float]] = None) -> Dict:
        """Synchronous wrapper for step_async."""
        try:
            asyncio.get_running_loop()
            return self.step_async(human_scores=human_scores)
        except RuntimeError:
            return asyncio.run(self.step_async(human_scores=human_scores))

    def run(self, generations: int = 10) -> List[Dict]:
        """Run the kernel synchronously for a number of generations."""
        summaries = []
        for _ in range(generations):
            summaries.append(self.step())
        return summaries

    async def run_async(self, generations: int = 10) -> List[Dict]:
        """Run the kernel asynchronously for a number of generations."""
        summaries = []
        for _ in range(generations):
            summaries.append(await self.step_async())
        return summaries

    async def run_steps(self, steps: int = 15):
        """Async driver used by the live SimulationService: runs `steps` generations,
        then returns (self, RunMetrics) to preserve the historical service contract.
        """
        for _ in range(steps):
            await self.step_async()
        metrics = summarize(self.organisms, self.generation)
        log.info("run complete generation=%d", self.generation)
        return self, metrics

    def status(self) -> Dict:
        """Return a snapshot of the current swarm status."""
        top = self.selector.top_organisms(self.organisms, n=5)
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
                    "model":        getattr(o.genome, "dominant_model", "unknown"),
                    "tools":        o.genome.active_tools(seed=self.generation),
                    "generation":   o.genome.generation,
                }
                for o in top
            ],
        }
