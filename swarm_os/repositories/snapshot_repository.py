from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

class SnapshotRepository(ABC):
    @abstractmethod
    def save(self, payload: dict, generation: int) -> Path: ...

    @abstractmethod
    def load(self, path: str | Path) -> dict: ...

    @abstractmethod
    def list(self) -> list[Path]: ...

    @abstractmethod
    def latest(self) -> Path | None: ...
