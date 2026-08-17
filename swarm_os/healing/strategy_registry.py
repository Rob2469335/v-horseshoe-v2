from __future__ import annotations

import json
import logging
import os
from typing import Dict, Any, Optional
from .governor_models import StrategyStats

log = logging.getLogger(__name__)


class StrategyRegistry:
    def __init__(self, path: Optional[str] = None):
        base = os.path.dirname(__file__)
        default = os.path.join(base, "_strategy_stats.json")
        self.path = path or default
        self._data: Dict[str, Any] = {}
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh) or {}
        except Exception:
            self._data = {}

    def get(self, strategy_id: str) -> StrategyStats:
        entry = self._data.get(strategy_id, {})
        return StrategyStats(
            strategy_id=strategy_id,
            success_count=entry.get("success_count", 0),
            failure_count=entry.get("failure_count", 0),
            confidence=entry.get("confidence", 0.0),
            average_duration=entry.get("average_duration", 0.0),
            approval_required_threshold=entry.get("approval_required_threshold", 0.5),
        )

    def update(self, strategy_id: str, success: bool, duration: float = 0.0):
        s = self._data.setdefault(strategy_id, {})
        if success:
            s["success_count"] = s.get("success_count", 0) + 1
        else:
            s["failure_count"] = s.get("failure_count", 0) + 1
        # simplistic confidence update
        succ = s.get("success_count", 0)
        fail = s.get("failure_count", 0)
        total = max(1, succ + fail)
        s["confidence"] = succ / total
        s["average_duration"] = (
            (s.get("average_duration", 0.0) * (total - 1) + duration) / total
            if total > 1
            else duration
        )
        self._save()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except Exception as e:
            log.warning("Failed to persist strategy win-rates: %s", e)
            pass

    def list_all(self):
        return dict(self._data)
