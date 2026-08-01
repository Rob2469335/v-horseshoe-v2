import pytest
import asyncio
from swarm_os.kernel.swarm_kernel import SwarmKernel
from swarm_os.kernel.genetics import Genome, mutate, crossover
from swarm_os.kernel.organism import Organism

class MockEnv:
    def tick(self):
        pass

class MockBrain:
    def step(self, env):
        self.stepped = True

class MockOrganism:
    def __init__(self, id_val):
        self.id = id_val
        self.brain = MockBrain()
        self.brain.stepped = False
        self.fitness = 0.5
        self.genome = Genome()

@pytest.mark.asyncio
async def test_swarm_kernel_step():
    orgs = [MockOrganism("org1"), MockOrganism("org2")]
    env = MockEnv()
    kernel = SwarmKernel(organisms=orgs, env=env)
    
    assert kernel.generation == 0
    await kernel.step_async()
    assert kernel.generation == 1
    
    for o in orgs:
        assert o.brain.stepped is True

def test_genome_mutation_boundaries():
    g = Genome()
    g.reasoning_depth = 0.5
    
    # Mutate with extremely high variance to push boundaries
    g.mutation_rate = 1.0
    g.lifetime_fitness = {"composite": 0.0}
    g.evaluations = 1
    mutate(g)
    
    # Ensure it stays within 0.0 and 1.0 (assuming the genome implementation bounds it)
    assert 0.0 <= g.reasoning_depth <= 1.0

def test_genome_crossover():
    g1 = Genome()
    g1.reasoning_depth = 0.1
    g1.crossover_stability = 0.0
    g1.cognition.hallucination_sensitivity = 0.9
    
    g2 = Genome()
    g2.reasoning_depth = 0.9
    g2.crossover_stability = 0.0
    g2.cognition.hallucination_sensitivity = 0.1
    
    child = crossover(g1, g2)
    
    assert child.reasoning_depth in [0.1, 0.9, 0.5]
    assert child.cognition.hallucination_sensitivity in [0.1, 0.9, 0.5]
