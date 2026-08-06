"""opencode-style permission model for the CLI.

Every tool resolves to one of three policies:
  allow — run without asking
  ask   — prompt the user for approval
  deny  — block the action

The policy table lives in `.permissions.json` next to the session file and can
be edited with `/permissions`. An optional global `auto` mode auto-approves any
tool whose policy is `allow` or `ask` (explicit `deny` always wins), matching
opencode's `--auto` behaviour — the REPL prompt shows a muted `auto` indicator
when it is active.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

PERMISSIONS_FILE = Path(__file__).parent / ".permissions.json"

# Tool -> default policy. Mirrors opencode's permissive defaults: most things
# run, only safety-sensitive surfaces prompt.
DEFAULT_POLICIES: Dict[str, str] = {
    "read": "allow",
    "write": "allow",
    "patch": "allow",
    "grep": "allow",
    "glob": "allow",
    "web_search": "allow",
    "web_fetch": "allow",
    "sandbox_repl": "ask",
    "system": "ask",
    "screen": "ask",
    "healing": "ask",
    "approval": "ask",
    "git": "allow",
}

VALID_POLICIES = ("allow", "ask", "deny")

_lock = threading.Lock()
_cache: Optional[Dict] = None


def _load() -> Dict:
    global _cache
    if _cache is not None:
        return _cache
    data = {"auto": False, "policies": dict(DEFAULT_POLICIES)}
    try:
        if PERMISSIONS_FILE.exists():
            loaded = json.loads(PERMISSIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                merged = dict(DEFAULT_POLICIES)
                merged.update(loaded.get("policies") or {})
                data = {"auto": bool(loaded.get("auto", False)), "policies": merged}
    except Exception:
        pass
    _cache = data
    return _cache


def _save(data: Dict) -> None:
    try:
        PERMISSIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def policy_for(tool: str) -> str:
    """Return the resolved policy (allow|ask|deny) for a tool name."""
    with _lock:
        return _load()["policies"].get(tool, "ask")


def set_policy(tool: str, policy: str) -> bool:
    """Set a tool's policy; returns False on an invalid tool/policy."""
    if policy not in VALID_POLICIES or not tool:
        return False
    with _lock:
        data = _load()
        data["policies"][tool] = policy
        _save(data)
    return True


def all_policies() -> Dict[str, str]:
    with _lock:
        return dict(_load()["policies"])


def auto_mode() -> bool:
    with _lock:
        return _load()["auto"]


def set_auto_mode(enabled: bool) -> None:
    with _lock:
        data = _load()
        data["auto"] = bool(enabled)
        _save(data)


def should_ask(tool: str) -> bool:
    """True if the CLI should prompt the user before a tool runs.

    In auto mode, only explicit `deny` still asks (opencode semantics); a
    bare `ask`/`allow` is auto-approved so unattended/CI runs don't hang.
    """
    with _lock:
        data = _load()
        policy = data["policies"].get(tool, "ask")
        if data["auto"]:
            return policy == "deny"
        return policy == "ask"


def blocked(tool: str) -> bool:
    """True if the tool is hard-blocked by an explicit deny (never auto-overridable)."""
    with _lock:
        return _load()["policies"].get(tool, "ask") == "deny"


def reset() -> None:
    with _lock:
        global _cache
        _cache = {"auto": False, "policies": dict(DEFAULT_POLICIES)}
        _save(_cache)
