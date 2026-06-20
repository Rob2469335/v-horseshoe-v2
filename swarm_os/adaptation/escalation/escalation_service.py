from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import json


class EscalationService:
    def __init__(self, store_path: Path | str | None = None) -> None:
        self.store_path = Path(store_path) if store_path is not None else None
        self._events: List[Dict[str, Any]] = []
        if self.store_path:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            if self.store_path.exists():
                try:
                    with open(self.store_path, 'r', encoding='utf-8') as fh:
                        self._events = json.load(fh)
                except Exception:
                    self._events = []

    def escalate(self, *, component: str, action: str, detail: str) -> Dict[str, Any]:
        evt = {
            'component': component,
            'action': action,
            'detail': detail,
        }
        self._events.append(evt)
        if self.store_path:
            with open(self.store_path, 'w', encoding='utf-8') as fh:
                json.dump(self._events, fh, indent=2)
        return evt

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        return list(self._events[-limit:])

