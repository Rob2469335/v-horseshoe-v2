"""Time-boxed, scoped trust grants — the SAFE subset of dynamic autonomy.

A user may grant temporary autonomy for a tool/action scope ("trust web_fetch
for 30 min"); the grant relaxes ONLY the CONFIRM tier (never ALWAYS_CONFIRM or
DENY) and expires automatically (evaluated at read time). With no grant, the
permission behaviour is exactly today's static policy — fail-closed by
construction.

Design grounding: time-boxed trust escalation with automatic revocation
(ironclaw#1814 / HumanLayer `DangerouslySkipPermissions` + `PermissionMonitor`);
risk-adaptive access control (Hedwig arXiv:2605.11495; TBAC arXiv:2510.11414);
safe/secure defaults (arXiv:2412.17329) — the relaxation is opt-in and narrow.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_GRANTS_PATH = Path(os.getenv("SWARM_TRUST_GRANTS", "data/trust_grants.json"))
_lock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(_GRANTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    try:
        _GRANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _GRANTS_PATH.with_name(f"{_GRANTS_PATH.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, _GRANTS_PATH)
    except Exception as e:  # noqa: BLE001
        log.warning("trust grant persist failed: %s", e)


def _norm_scope(scope: str) -> str:
    return (scope or "").strip().lower()


def grant(scope: str, ttl_seconds: int) -> dict:
    """Grant trust for ``scope`` ("tool" or "tool:action") for ``ttl_seconds``."""
    s = _norm_scope(scope)
    ttl = max(1, int(ttl_seconds))
    with _lock:
        data = _load()
        data[s] = {"expires_at": time.time() + ttl, "ttl": ttl}
        _save(data)
    return data[s]


def revoke(scope: str) -> bool:
    s = _norm_scope(scope)
    with _lock:
        data = _load()
        existed = data.pop(s, None) is not None
        _save(data)
    return existed


def list_grants() -> dict:
    """Active (non-expired) grants only."""
    now = time.time()
    return {s: g for s, g in _load().items() if g.get("expires_at", 0) > now}


def is_trusted(tool: str, action: Optional[str] = None) -> bool:
    """True iff an ACTIVE grant covers this tool (or ``tool:action``).

    Expired grants are ignored (auto-revocation). A tool-level grant covers all
    of that tool's actions; a ``tool:action`` grant covers only that action.
    """
    t = (tool or "").strip().lower()
    a = (action or "").strip().lower()
    if not t:
        return False
    now = time.time()
    grants = _load()
    for scope in ((f"{t}:{a}" if a else None), t):
        if not scope:
            continue
        g = grants.get(scope)
        if g and g.get("expires_at", 0) > now:
            return True
    return False
