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


def _fake_generate(requested_model, prompt, **kwargs):
    """Deterministic stand-in for the model call.

    `SwarmKernel._make_organism` builds every bred child and elite clone with
    `brain_registry.make(...)` (swarm_kernel.py:42) — it does NOT inherit the
    parent's injected brain. `generate_fn` is the seam that threads through
    `__init__:125` -> `_breed_children:163` -> `_clone_organism:295` ->
    `brain.py:305`, where it short-circuits BEFORE the `SwarmBrainClient(swarm_url=...)`
    call at :318. Without it, children dial the real model endpoint and hang when
    the stack is down.
    """
    return ("```python\ndef f(): return 1\n```", requested_model or "test-model")


def _kernel(organisms):
    return SwarmKernel(organisms, Environment(), generate_fn=_fake_generate)


@pytest.mark.anyio
async def test_step_runs_without_crash():
    random.seed(42)
    organisms = [_make_org(f"org_{i}") for i in range(4)]
    kernel = _kernel(organisms)

    summary = await kernel.step_async()

    assert kernel.generation == 1
    assert len(kernel.organisms) >= 1
    # Real evolution: children were bred this generation.
    assert summary["children_bred"] > 0


@pytest.mark.anyio
async def test_population_stays_bounded():
    random.seed(7)
    organisms = [_make_org(f"org_{i}") for i in range(6)]
    kernel = _kernel(organisms)

    await kernel.step_async()
    await kernel.step_async()
    await kernel.step_async()

    assert len(kernel.organisms) <= settings.population_max
