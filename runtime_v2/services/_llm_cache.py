"""Decision cache for LLM tool-call responses to reduce redundant API calls."""
import hashlib
import json
import logging
from typing import Optional
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

_decision_cache: dict[str, tuple[dict, datetime]] = {}
_cache_ttl = 300


def get_cache_key(messages: list, agent_id: str) -> str:
    if messages:
        last_msg = messages[-1].get("content", "")
        if not isinstance(last_msg, str):
            last_msg = json.dumps(last_msg)
        h = hashlib.sha256(last_msg.encode("utf-8")).hexdigest()
        return f"{agent_id}:{h}"
    return f"{agent_id}:default"


def get_cached_decision(cache_key: str) -> Optional[dict]:
    if cache_key in _decision_cache:
        decision, timestamp = _decision_cache[cache_key]
        if datetime.now() - timestamp < timedelta(seconds=_cache_ttl):
            return decision
        del _decision_cache[cache_key]
    return None


def cache_decision(cache_key: str, decision: dict):
    if len(_decision_cache) > 500:
        keys_to_remove = list(_decision_cache.keys())[:100]
        for k in keys_to_remove:
            _decision_cache.pop(k, None)
    _decision_cache[cache_key] = (decision, datetime.now())
