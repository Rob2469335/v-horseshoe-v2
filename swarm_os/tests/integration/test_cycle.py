import random
import unittest.mock as mock

import pytest

from swarm_os.kernel.environment import Environment
from swarm_os.kernel.genetics import Genome, normalize_affinities
from swarm_os.kernel.swarm_kernel import SwarmKernel


def _make_org(org_id: str):
    from swarm_os.kernel.organism import Organism
    from swarm_os.kernel.brain import registry as brain_registry

    g = Genome()
    normalize_affinities(g)
    brain = brain_registry.make("swarm", g, "general")

    org = object.__new__(Organism)
    org.id = org_id
    org.brain = brain
    org.genome = g
    org.fitness = 0.0
    org._action_count = 0

    class _NullMemory:
        def write(self, *a, **kw):
            pass

        def recent(self, n=20):
            return []

    org.memory = _NullMemory()
    return org


MOCK_RESP = {
    "choices": [{"message": {
        "content": "```python\ndef f(): pass\n```",
        "tool_calls": []
    }, "finish_reason": "stop"}],
    "usage": {"total_tokens": 50, "prompt_tokens": 20},
}


async def _run_steps(kernel, n=1):
    with mock.patch("swarm_os.kernel.brain.httpx.Client") as mc:
        mc.return_value.__enter__.return_value.post.return_value.json.return_value = MOCK_RESP
        mc.return_value.__enter__.return_value.post.return_value.raise_for_status = mock.MagicMock()
        for _ in range(n):
            await kernel.step()


@pytest.mark.anyio
async def test_step_runs_without_crash():
    random.seed(42)
    env = Environment()
    organisms = [_make_org(f"org_{i}") for i in range(4)]
    kernel = SwarmKernel(organisms, env, snapshot_every=999)

    await _run_steps(kernel, 1)

    assert kernel.generation == 1
    assert len(kernel.organisms) >= 1


@pytest.mark.anyio
async def test_population_stays_bounded():
    random.seed(7)
    env = Environment()
    organisms = [_make_org(f"org_{i}") for i in range(6)]
    kernel = SwarmKernel(organisms, env, snapshot_every=999)

    try:
        from swarm_os.config.settings import settings
        pop_max = settings.population_max
    except Exception:
        pop_max = 10

    await _run_steps(kernel, 3)

    assert len(kernel.organisms) <= pop_max
