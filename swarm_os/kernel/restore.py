# swarm_os/kernel/restore.py
"""
Restore organisms from a snapshot dict.
Fixed: all imports now point to swarm_os.kernel.*
Fixed: uses brain_registry.make() not .get()
"""
from __future__ import annotations

from swarm_os.kernel.organism import Organism
from swarm_os.kernel.genetics import Genome
from swarm_os.kernel.brain import registry as brain_registry
from swarm_os.kernel.migrations import migrate_snapshot


def organisms_from_snapshot(snapshot: dict) -> list[Organism]:
    """
    Rebuild a population from a snapshot dict.
    Runs migration first so old snapshot versions are handled.
    """
    snapshot = migrate_snapshot(snapshot)
    items    = []

    for item in snapshot.get("organisms", []):
        genome = Genome.from_dict(item["genome"])
        brain  = brain_registry.make("swarm", genome, "general")
        org    = Organism(id=item["id"], brain=brain, genome=genome)
        org.fitness = float(item.get("fitness", 0.0))
        items.append(org)

    return items


