from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "v-horseshoe-v2"
    environment: str = "dev"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")
    logs_dir: Path = Path("logs")
    events_dir: Path = Path("data") / "events"
    snapshots_dir: Path = Path("data") / "snapshots"
    ollama_base_url: str = "http://127.0.0.1:11434"
    qdrant_url: str = "http://127.0.0.1:6333"

    @property
    def snapshot_dir(self) -> Path:
        return self.snapshots_dir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
