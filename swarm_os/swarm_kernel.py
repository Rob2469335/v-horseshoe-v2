# swarm_os/swarm_kernel.py
"""
Canonical evolutionary kernel — re-exports the implementation in
`swarm_os/kernel/swarm_kernel.py`.

The root path existed as a divergent copy. Keeping it as a thin re-export means
the CLI runner (`simulation_runner.py`), the live API (`SimulationService`), and
tests all share ONE engine, so the two stacks can never drift apart again.
"""
from __future__ import annotations

from .kernel.swarm_kernel import (  # noqa: F401
    SwarmKernel,
    _clone_organism,
    _make_organism,
    _population_diversity,
)

__all__ = ["SwarmKernel", "_clone_organism", "_make_organism", "_population_diversity"]
