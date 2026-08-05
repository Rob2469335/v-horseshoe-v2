from dataclasses import dataclass
import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        # Strip surrounding single/double quotes so a quoted value like
        # OPENAI_API_BASE="https://..." does not leak literal `"` into URLs.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key.strip()] = value


_load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).lower() in ("true", "1", "yes", "t", "y")


@dataclass(frozen=True)
class Settings:
    log_path: str = _env_str("SWARM_LOG_PATH", "swarm_os/logs/organism_diary.jsonl")
    population_max: int = _env_int("SWARM_POPULATION_MAX", 8)
    random_seed: int = _env_int("SWARM_RANDOM_SEED", 42)
    scenario_name: str = _env_str("SWARM_SCENARIO_NAME", "default")
    swarm_url: str = _env_str("SWARM_URL", "http://127.0.0.1:11436")
    swarm_timeout: float = float(_env_int("SWARM_TIMEOUT", 30))
    snapshot_dir: str = _env_str("SWARM_SNAPSHOT_DIR", "data/snapshots")
    snapshot_every: int = _env_int("SWARM_SNAPSHOT_EVERY", 5)
    generations: int = _env_int("SWARM_GENERATIONS", 20)
    ssl_verify: bool = _env_bool("SWARM_SSL_VERIFY", False)


settings = Settings()

