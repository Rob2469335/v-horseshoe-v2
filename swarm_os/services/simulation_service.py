from __future__ import annotations

import logging
import random

from swarm_os.core.settings import get_settings
from swarm_os.kernel.environment import Environment
from swarm_os.kernel.swarm_kernel import SwarmKernel
from swarm_os.kernel.genetics import Genome
import swarm_os.brain as brain_module
from swarm_os.kernel.organism import Organism
from swarm_os.scenarios.registry import build as build_scenario
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
from swarm_os.repositories.snapshot_repository import SnapshotRepository

log = logging.getLogger(__name__)


def _organisms_from_snapshot(snapshot: dict, generate_fn=None) -> list[Organism]:
    items = []
    for item in snapshot.get("organisms", []):
        genome = Genome.from_dict(item["genome"])
        brain = brain_module.registry.make("swarm", genome, "general", generate_fn=generate_fn)
        org = Organism(id=item["id"], brain=brain, genome=genome)
        org.fitness = float(item.get("fitness", 0.0))
        items.append(org)
    return items


class SimulationService:
    def __init__(self, snapshot_repo: SnapshotRepository | None = None, generate_fn=None) -> None:
        self.settings = get_settings()
        self.snapshot_repo = snapshot_repo or FileSnapshotRepository(
            self.settings.snapshots_dir
        )
        self.generate_fn = generate_fn

    async def run(
        self,
        resume_path: str = "",
        steps: int = 15,
        scenario: str | None = None,
        generate_fn=None,
    ):
        s = self.settings
        if generate_fn is None:
            generate_fn = self.generate_fn

        if getattr(s, "random_seed", None):
            random.seed(s.random_seed)

        env = Environment()

        if resume_path:
            raw = self.snapshot_repo.load(resume_path)
            from swarm_os.kernel.migrations import migrate_snapshot
            snapshot = migrate_snapshot(raw)
            organisms = _organisms_from_snapshot(snapshot, generate_fn=generate_fn)

            kernel = SwarmKernel(
                organisms,
                env,
                generate_fn=generate_fn,
                snapshot_repo=self.snapshot_repo,
                snapshot_every=1,
            )
            kernel.generation = snapshot.get("generation", 0)

        else:
            sc_name = scenario or getattr(s, "scenario_name", "default")
            organisms = build_scenario(sc_name)

            kernel = SwarmKernel(
                organisms,
                env,
                generate_fn=generate_fn,
                snapshot_repo=self.snapshot_repo,
                snapshot_every=1,
            )

        return await kernel.run_steps(steps)
