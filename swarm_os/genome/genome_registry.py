from swarm_os.genome.skill_genome import SkillGenome

class GenomeRegistry:
    """
    Maps skill_id → genome
    """

    def __init__(self):
        self._genomes = {}

    def get(self, skill_id: str) -> SkillGenome:
        if skill_id not in self._genomes:
            self._genomes[skill_id] = SkillGenome(skill_id)
        return self._genomes[skill_id]

    def update(self, skill_id: str, score: float, failure: bool = False):
        genome = self.get(skill_id)

        genome.update_fitness(score)

        # failure increases exploration pressure
        pressure = 1.2 if failure else 0.8

        genome.mutate(pressure)

        return genome
