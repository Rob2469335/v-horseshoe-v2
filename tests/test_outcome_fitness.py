"""Tests for the SOTA outcome-driven self-learning loop.

Covers:
1. Outcome fitness computation (research-grounded composite, completion gating).
2. Evolution generation selects elites on REAL persisted fitness and breeds.
3. The agent loop feeds real outcomes (completion/tool-success) to the store.
"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock


def test_compute_fitness_completion_gating():
    from swarm_os.services.outcome_fitness import compute_fitness
    unfinished = compute_fitness(completion=0.0, tool_success=0.9)
    done = compute_fitness(completion=1.0, tool_success=0.9, test_pass=0.8, efficiency=0.6)
    assert unfinished["composite"] <= 0.4  # gated
    assert done["composite"] > unfinished["composite"]
    assert done["composite"] > 0.5


def test_record_outcome_persists(tmp_path, monkeypatch):
    from swarm_os.services import outcome_fitness as of
    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    fitness = of.record_outcome("g1", completion=1.0, tool_success=0.8, test_pass=1.0)
    assert fitness["composite"] > 0.5
    assert of.best_fitness("g1") == fitness["composite"]
    assert of.best_fitness("missing") is None


def test_evolution_selects_elite_on_real_fitness(tmp_path, monkeypatch):
    from swarm_os.services import outcome_fitness as of
    from swarm_os.services import evolution_daemon as ed
    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setattr(ed, "GENOMES_PATH", tmp_path / "genomes.jsonl")

    ed.evolve_one_generation()  # seed population
    of.record_outcome("genome_0", completion=1.0, tool_success=1.0, test_pass=1.0, efficiency=0.9)
    of.record_outcome("genome_1", completion=0.0, tool_success=0.2, test_pass=0.0, efficiency=0.0)

    summary = ed.evolve_one_generation()
    assert "genome_0" in summary["elites"]  # best real fitness kept
    assert summary["population"] == ed.POPULATION_SIZE
    assert summary["best_fitness"] > 0.5


def test_evolution_daemon_never_raises(tmp_path, monkeypatch):
    from swarm_os.services import evolution_daemon as ed
    monkeypatch.setattr(ed, "GENOMES_PATH", tmp_path / "nope" / "genomes.jsonl")
    # Empty/missing paths -> graceful
    summary = ed.evolve_one_generation()
    assert summary["generation"] >= 0 or "error" in summary


@pytest.mark.asyncio
async def test_handle_final_feeds_real_outcome(tmp_path, monkeypatch):
    """The live final handler must feed a completed outcome to the fitness store
    when SWARM_EVOLUTION=1 (and no-op otherwise)."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")

    service = AgentServiceV2()
    service._remember = AsyncMock()
    service._record_success = lambda *a, **k: None
    state = _CallState()
    state._start_time = 1.0
    state._tool_attempts = 2
    state._tool_successes = 2
    state._turn = 3
    messages = []
    gen = service._handle_final(
        {"action": "final", "response": "done"},
        "coder", "m", "p", messages, 0.0, "build a thing", True, state,
    )
    [e async for e in gen]

    recs = of.recent_fitness()
    assert recs, "expected a persisted outcome"
    assert recs[-1]["completion"] == 1.0
    assert recs[-1]["tool_success"] == 1.0
