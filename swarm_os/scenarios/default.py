from __future__ import annotations
from swarm_os.genetics import Genome
from swarm_os.organism import Organism
from swarm_os.brain import BrainRegistry, simple_brain

def build_default_population():
    registry = BrainRegistry()
    registry.register("simple", simple_brain)
    brain = registry.get("simple")

    return [
        Organism("A", brain, Genome({"a": 0.5}, ["core"])),
        Organism("B", brain, Genome({"a": 0.2}, ["core"])),
        Organism("C", brain, Genome({"a": 0.9}, ["core"])),
    ]

