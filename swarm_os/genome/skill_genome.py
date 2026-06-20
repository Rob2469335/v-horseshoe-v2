from dataclasses import dataclass, field
from datetime import datetime
import random

@dataclass
class SkillGenome:
    """
    Evolutionary metadata layer for skills
    """

    skill_id: str

    # behavioral traits
    aggressiveness: float = 0.5
    generalization: float = 0.5
    precision: float = 0.5

    # evolutionary controls
    mutation_rate: float = 0.05
    stability: float = 0.5

    # tracking
    fitness_history: list = field(default_factory=list)
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def mutate(self, pressure: float):
        """
        Adaptive mutation:
        - higher pressure → more change
        - stable skills → less change
        """

        effective_mutation = self.mutation_rate * pressure * (1.0 - self.stability)

        def drift(value):
            return max(0.0, min(1.0, value + random.uniform(-effective_mutation, effective_mutation)))

        self.aggressiveness = drift(self.aggressiveness)
        self.generalization = drift(self.generalization)
        self.precision = drift(self.precision)

        self.last_updated = datetime.utcnow().isoformat()

    def update_fitness(self, score: float):
        self.fitness_history.append(score)

        if len(self.fitness_history) > 20:
            self.fitness_history.pop(0)

        avg = sum(self.fitness_history) / len(self.fitness_history)

        # stability emerges from consistency
        self.stability = max(0.1, min(1.0, avg))
