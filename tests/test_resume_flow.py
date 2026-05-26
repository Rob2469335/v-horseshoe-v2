from pathlib import Path

from swarm_os.environment import Environment
from swarm_os.restore import organisms_from_snapshot
from swarm_os.snapshot import load_snapshot
from swarm_os.swarm_kernel import SwarmKernel

FIXTURE = Path("tests/fixtures/snapshot_v1.json")

def test_resume_flow_advances_generation():
    snapshot = load_snapshot(FIXTURE)
    organisms = organisms_from_snapshot(snapshot)
    kernel = SwarmKernel(organisms, Environment())
    kernel.generation = snapshot["generation"]

    before = kernel.generation
    kernel.step()

    assert kernel.generation == before + 1
    assert len(kernel.organisms) == len(organisms)

