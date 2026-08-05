# swarm_os/app/services/learning_service.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

class LearningService:
    def __init__(self, store_path: Path | str | None = None) -> None:
        self.store_path = Path(store_path) if store_path is not None else None
        self._outcomes: List[Dict[str, Any]] = []
        self._repairs: Dict[str, List[Dict[str, Any]]] = {}
        self._stats: Dict[str, Dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if self.store_path and self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    self._outcomes = data.get("outcomes", [])
                    self._repairs = data.get("repairs", {})
                    self._stats = data.get("stats", {})
            except Exception:
                pass

    def _save(self) -> None:
        if self.store_path:
            try:
                self.store_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.store_path, "w", encoding="utf-8") as fh:
                    json.dump({
                        "outcomes": self._outcomes,
                        "repairs": self._repairs,
                        "stats": self._stats
                    }, fh, default=str, indent=2)
            except Exception as e:
                log.warning("Failed to persist learning outcomes: %s", e)
                pass

    def ingest_outcome(self, outcome: Any) -> dict[str, str]:
        if isinstance(outcome, dict):
            outcome_id = outcome.get("outcome_id")
            component = outcome.get("component")
            status = outcome.get("status")
        else:
            outcome_id = getattr(outcome, "outcome_id", None)
            component = getattr(outcome, "component", None)
            status = getattr(outcome, "status", None)

        evt = {
            "outcome_id": outcome_id,
            "component": component,
            "status": status,
        }
        self._outcomes.append(evt)

        if component:
            if component not in self._stats:
                self._stats[component] = {"failures": 0, "successes": 0}
            if "successes" not in self._stats[component]:
                self._stats[component]["successes"] = 0
            if "failures" not in self._stats[component]:
                self._stats[component]["failures"] = 0

            if status == "failed":
                self._stats[component]["failures"] += 1
            elif status == "success":
                self._stats[component]["successes"] += 1

        self._save()
        return {
            "outcome_id": outcome_id or "",
            "status": "accepted",
        }

    def list_outcomes(self) -> List[Dict[str, Any]]:
        return list(self._outcomes)

    def record_repair(
        self,
        component: str,
        action: str,
        success: bool | None = None,
        result: str | None = None,
        reason: str | None = None
    ) -> None:
        if success is None and result is not None:
            success = (result == "success" or result is True)
        elif success is not None and result is None:
            result = "success" if success else "failed"
        elif success is None and result is None:
            success = True
            result = "success"

        if component not in self._repairs:
            self._repairs[component] = []
        self._repairs[component].append({
            "action": action,
            "success": success,
            "result": result,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if component not in self._stats:
            self._stats[component] = {"failures": 0, "successes": 0}
        if "successes" not in self._stats[component]:
            self._stats[component]["successes"] = 0
        if "failures" not in self._stats[component]:
            self._stats[component]["failures"] = 0

        if success:
            self._stats[component]["successes"] += 1
        else:
            self._stats[component]["failures"] += 1

        self._save()

    def get_component_profile(self, component: str) -> Dict[str, Any]:
        stats = self._stats.get(component, {"failures": 0, "successes": 0})
        if "successes" not in stats:
            stats["successes"] = 0
        if "failures" not in stats:
            stats["failures"] = 0
        repairs = self._repairs.get(component, [])
        return {
            "component": component,
            "stats": stats,
            "recent_repairs": repairs,
        }
