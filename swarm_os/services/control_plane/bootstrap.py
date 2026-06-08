from __future__ import annotations

from .strategy_registry import strategy_registry


def bootstrap_control_plane() -> None:
    _ = strategy_registry

