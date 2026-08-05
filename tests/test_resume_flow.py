import pytest
from pathlib import Path

from swarm_os.environment import Environment
from swarm_os.restore import organisms_from_snapshot
from swarm_os.snapshot import load_snapshot
from swarm_os.swarm_kernel import SwarmKernel

FIXTURE = Path("tests/fixtures/snapshot_v1.json")


@pytest.mark.anyio
async def test_resume_flow_advances_generation():
    snapshot = load_snapshot(FIXTURE)
    organisms = organisms_from_snapshot(snapshot)
    kernel = SwarmKernel(organisms, Environment())
    kernel.generation = snapshot["generation"]

    before = kernel.generation
    await kernel.step_async()

    assert kernel.generation == before + 1
    # Real evolution bred children/elites on top of the resumed population.
    assert len(kernel.organisms) >= len(organisms)
    orig_ids = {o.id for o in organisms}
    assert orig_ids.issubset({o.id for o in kernel.organisms})

