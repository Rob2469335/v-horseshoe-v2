from __future__ import annotations

import logging
from swarm_os.kernel.metrics import summarize

log = logging.getLogger(__name__)


class SwarmKernel:
    def __init__(self, organisms, env, generate_fn=None, snapshot_repo=None):
        self.organisms = organisms
        self.env = env
        self.generate_fn = generate_fn
        self.snapshot_repo = snapshot_repo
        self.generation = 0

    async def step_async(self):
        # minimal evolution step (placeholder-safe)
        self.generation += 1

        for o in self.organisms:
            # keep compatibility with existing structure
            if hasattr(o, "brain") and hasattr(o.brain, "step"):
                try:
                    o.brain.step(self.env)
                except Exception:
                    pass

        return self.organisms

    async def run(self, steps: int = 15):
        for _ in range(steps):
            await self.step_async()

            if self.snapshot_repo:
                payload = {
                    "snapshot_version": 4,
                    "generation": self.generation,
                    "organisms": [
                        {
                            "id": o.id,
                            "fitness": getattr(o, "fitness", 0.0),
                            "genome": o.genome.to_dict(),
                        }
                        for o in self.organisms
                    ],
                }

                self.snapshot_repo.save(payload, self.generation)

        metrics = summarize(self.organisms, self.generation)
        log.info("run complete generation=%d", self.generation)

        return self, metrics
