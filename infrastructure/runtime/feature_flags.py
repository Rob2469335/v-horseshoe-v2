from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from infrastructure.config.settings import get_settings


@dataclass(frozen=True)
class FeatureFlags:
    self_learning: bool = True
    self_healing: bool = True
    autonomous_tools: bool = False
    autonomous_vision: bool = False
    operator_approval_required: bool = True
    background_jobs: bool = True
    qdrant_enabled: bool = True
    cache_enabled: bool = True


def _load_from_file() -> dict:
    settings = get_settings()
    path = Path(settings.feature_flags_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_feature_flags() -> FeatureFlags:
    data = _load_from_file()
    return FeatureFlags(
        self_learning=bool(data.get("self_learning", True)),
        self_healing=bool(data.get("self_healing", True)),
        autonomous_tools=bool(data.get("autonomous_tools", False)),
        autonomous_vision=bool(data.get("autonomous_vision", False)),
        operator_approval_required=bool(data.get("operator_approval_required", True)),
        background_jobs=bool(data.get("background_jobs", True)),
        qdrant_enabled=bool(data.get("qdrant_enabled", True)),
        cache_enabled=bool(data.get("cache_enabled", True)),
    )


def reset_feature_flags_cache() -> None:
    get_feature_flags.cache_clear()
