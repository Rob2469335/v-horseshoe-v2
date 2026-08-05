import random

import pytest

from swarm_os.config.settings import settings
from swarm_os.kernel.environment import Environment
from swarm_os.kernel.genetics import Genome, normalize_affinities
from swarm_os.kernel.organism import Organism
from swarm_os.kernel.swarm_kernel import SwarmKernel


def _make_org(org_id: str):
    g = Genome()
    normalize_affinities(g)

    def brain(ctx):
        return {
            "content": "```python\ndef f(): return 1\n```",
            "elapsed": 1.0,
            "finish_reason": "stop",
            "cost": 0.1,
            "tools_used": [],
            "model": "test",
            "total_tokens": 10,
        }

    return Organism(org_id, brain, g)


@pytest.mark.anyio
async def test_step_runs_without_crash():
    random.seed(42)
    env = Environment()
    organisms = [_make_org(f"org_{i}") for i in range(4)]
    kernel = SwarmKernel(organisms, env)

    summary = await kernel.step_async()

    assert kernel.generation == 1
    assert len(kernel.organisms) >= 1
    # Real evolution: children were bred this generation.
    assert summary["children_bred"] > 0


@pytest.mark.anyio
async def test_population_stays_bounded():
    random.seed(7)
    env = Environment()
    organisms = [_make_org(f"org_{i}") for i in range(6)]
    kernel = SwarmKernel(organisms, env)

    await kernel.step_async()
    await kernel.step_async()
    await kernel.step_async()

    assert len(kernel.organisms) <= settings.population_max
