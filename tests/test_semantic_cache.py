"""Tests for the opt-in semantic decision cache (cost optimization).

When SWARM_SEMANTIC_CACHE=1, near-duplicate tool decisions short-circuit the
LLM — the biggest lever for reducing per-call token spend. Exact SHA-256 keys
(zero false positives) hit an in-process LRU; semantic Qdrant lookup is
threshold-gated; all failures degrade to a miss (never blocks the LLM).
"""

from __future__ import annotations
import os
import pytest
from unittest.mock import patch


def test_cache_disabled_by_default():
    from runtime_v2.services._semantic_decision_cache import _enabled

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SWARM_SEMANTIC_CACHE", None)
        assert _enabled() is False


def test_cache_enabled_via_env():
    from runtime_v2.services._semantic_decision_cache import _enabled

    with patch.dict(os.environ, {"SWARM_SEMANTIC_CACHE": "1"}, clear=False):
        assert _enabled() is True


def test_exact_key_is_stable_and_agent_scoped():
    from runtime_v2.services._semantic_decision_cache import get_cache_key

    msgs = [{"role": "user", "content": "list files in src"}]
    k1 = get_cache_key(msgs, "coder")
    k2 = get_cache_key(list(msgs), "coder")
    k3 = get_cache_key(msgs, "reviewer")
    assert k1 == k2
    assert k1 != k3  # agent-scoped


@pytest.mark.asyncio
async def test_exact_cache_hit_short_circuits():
    from runtime_v2.services import _semantic_decision_cache as m

    msgs = [{"role": "user", "content": "analyze the codebase for bugs"}]
    decision = {"action": "filesystem", "operation": "glob", "path": "runtime_v2"}
    key = m.get_cache_key(msgs, "code_analyzer")
    m._put_exact(key, decision)
    with patch.dict(os.environ, {"SWARM_SEMANTIC_CACHE": "1"}, clear=False):
        out = await m.get_semantic_cached_decision(msgs, "code_analyzer")
    assert out == decision


@pytest.mark.asyncio
async def test_cache_miss_degrades_to_none():
    """A message with no stored decision returns None (falls through to the LLM)
    and never raises, even with the cache enabled and services offline."""
    from runtime_v2.services import _semantic_decision_cache as m

    msgs = [{"role": "user", "content": "something totally unique 42"}]
    with patch.dict(os.environ, {"SWARM_SEMANTIC_CACHE": "1"}, clear=False):
        out = await m.get_semantic_cached_decision(msgs, "coder")
    assert out is None
