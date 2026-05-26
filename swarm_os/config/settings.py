from dataclasses import dataclass
import os

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default

def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)

@dataclass(frozen=True)
class Settings:
    log_path: str = _env_str("SWARM_LOG_PATH", "swarm_os/logs/organism_diary.jsonl")
    population_max: int = _env_int("SWARM_POPULATION_MAX", 8)
    random_seed: int = _env_int("SWARM_RANDOM_SEED", 42)
    scenario_name: str = _env_str("SWARM_SCENARIO_NAME", "default")
    swarm_url: str = _env_str("SWARM_URL", "http://127.0.0.1:11436")

settings = Settings()
