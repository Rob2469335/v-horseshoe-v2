"""Tests for the genetic mutation loop's provider-liveness fail-fast.

Regression 2026-08-24: with the whole cloud chain dead (401/402/usage-capped),
run_genetic_mutation burned 3 x 90s retries per hourly tick and recorded
"failure" each time, tripping the 3-consecutive-failure Extinction Event —
"halting" evolution over pure infra downtime. The loop must SKIP the cycle
(without recording a failure) when no live cloud provider exists.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from swarm_os.services import genetic_mutation_loop as gm


TARGET_FUNC = "def get_agent():\n    return 'x'\n"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolate disk + LLM seams so the test never touches real state."""
    monkeypatch.setattr(gm, "HISTORY_FILE", tmp_path / "mutation_history.json")
    monkeypatch.setattr(gm, "PENDING_MUTATION_DIR", tmp_path / "pending_mutations")

    target = tmp_path / "agent_service_v2.py"
    target.write_text(TARGET_FUNC, encoding="utf-8")

    class _FakeBridge:
        def __init__(self):
            self.added = []

        async def query_routing_hint(self, *a, **k):
            return {"weight": 1.0}

        async def get_memory_context(self, *a, **k):
            return ""

        def _add(self, ev):
            self.added.append(ev)

        async def _flush(self):
            pass

    monkeypatch.setattr(gm, "MemoryBridge", _FakeBridge)
    return {"target": str(target), "history": tmp_path / "mutation_history.json"}


def test_dead_chain_skips_cycle_without_llm_call_or_failure(isolated, monkeypatch):
    """No live cloud fallback + cloud primary -> return BEFORE any LLM call,
    and WITHOUT appending a 'failure' (which would trip extinction)."""
    from runtime_v2.services import fallback_manager as fm

    async def _empty(mode="auto"):
        return []

    monkeypatch.setattr(fm, "get_live_fallbacks", _empty)

    llm = AsyncMock(side_effect=AssertionError("LLM must not be called"))
    monkeypatch.setattr(gm, "acompletion", llm)

    asyncio.run(gm.run_genetic_mutation(isolated["target"]))

    assert llm.await_count == 0
    # No failure recorded -> extinction counter untouched.
    if isolated["history"].exists():
        assert "failure" not in isolated["history"].read_text(encoding="utf-8")


def test_live_chain_still_runs_the_loop(isolated, monkeypatch):
    """When a cloud provider IS live, the pre-check must not skip: the LLM is
    called (and its garbage output drives the normal retry/halt path)."""
    from runtime_v2.services import fallback_manager as fm

    async def _live(mode="auto"):
        return [{"model": "gemini/gemini-2.5-flash"}]

    monkeypatch.setattr(fm, "get_live_fallbacks", _live)

    llm = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="```python\npass\n```"))
            ]
        )
    )
    monkeypatch.setattr(gm, "acompletion", llm)
    monkeypatch.setattr(gm, "_find_related_test_files", lambda p: [], raising=False)

    asyncio.run(gm.run_genetic_mutation(isolated["target"]))

    assert llm.await_count >= 1
