import random

def normalize(affinities: dict) -> dict:
    total = sum(affinities.values())
    if total == 0:
        return {k: 1.0/len(affinities) for k in affinities}
    return {k: v / total for k, v in affinities.items()}

def get_mutation_rate(cycle_count: int, fitness_trend: float) -> float:
    base_rate = 0.05
    return 0.25 if fitness_trend < 0.2 else base_rate

def mutate(genome: dict, mutation_rate: float) -> dict:
    # 1. Mutate
    for gene in genome["affinities"]:
        if random.random() < mutation_rate:
            # Allow drift, even if negative or > 1.0
            genome["affinities"][gene] += random.uniform(-0.1, 0.1)
    
    # 2. Renormalize to maintain a valid probability distribution
    genome["affinities"] = normalize(genome["affinities"])
    return genome
