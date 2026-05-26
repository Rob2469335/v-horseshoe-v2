from __future__ import annotations
from swarm_os.genetics import Genome
from swarm_os.organism import Organism
from swarm_os.brain import BrainRegistry, simple_brain

def build_stress_population():
    registry = BrainRegistry()
    registry.register("simple", simple_brain)
    brain = registry.get("simple")

    return [
        Organism("X", brain, Genome({"a": 1.0}, ["core", "stress"])),
        Organism("Y", brain, Genome({"a": 0.8}, ["core", "stress"])),
        Organism("Z", brain, Genome({"a": 0.6}, ["core", "stress"])),
        Organism("W", brain, Genome({"a": 0.4}, ["core", "stress"])),
        Organism("V", brain, Genome({"a": 0.2}, ["core", "stress"])),
    ]

