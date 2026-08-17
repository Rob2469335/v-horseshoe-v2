"""Online win-rate routing for analysis agents (opt-in, Item #8).

The legacy `_llm_client.get_litellm_model` pushed analysis agents to cloud for
every tool decision whenever a key was present. This module makes that routing
data-driven: we persist per-agent success/failure counts for the analysis agent
and only keep the cloud hop while its tracked win-rate stays above a floor.
Repeated failures decay it back to local, doubling down on the strategy_stats
pattern already used by the Governor.

Design:
  - Saved to a small JSON file so win-rates survive restarts.
  - `cloud_allowed_for_agent(agent_id)` is consulted before the cloud hop.
  - `record_analysis_outcome(agent_id, ok)` is called after each decision.
  - Off by default (SWARM_WINRATE_ROUTING=1 to enable) to preserve current
    behavior; any I/O error defaults to the legacy "allow" rule (never blocks).
"""

from __future__ import annotations

import json
import logging
import os
import threading

log = logging.getLogger(__name__)

_STORE_PATH = os.environ.get(
    "SWARM_WINRATE_PATH",
    os.path.join(os.path.dirname(__file__), "_agent_winrates.json"),
)
_MIN_SAMPLES = int(os.environ.get("SWARM_WINRATE_MIN_SAMPLES", "5"))
_FLOOR = float(os.environ.get("SWARM_WINRATE_FLOOR", "0.5"))
_WINDOW = int(os.environ.get("SWARM_WINRATE_WINDOW", "30"))

_lock = threading.Lock()
_data: dict = {}


def _enabled() -> bool:
    return os.environ.get("SWARM_WINRATE_ROUTING", "0") == "1"


def _load():
    global _data
    try:
        if os.path.exists(_STORE_PATH):
            with open(_STORE_PATH, "r", encoding="utf-8") as fh:
                _data = json.load(fh) or {}
    except Exception as e:  # noqa: BLE001
        log.debug("winrate store load failed: %s", e)
        _data = {}


def _save():
    try:
        tmp = _STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_data, fh, indent=2)
        os.replace(tmp, _STORE_PATH)
    except Exception as e:  # noqa: BLE001
        log.debug("winrate store save failed: %s", e)


def _entry(agent_id: str) -> dict:
    if not _data:
        _load()
    with _lock:
        return _data.setdefault(agent_id, {"success": 0, "failure": 0})


def cloud_allowed_for_agent(agent_id: str) -> bool:
    """True = keep the cloud hop for this agent. Below min-samples (or disabled)
    we defer to the legacy rule (allow) so nothing changes out of the box."""
    if not _enabled():
        return True
    e = _entry(agent_id)
    total = e.get("success", 0) + e.get("failure", 0)
    if total < _MIN_SAMPLES:
        return True
    winrate = e.get("success", 0) / total
    allowed = winrate >= _FLOOR
    if not allowed:
        log.info(
            "[winrate] %s win-rate %.0f%% below floor %.0f%% — routing analysis back to local",
            agent_id,
            winrate * 100,
            _FLOOR * 100,
        )
    return allowed


def record_analysis_outcome(agent_id: str, ok: bool) -> None:
    """Record one analysis outcome so future routing reflects real success."""
    if not _enabled():
        return
    e = _entry(agent_id)
    with _lock:
        if ok:
            e["success"] = e.get("success", 0) + 1
        else:
            e["failure"] = e.get("failure", 0) + 1
        total = e["success"] + e["failure"]
        if total > _WINDOW:
            drop = total - _WINDOW
            e["success"] = max(0, e["success"] - drop)
            e["failure"] = max(0, e["failure"] - drop)
    _save()
