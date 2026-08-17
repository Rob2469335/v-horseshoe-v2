import pytest

from swarm_os.services.simulation_service import SimulationService
from tests.mocks import MockSnapshotRepository


@pytest.mark.anyio
async def test_simulation_service_saves_snapshots():
    repo = MockSnapshotRepository()
    service = SimulationService(repo)

    kernel, metrics = await service.run(steps=2)

    assert len(repo.saved) == 2
    assert metrics.organism_count == len(kernel.organisms)
    assert metrics.generation == kernel.generation
