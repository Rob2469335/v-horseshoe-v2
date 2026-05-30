from __future__ import annotations

from typing import Any, Dict


class StateManager:
    def __init__(self) -> None:
        self._working_state: Dict[str, Any] = {}

    def get(self) -> Dict[str, Any]:
        return dict(self._working_state)

    def update(self, values: Dict[str, Any]) -> Dict[str, Any]:
        self._working_state.update(values)
        return self.get()

    def clear(self) -> None:
        self._working_state.clear()
