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
from .organism import Organism
from .selection import SelectionEngine
from .snapshot import save_snapshot

log = logging.getLogger(__name__)
_CONCURRENCY = 1

def _make_organism(org_id: str, genome: Genome, task_domain: str = "general") -> Organism:
    brain = brain_registry.make("swarm", genome, task_domain)
    return Organism(id=org_id, brain=brain, genome=genome)

async def _act_async(organism: Organism, env_state: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, organism.act, env_state)

class SwarmKernel:
    def __init__(
        self,
        organisms: List[Organism],
        env: Environment,
        snapshot_every: int = 5,
        elite_count: int = 2,
        fitness_decay: float = 0.85,
    ):
        self.organisms = organisms
        self.env = env
        self.selector = SelectionEngine()
        self.generation = 0
        self.snapshot_every = snapshot_every
        self.elite_count = elite_count
        self.fitness_decay = fitness_decay
        self._generation_log: List[Dict] = []

    async def step_async(self, human_scores: Optional[Dict[str, float]] = None) -> Dict:
        t0 = time.perf_counter()
        self.env.tick()
        task = getattr(self.env, "current_task", None)

        env_state = self.env.state()
        if task:
            env_state["task"] = task.prompt

        elites = self.selector.top_organisms(self.organisms, n=self.elite_count)
        elite_ids = {o.id for o in elites}

        sem = asyncio.Semaphore(_CONCURRENCY)
        actions = await asyncio.gather(*[_act_async(o, env_state, sem) for o in self.organisms])

        self.env.apply(list(actions))
        self.selector.evaluate(self.organisms, self.env, actions=list(actions), human_scores=human_scores)

        for o in self.organisms:
            o.fitness *= self.fitness_decay

        parents = self.selector.select(self.organisms, k=max(2, len(self.organisms)))

        children = []
        for i in range(0, len(parents) - 1, 2):
            child_genome = crossover(parents[i].genome, parents[i + 1].genome)
            mutate(child_genome)
            child_genome.parent_id = parents[i].id
            child = _make_organism(
                org_id=f"g{self.generation}_c{random.randint(0, 9999)}",
                genome=child_genome,
                task_domain=task.domain if task else "general",
            )
            children.append(child)

        self.organisms.extend(children)
        self.organisms = self.selector.cull(self.organisms, max_size=settings.population_max)

        current_ids = {o.id for o in self.organisms}
        for elite in elites:
            if elite.id not in current_ids:
                self.organisms.sort(key=lambda o: o.fitness)
                if self.organisms and self.organisms[0].id not in elite_ids:
                    self.organisms[0] = elite

        top = self.selector.top_organisms(self.organisms, n=3)
        summary = {
            "generation": self.generation,
            "population": len(self.organisms),
            "children_bred": len(children),
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "elite_ids": list(elite_ids),
            "top_organisms": [{"id": o.id, "fitness": round(o.fitness, 4)} for o in top],
        }

        self._generation_log.append(summary)

        if self.generation % self.snapshot_every == 0:
            save_snapshot(self.organisms, self.generation)

        self.generation += 1
        return summary

    def step(self, human_scores: Optional[Dict[str, float]] = None) -> Dict:
        return asyncio.run(self.step_async(human_scores=human_scores))

    def run(self, generations: int = 10) -> List[Dict]:
        return [self.step() for _ in range(generations)]

    async def run_async(self, generations: int = 10) -> List[Dict]:
        summaries = []
        for _ in range(generations):
            summaries.append(await self.step_async())
        return summaries

    def status(self) -> Dict:
        top = self.selector.top_organisms(self.organisms, n=5)
        return {
            "generation": self.generation,
            "population": len(self.organisms),
            "entropy": round(self.env.entropy, 4),
            "resources": self.env.resources,
            "top_organisms": [{"id": o.id, "fitness": round(o.fitness, 4)} for o in top],
        }



