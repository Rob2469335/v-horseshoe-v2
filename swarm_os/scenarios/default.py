from __future__ import annotations

from swarm_os.kernel.genetics import Genome
from swarm_os.kernel.organism import Organism
from swarm_os.kernel.brain import simple_brain

def build_default_population():
    genome_a = Genome(0.5)
    genome_a.smoke = True

    genome_b = Genome(0.2)
    genome_b.smoke = True

    genome_c = Genome(0.9)
    genome_c.smoke = True

    return [
        Organism("A", simple_brain(genome_a, "general"), genome_a),
        Organism("B", simple_brain(genome_b, "general"), genome_b),
        Organism("C", simple_brain(genome_c, "general"), genome_c),
    ]

