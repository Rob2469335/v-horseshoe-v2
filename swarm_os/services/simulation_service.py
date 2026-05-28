# swarm_os/services/simulation_service.py
from __future__ import annotations

import logging
import random
from swarm_os.core.settings import get_settings
from swarm_os.kernel.environment import Environment
from swarm_os.kernel.metrics import summarize
from swarm_os.kernel.swarm_kernel import SwarmKernel
from swarm_os.kernel.genetics import Genome
from swarm_os.kernel.brain import registry as brain_registry
from swarm_os.kernel.organism import Organism
from swarm_os.scenarios.registry import build as build_scenario
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
from swarm_os.repositories.snapshot_repository import SnapshotRepository
from swarm_os.kernel.migrations import migrate_snapshot

log = logging.getLogger(__name__)


def _organisms_from_snapshot(snapshot: dict) -> list[Organism]:
    items = []
    for item in snapshot.get("organisms", []):
        genome = Genome.from_dict(item["genome"])
        brain  = brain_registry.make("swarm", genome, "general")
        org    = Organism(id=item["id"], brain=brain, genome=genome)
        org.fitness = float(item.get("fitness", 0.0))
        items.append(org)
    return items


class SimulationService:
    def __init__(self, snapshot_repo: SnapshotRepository | None = None) -> None:
        self.settings      = get_settings()
        self.snapshot_repo = snapshot_repo or FileSnapshotRepository(
            self.settings.snapshots_dir
        )

    async def run(
        self,
        resume_path: str = "",
        steps: int = 15,
        scenario: str | None = None,
    ) -> tuple:
        s = self.settings

        seed = getattr(s, "random_seed", None)
        if seed is not None:
            random.seed(seed)

        env = Environment()

        if resume_path:
            raw       = self.snapshot_repo.load(resume_path)
            snapshot  = migrate_snapshot(raw)
            organisms = _organisms_from_snapshot(snapshot)
            kernel    = SwarmKernel(organisms, env)
            kernel.generation = snapshot.get("generation", 0)
            log.info("resumed from %s at generation %d",
                     resume_path, kernel.generation)
        else:
            sc_name   = scenario or getattr(s, "scenario_name", "default")
            pop_max   = getattr(s, "population_max", 8)
            organisms = build_scenario(sc_name)
            kernel    = SwarmKernel(organisms, env)
            log.info("started fresh scenario=%s pop=%d", sc_name, len(organisms))

        for _ in range(steps):
            await kernel.step()
            payload = {
                "snapshot_version": 4,
                "generation":       kernel.generation,
                "organisms": [
                    {
                        "id":      o.id,
                        "fitness": o.fitness,
                        "genome":  o.genome.to_dict(),
                    }
                    for o in kernel.organisms
                ],
            }
            self.snapshot_repo.save(payload, kernel.generation)

        metrics = summarize(kernel.organisms, kernel.generation)
        log.info("run complete generation=%d best_fitness=%.4f",
                 kernel.generation, metrics.best_fitness)
        return kernel, metrics

