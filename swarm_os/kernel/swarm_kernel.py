# swarm_os/kernel/swarm_kernel.py
"""
Compatibility shim that re-exports the canonical SwarmKernel from swarm_os.swarm_kernel.
This keeps existing imports in simulation_service.py and simulation_runner.py working
while consolidating the kernel logic into the root swarm_kernel.py.
"""
from __future__ import annotations

from swarm_os.swarm_kernel import SwarmKernel, _clone_organism, _population_diversity

__all__ = ["SwarmKernel", "_clone_organism", "_population_diversity"]
