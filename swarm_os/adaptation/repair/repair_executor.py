from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, Dict, Tuple


class RepairExecutor:
    def __init__(self, action_map: Dict[Tuple[str, str], Callable] | None = None) -> None:
        # action_map maps (component, action) -> callable(component, action) -> (success: bool, detail: str)
        self.action_map = action_map or {}

    def execute(self, component: str, action: str):
        key = (component, action)
        if key in self.action_map:
            ok, detail = self.action_map[key](component, action)
            status = 'success' if ok else 'failed'
            return SimpleNamespace(status=status, component=component, action=action, detail=detail)
        # default success
        return SimpleNamespace(status='success', component=component, action=action, detail='repair executed')

