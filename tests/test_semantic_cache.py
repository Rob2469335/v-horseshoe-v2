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


@pytest.mark.asyncio
async def test_final_decision_is_never_served_from_cache():
    # A cached terminal `final` must never be replayed: once the L1 gate rejects
    # it, replaying it spins the turn loop to the budget (observed live: 9
    # forced-final turns in <1s serving one cached final).
    from runtime_v2.services import _semantic_decision_cache as m

    m._decision_cache.clear()
    msgs = [{"role": "user", "content": "forced-final context turn text"}]
    key = m.get_cache_key(msgs, "code_analyzer")
    m._put_exact(key, {"action": "final", "response": "done"})
    with patch.dict(os.environ, {"SWARM_SEMANTIC_CACHE": "1"}, clear=False):
        out = await m.get_semantic_cached_decision(msgs, "code_analyzer")
    assert out is None


@pytest.mark.asyncio
async def test_final_decision_is_never_stored():
    # cache_tool_decision drops terminal `final` decisions so they can't later be
    # served by the exact or semantic path. Revert-proof: pre-fix the exact entry
    # was stored before the (now-skipped) Qdrant write.
    from runtime_v2.services import _semantic_decision_cache as m

    m._decision_cache.clear()
    msgs = [{"role": "user", "content": "an analysis turn worth caching"}]
    with patch.dict(os.environ, {"SWARM_SEMANTIC_CACHE": "1"}, clear=False):
        await m.cache_tool_decision(msgs, "code_analyzer", {"action": "final", "response": "done"})
    assert m._get_exact(m.get_cache_key(msgs, "code_analyzer")) is None


@pytest.mark.asyncio
async def test_prune_stale_decisions_deletes_only_missing_paths(monkeypatch):
    # The self-healing GC must delete a cached decision that references a deleted
    # file (it re-poisons the loop) and keep one that references a live file.
    from types import SimpleNamespace
    from runtime_v2.services import _semantic_decision_cache as m

    stale = SimpleNamespace(
        id="stale-1",
        payload={"decision": {"action": "filesystem", "operation": "read",
                              "path": "does_not_exist_zzz.txt"}},
    )
    live = SimpleNamespace(
        id="live-1",
        payload={"decision": {"action": "filesystem", "operation": "read",
                              "path": "README.md"}},
    )
    deleted = {}

    class FakeClient:
        async def scroll(self, **kw):
            return ([stale, live], None)

        async def delete(self, collection_name=None, points_selector=None):
            deleted["ids"] = list(points_selector)

    async def _noop():
        return None

    monkeypatch.setattr(m, "_ensure_components", _noop)
    monkeypatch.setattr(m, "_client", FakeClient())
    with patch.dict(os.environ, {"SWARM_SEMANTIC_CACHE": "1"}, clear=False):
        res = await m.prune_stale_decisions()
    assert res["ok"] is True and res["stale"] == 1 and res["deleted"] == 1
    assert deleted["ids"] == ["stale-1"]


@pytest.mark.asyncio
async def test_prune_stale_decisions_reports_failure(monkeypatch):
    # A scroll error must return ok:False so the daemon logs a WARNING instead of
    # silently no-op'ing (a broken GC is worse than none).
    from runtime_v2.services import _semantic_decision_cache as m

    class BadClient:
        async def scroll(self, **kw):
            raise RuntimeError("boom")

    async def _noop():
        return None

    monkeypatch.setattr(m, "_ensure_components", _noop)
    monkeypatch.setattr(m, "_client", BadClient())
    with patch.dict(os.environ, {"SWARM_SEMANTIC_CACHE": "1"}, clear=False):
        res = await m.prune_stale_decisions()
    assert res["ok"] is False
