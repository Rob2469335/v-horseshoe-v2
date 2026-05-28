import random

import pytest

from swarm_os.config.settings import settings
from swarm_os.kernel.environment import Environment
from swarm_os.kernel.genetics import Genome
from swarm_os.kernel.organism import Organism
from swarm_os.kernel.swarm_kernel import SwarmKernel


def build_kernel():
    random.seed(settings.random_seed)
    env = Environment()

    def brain(ctx):
        return {
            "content": "test response",
            "elapsed": 1.0,
            "finish_reason": "stop",
            "cost": 0.1,
            "tools_used": [],
            "model": "test",
            "total_tokens": 10,
        }

    organisms = [
        Organism("A", brain, Genome()),
        Organism("B", brain, Genome()),
        Organism("C", brain, Genome()),
    ]
    return SwarmKernel(organisms, env)


@pytest.mark.anyio
async def test_same_seed_same_first_step():
    k1 = build_kernel()
    k2 = build_kernel()

    await k1.step()
    await k2.step()

    ids1 = sorted(o.id for o in k1.organisms)
    ids2 = sorted(o.id for o in k2.organisms)

    assert len(ids1) == len(ids2)
    assert k1.generation == k2.generation
