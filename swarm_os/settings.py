# swarm_os/config/settings.py
"""
Swarm OS runtime settings.
Used by kernel internals — separate from v-horseshoe-v2/config/settings.py.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).parent.parent   # swarm_os/

@dataclass
class SwarmSettings:
    log_path:             str   = str(_ROOT / "logs" / "organism_diary.jsonl")
    population_max:       int   = 8
    random_seed:          int | None = None
    scenario_name:        str   = "default"
    swarm_url:            str   = "http://localhost:11436"
    swarm_timeout:        int   = 60
    snapshot_every:       int   = 5
    snapshot_dir:         str   = str(_ROOT / "data" / "snapshots")
    generations:          int   = 20
    initial_mutation_rate:float = 0.1


def _load_from_env(s: SwarmSettings) -> SwarmSettings:
    if v := os.getenv("SWARM_LOG_PATH"):        s.log_path       = v
    if v := os.getenv("SWARM_POPULATION_MAX"):  s.population_max = int(v)
    if v := os.getenv("SWARM_RANDOM_SEED"):     s.random_seed    = int(v)
    if v := os.getenv("SWARM_SCENARIO_NAME"):   s.scenario_name  = v
    if v := os.getenv("SWARM_URL"):             s.swarm_url      = v
    if v := os.getenv("SWARM_TIMEOUT"):         s.swarm_timeout  = int(v)   # was missing
    if v := os.getenv("SWARM_GENERATIONS"):     s.generations    = int(v)
    if v := os.getenv("SWARM_SNAPSHOT_DIR"):    s.snapshot_dir   = v
    return s


settings = _load_from_env(SwarmSettings())
