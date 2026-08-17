from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)


@dataclass
class HealingMetrics:
    store_path: Path | str | None = None
    totals: Dict[str, int] = field(
        default_factory=lambda: {
            "attempts": 0,
            "executed": 0,
            "verified_failure": 0,
            "escalations": 0,
        }
    )
    events: list[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.store_path is not None:
            p = Path(self.store_path)
            self._path = p
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        self.totals.update(data.get("totals", {}))
                        self.events.extend(data.get("events", []))
                except Exception:
                    pass
            else:
                # ensure file exists
                try:
                    with open(p, "w", encoding="utf-8") as fh:
                        json.dump({"totals": self.totals, "events": self.events}, fh)
                except Exception:
                    pass

    def _save(self) -> None:
        if getattr(self, "_path", None) is None:
            return
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump({"totals": self.totals, "events": self.events}, fh)
        except Exception as e:
            log.warning("Failed to persist healing metrics: %s", e)
            pass

    def record(
        self,
        *,
        component: str,
        action: str,
        executed: bool,
        verified: bool,
        escalated: bool,
    ) -> None:
        self.totals["attempts"] += 1
        if executed:
            self.totals["executed"] += 1
        if not verified:
            self.totals["verified_failure"] += 1
        if escalated:
            self.totals["escalations"] += 1
        self.events.append(
            {
                "component": component,
                "action": action,
                "executed": executed,
                "verified": verified,
                "escalated": escalated,
            }
        )
        # persist metrics
        try:
            self._save()
        except Exception as e:
            log.warning("Failed to persist healing metrics: %s", e)
            pass

    def snapshot(self) -> Dict[str, Any]:
        return {"totals": dict(self.totals), "recent": list(self.events[-20:])}
