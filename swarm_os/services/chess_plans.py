"""Persistent "current plan" state (2026 SOTA — the anti-drift mechanism).

The research's top planning feature: a plan that is CARRIED FORWARD, only
regenerating when a trigger actually changes (their king becomes exposed, a
pawn becomes weak, material changes, a file opens, an outpost appears).
Otherwise the SAME plan persists and marks progress ("still: get the rook to
c1"). This teaches "have a plan and follow it" instead of drifting move to
move.

The plan is derived from coach_plan's standard-plans menu (machine-detectable
triggers), so it's concrete and checkable — never vague.

State is per-game: reset when a new game starts; advanced after each move via
the move's post-move coach plan.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
# game_id -> {plan_key, plan_name, recipe, trigger, unchanged_moves}
_state: dict[str, dict[str, Any]] = {}


def reset(game_id: str) -> None:
    with _LOCK:
        _state.pop(game_id, None)


def advance(game_id: str, coach: dict[str, Any] | None) -> dict[str, Any]:
    """Advance the persistent plan from a coach_plan result (post-move). Returns
    the CURRENT plan state. If the trigger is unchanged from the previous move,
    the plan persists with unchanged_moves incremented; if it changed, a new
    plan starts."""
    if not coach or not coach.get("ok"):
        return {"ok": True, "persisted": True, "plan": None, "unchanged_moves": 0}
    std = coach.get("standard_plan") or {}
    key = std.get("key") or "consolidate"
    with _LOCK:
        prev = _state.get(game_id)
        if prev and prev.get("plan_key") == key:
            prev["unchanged_moves"] = prev.get("unchanged_moves", 0) + 1
            _state[game_id] = prev
            return {
                "ok": True,
                "persisted": True,
                "plan": {
                    "name": prev.get("plan_name"),
                    "recipe": prev.get("recipe"),
                    "trigger": prev.get("trigger"),
                },
                "unchanged_moves": prev["unchanged_moves"],
            }
        fresh = {
            "plan_key": key,
            "plan_name": std.get("name", "Consolidate"),
            "recipe": std.get("recipe", ""),
            "trigger": std.get("trigger", ""),
            "unchanged_moves": 0,
        }
        _state[game_id] = fresh
        return {
            "ok": True,
            "persisted": False,
            "plan": {
                "name": fresh["plan_name"],
                "recipe": fresh["recipe"],
                "trigger": fresh["trigger"],
            },
            "unchanged_moves": 0,
        }


def current(game_id: str) -> dict[str, Any]:
    with _LOCK:
        st = _state.get(game_id)
        if not st:
            return {"ok": True, "plan": None, "unchanged_moves": 0}
        return {
            "ok": True,
            "plan": {
                "name": st.get("plan_name"),
                "recipe": st.get("recipe"),
                "trigger": st.get("trigger"),
            },
            "unchanged_moves": st.get("unchanged_moves", 0),
        }
