from __future__ import annotations

from swarm_os.kernel.genetics import Genome
from swarm_os.kernel.organism import Organism
from swarm_os.kernel.brain import simple_brain

def build_stress_population():
    genome_x = Genome(0.8)
    genome_x.smoke = True

    genome_y = Genome(0.6)
    genome_y.smoke = True

    genome_z = Genome(0.4)
    genome_z.smoke = True

    genome_w = Genome(0.2)
    genome_w.smoke = True

    genome_v = Genome(0.1)
    genome_v.smoke = True

    return [
        Organism("X", simple_brain(genome_x, "general"), genome_x),
        Organism("Y", simple_brain(genome_y, "general"), genome_y),
        Organism("Z", simple_brain(genome_z, "general"), genome_z),
        Organism("W", simple_brain(genome_w, "general"), genome_w),
        Organism("V", simple_brain(genome_v, "general"), genome_v),
    ]

