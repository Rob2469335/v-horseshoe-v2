import random
from swarm_os.genetics.mutator import mutate

def run_cycle(population: list) -> list:
    # 1. Evaluate fitness (provided by SelectionEngine)
    # 2. Select survivors
    survivors = sorted(population, key=lambda x: x.fitness, reverse=True)[:len(population)//2]
    
    # 3. Reproduce and Mutate
    next_gen = []
    for _ in range(len(population)):
        parent = random.choice(survivors)
        child = mutate(parent.genome.copy(), mutation_rate=0.1)
        next_gen.append(child)
        
    return next_gen
