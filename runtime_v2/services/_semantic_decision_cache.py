"""Semantic decision cache for the tool-decision loop (opt-in).

Implements the 2026 hybrid exact->semantic cache recommendation. On each
get_tool_decision() call we:
  1. compute an exact SHA-256 key of the final user message and look it up in a
     small in-process LRU (zero false positives);
  2. on exact miss, embed the query (nomic on :8081) and search a Qdrant
     "decision_cache" collection, returning the stored decision only when the
     cosine similarity clears a tuned threshold (defense against false positives);
  3. on a semantic miss, fall through to the LLM and write the result back so the
     next near-duplicate request short-circuits.

It is OFF by default (env SWARM_SEMANTIC_CACHE=1 to enable) because the earlier
pure-exact in-dict cache was deliberately disabled. All failures degrade to
"no hit" - this layer never blocks the real LLM decision.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_decision_cache: dict[str, tuple[dict, datetime]] = {}
_cache_ttl = 300
_max_cache_entries = 500
_collection = "decision_cache"

# Lazy singletons.
_client = None
_embedder = None

# Metric counters (exposed via decision_cache_stats()).
_stats = {"hits": 0, "semantic_hits": 0, "misses": 0, "errors": 0, "lookups": 0}

# Cosine threshold above which a candidate is treated as a safe semantic hit.
SEMANTIC_THRESHOLD = float(os.environ.get("SWARM_SEMANTIC_CACHE_THRESHOLD", "0.85"))

def _contains_secrets(text: str) -> bool:
    """Return True if the text contains patterns that look like obvious secrets."""
    import re
    # Match common secret prefixes or formats (e.g. Bearer tokens, API keys)
    # This is a lightweight heuristic as recommended by SOTA to bypass cache for sensitive data.
    secret_patterns = [
        r"Bearer\s+[a-zA-Z0-9_\-\.]+",
        r"sk-[a-zA-Z0-9]{20,}",
        r"AKIA[0-9A-Z]{16}",
    ]
    return any(re.search(p, text) for p in secret_patterns)


def _enabled() -> bool:
    return os.environ.get("SWARM_SEMANTIC_CACHE", "0") == "1"


def decision_cache_stats() -> dict:
    return dict(_stats)


def get_cache_key(messages: list, agent_id: str) -> str:
    """Exact-match key: SHA-256 of the final user content scoped by agent."""
    if messages:
        last_msg = messages[-1].get("content", "")
        if not isinstance(last_msg, str):
            last_msg = json.dumps(last_msg)
        h = hashlib.sha256(last_msg.encode("utf-8")).hexdigest()
        return f"{agent_id}:{h}"
    return f"{agent_id}:default"


def _get_exact(cache_key: str) -> Optional[dict]:
    entry = _decision_cache.get(cache_key)
    if not entry:
        return None
    decision, timestamp = entry
    if datetime.now() - timestamp < timedelta(seconds=_cache_ttl):
        return decision
    del _decision_cache[cache_key]
    return None


def _put_exact(cache_key: str, decision: dict):
    if len(_decision_cache) > _max_cache_entries:
        stale = sorted(_decision_cache, key=lambda k: _decision_cache[k][1])[:100]
        for k in stale:
            _decision_cache.pop(k, None)
    _decision_cache[cache_key] = (decision, datetime.now())


def _hash_point_id(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return "".join(c for c in digest if c.isalnum())[:32] or "0" * 32


async def _ensure_components():
    """Lazily wire Qdrant + embedder so enablement needs no extra setup."""
    global _client, _embedder
    if _client is not None:
        return
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import VectorParams, Distance
    from swarm_os.services.embedding_service import EmbeddingService

    _embedder = EmbeddingService()
    _client = AsyncQdrantClient(url="http://127.0.0.1:6333")
    try:
        collections = (await _client.get_collections()).collections
        if not any(c.name == _collection for c in collections):
            await _client.create_collection(
                collection_name=_collection,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )
            log.info("Created decision cache collection '%s'", _collection)
    except Exception as e:  # noqa: BLE001
        log.warning("decision cache init failed: %s", e)


def _last_user_text(messages: list) -> str:
    if not messages:
        return ""
    last_msg = messages[-1].get("content", "") if messages else ""
    if not isinstance(last_msg, str):
        last_msg = json.dumps(last_msg)
    return last_msg


async def get_semantic_cached_decision(messages: list, agent_id: str) -> Optional[dict]:
    """Return a cached decision for these messages or None. Exact match first,
    then semantic over Qdrant. Never raises - failures degrade to a miss."""
    if not _enabled():
        return None
    _stats["lookups"] += 1

    cache_key = get_cache_key(messages, agent_id)
    cached = _get_exact(cache_key)
    if cached is not None:
        _stats["hits"] += 1
        return cached

    last_msg = _last_user_text(messages)
    if not last_msg or len(last_msg.strip()) < 10:
        return None

    try:
        await _ensure_components()
        query = f"agent:{agent_id} decision {last_msg[:400]}"
        emb = await _embedder.embed(query)
        resp = await _client.query_points(
            collection_name=_collection,
            query=emb,
            limit=1,
        )
        points = getattr(resp, "points", resp)
        if not points:
            _stats["misses"] += 1
            return None
        top = points[0]
        if float(top.score) < SEMANTIC_THRESHOLD:
            _stats["misses"] += 1
            return None
        decision = top.payload.get("decision")
        if isinstance(decision, dict) and decision.get("action"):
            _stats["semantic_hits"] += 1
            _put_exact(cache_key, decision)
            return decision
    except Exception as e:
        _stats["errors"] += 1
        log.debug("semantic cache lookup degraded to miss: %s", e)
    _stats["misses"] += 1
    return None


async def cache_tool_decision(messages: list, agent_id: str, decision: dict):
    """Record a decision so repeated requests short-circuit. Failures non-fatal."""
    if not _enabled():
        return
    try:
        last_msg = _last_user_text(messages)
        if _contains_secrets(str(decision)) or (last_msg and _contains_secrets(last_msg)):
            log.debug("Skipping semantic cache write: obvious secret detected")
            return

        cache_key = get_cache_key(messages, agent_id)
        _put_exact(cache_key, decision)

        if not last_msg or len(last_msg.strip()) < 10:
            return

        await _ensure_components()
        emb = await _embedder.embed(f"agent:{agent_id} decision: {last_msg[:400]}".rstrip())
        from qdrant_client.models import PointStruct
        from datetime import datetime as _dt

        await _client.upsert(
            collection_name=_collection,
            points=[PointStruct(
                id=_hash_point_id(cache_key),
                vector=emb,
                payload={
                    "agent_id": agent_id,
                    "decision": decision,
                    "ts": _dt.utcnow().isoformat(),
                },
            )],
            wait=False,
        )
    except Exception as exc:
        log.debug("decision cache write failed (non-fatal): %s", exc)