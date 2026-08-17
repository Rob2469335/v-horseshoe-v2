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
    done = compute_fitness(
        completion=1.0, tool_success=0.9, test_pass=0.8, efficiency=0.6
    )
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
    of.record_outcome(
        "genome_0", completion=1.0, tool_success=1.0, test_pass=1.0, efficiency=0.9
    )
    of.record_outcome(
        "genome_1", completion=0.0, tool_success=0.2, test_pass=0.0, efficiency=0.0
    )

    summary = ed.evolve_one_generation()
    # The real signal must reach the kernel (was a frozen 0.0425 elite plateau
    # when every genome scored the 0.05 prior because live outcomes are keyed
    # `agent:<id>`, never `genome_<n>`). With the shared-lineage aggregate
    # fallback, fresh children inherit the lineage's best signal, so we assert
    # the plateau is broken and the population is intact — not any one id.
    assert summary["population"] == ed.POPULATION_SIZE
    assert summary["best_fitness"] > 0.5


def test_evolution_daemon_never_raises(tmp_path, monkeypatch):
    from swarm_os.services import evolution_daemon as ed

    monkeypatch.setattr(ed, "GENOMES_PATH", tmp_path / "nope" / "genomes.jsonl")
    # Empty/missing paths -> graceful
    summary = ed.evolve_one_generation()
    assert summary["generation"] >= 0 or "error" in summary


def test_score_genome_falls_back_to_aggregate_fitness(tmp_path, monkeypatch):
    """Reviewer item #4: live outcomes are keyed `agent:<id>` which never equals
    the population's `genome_<n>` ids, so exact-ID best_fitness returns None and
    every genome scores the flat 0.05 prior (frozen population). _score_genome
    must fall back to best_aggregate_fitness() so the real signal reaches the
    kernel."""
    from swarm_os.services import evolution_daemon as ed
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(ed, "GENOMES_PATH", tmp_path / "genomes.jsonl")
    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")

    # Only agent-keyed outcomes exist (the live shape).
    of.record_outcome(
        "agent:coder", completion=1.0, tool_success=1.0, test_pass=1.0, efficiency=1.0
    )

    score = ed._score_genome({"id": "genome_12345_99"})
    assert score > 0.5  # aggregate fallback, not the 0.05 prior
    assert of.best_fitness("genome_12345_99") is None  # exact match truly absent


def test_score_genome_exact_match_takes_precedence(tmp_path, monkeypatch):
    """When a genome has a real exact-ID outcome, that must win over the
    aggregate fallback."""
    from swarm_os.services import evolution_daemon as ed
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(ed, "GENOMES_PATH", tmp_path / "genomes.jsonl")
    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")

    of.record_outcome(
        "genome_7", completion=1.0, tool_success=1.0, test_pass=1.0, efficiency=1.0
    )
    # Weak aggregate signal present too.
    of.record_outcome(
        "agent:other", completion=0.0, tool_success=0.2, test_pass=0.0, efficiency=0.0
    )

    assert ed._score_genome({"id": "genome_7"}) > 0.5


def test_score_genome_aggregate_tie_not_decayed_for_survivors(tmp_path, monkeypatch):
    """A surviving elite that only ties on the SHARED aggregate must NOT be
    decayed below a fresh child. The old code applied 0.85^n to the aggregate
    baseline too, so any elite with decay_generations >= 1 scored below every
    newborn (aggregate x 1.0) — systematically vacating the elite slots to
    random children and killing selection pressure when exact-ID records are
    sparse (the common live shape: outcomes are keyed agent:<id>)."""
    from swarm_os.services import evolution_daemon as ed
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(ed, "GENOMES_PATH", tmp_path / "genomes.jsonl")
    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")

    # Only agent-keyed outcomes exist (the live shape -> every genome ties on
    # the same aggregate).
    of.record_outcome(
        "agent:coder", completion=1.0, tool_success=1.0, test_pass=1.0, efficiency=1.0
    )

    survivor = {"id": "genome_survivor", "decay_generations": 5}
    newborn = {"id": "genome_newborn", "decay_generations": 0}

    survivor_score = ed._score_genome(survivor)
    newborn_score = ed._score_genome(newborn)
    # Both have no exact record -> both = aggregate. The survivor's 5-gen
    # survival must NOT push it below the newborn.
    assert survivor_score == newborn_score, (
        "aggregate tie must not be decayed by survivor generations — decay "
        f"applies only to exact per-genome records (got {survivor_score} vs {newborn_score})"
    )


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
        "coder",
        "m",
        "p",
        messages,
        0.0,
        "build a thing",
        True,
        state,
    )
    [e async for e in gen]

    recs = of.recent_fitness()
    assert recs, "expected a persisted outcome"
    assert recs[-1]["completion"] == 1.0
    assert recs[-1]["tool_success"] == 1.0


@pytest.mark.asyncio
async def test_feed_outcome_uses_real_test_pass(tmp_path, monkeypatch):
    """L3 (2026): when a real in-sandbox test run recorded a FAILED outcome
    (0.0) for a coder that otherwise completed cleanly, _feed_outcome must feed
    test_pass=0.0 — NOT the completion-proxy 1.0. This is the Self-Repair Trap
    fix: 'it finished' no longer stands in for 'it actually works'."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")
    service = AgentServiceV2()
    state = _CallState()
    state._turn = 4
    state._tool_attempts = 3
    state._tool_successes = 3
    # A real test run happened and FAILED, even though the run completed.
    state.test_pass_result = 0.0
    service._feed_outcome(
        "coder",
        "implement the fix",
        state,
        completed=True,
        tool_success_rate=1.0,
        turns_used=4,
        genome_id="agent:coder",
    )
    recs = of.recent_fitness()
    assert recs
    assert recs[-1]["test_pass"] == 0.0
    assert (
        recs[-1]["completion"] == 1.0
    )  # completed but tests failed -> scored as broken


@pytest.mark.asyncio
async def test_danger_room_run_tests_returns_real_exit_code(tmp_path, monkeypatch):
    """L3: DangerRoom.run_tests runs pytest in the sandbox and returns the REAL
    exit code (a rewrite that breaks a test yields exit!=0, ok=False)."""
    from swarm_os.services.danger_room import DangerRoom

    # Build a tiny sandbox with a passing and a failing test.
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_a.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_bad.py").write_text(
        "def test_fails():\n    assert 1 == 2\n", encoding="utf-8"
    )

    async def fake_setup(self):
        self.is_active = True
        self.sandbox_dir = tmp_path
        return tmp_path

    monkeypatch.setattr(DangerRoom, "setup", fake_setup)
    dr = DangerRoom(tmp_path)
    dr.is_active = True
    dr.sandbox_dir = tmp_path

    ok_res = await dr.run_tests([tmp_path / "tests" / "test_a.py"])
    assert ok_res["ok"] is True
    assert ok_res["exit_code"] == 0

    # Whole suite (includes the failing test) must fail.
    bad_res = await dr.run_tests([tmp_path / "tests"])
    assert bad_res["ok"] is False
    assert bad_res["exit_code"] != 0


@pytest.mark.asyncio
async def test_run_change_tests_no_tests_sound_file_is_discounted(
    tmp_path, monkeypatch
):
    """L3: when no related test is found AND the edited file is structurally sound
    (parses, non-empty), it must be scored 0.5 (discounted UNVERIFIED), NOT a free
    1.0 — so an unverified change can never out-compete a genuinely tested pass."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState

    service = AgentServiceV2()
    monkeypatch.setattr(AgentServiceV2, "_find_related_tests", lambda self, fp: [])
    monkeypatch.setattr(service, "_structural_verify", lambda fp: True)
    state = _CallState()
    await service._run_change_tests(state, "some/edited.py")
    assert state.test_pass_result == 0.5


@pytest.mark.asyncio
async def test_run_change_tests_no_tests_broken_file_is_zero(tmp_path, monkeypatch):
    """L3: no test found AND structural verify FAILS (broken/unparseable edit)
    must score 0.0 — the exact Self-Repair Trap hole: a mutation that breaks the
    file must never sail through as a fitness-positive."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState

    service = AgentServiceV2()
    monkeypatch.setattr(AgentServiceV2, "_find_related_tests", lambda self, fp: [])
    monkeypatch.setattr(service, "_structural_verify", lambda fp: False)
    state = _CallState()
    await service._run_change_tests(state, "some/edited.py")
    assert state.test_pass_result == 0.0


def test_structural_verify_detects_parse_and_emptiness(tmp_path):
    """L3 structural verifier (ast.parse + non-empty) must reject broken/empty
    files and accept valid ones, using the repo-root test seam."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2

    service = AgentServiceV2()
    (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    assert service._structural_verify("good.py", repo=tmp_path) is True
    assert service._structural_verify("bad.py", repo=tmp_path) is False
    assert service._structural_verify("empty.py", repo=tmp_path) is False


def test_genome_read_offloaded_to_thread():
    """The agent loop's per-run genome read (genomes.jsonl + fitness.jsonl,
    synchronous disk IO) must run via asyncio.to_thread so it never blocks the
    event loop when SWARM_EVOLUTION=1."""
    import inspect

    from runtime_v2.api import agent_service_v2 as svc

    src = inspect.getsource(svc.AgentServiceV2._step_agent_stream_inner)
    assert "asyncio.to_thread(" in src
    assert "get_active_genome" in src
    # The old blocking call is gone — the genome read is offloaded to a thread.
    assert (
        "await asyncio.to_thread(\n                    get_active_genome, True\n                )"
        in src
    )


# ---------------------------------------------------------------------------
# Agent-loop exit-path outcome audit: every TERMINAL path must feed an outcome
# to the fitness store (SWARM_EVOLUTION=1), so no failure/success is invisible.
# Covered here: premature-final ABORT, reviewer-fail ABORT, circular-recovery
# RECOVERED, and coordinator COORDINATOR_DONE. (ask_user / approval_request are
# PAUSES, not terminals — the resumed stream feeds its own outcome.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_premature_final_abort_feeds_failed_outcome(tmp_path, monkeypatch):
    """A run aborted for calling final twice without reading any files must
    feed completed=False — the failure must not be invisible to the kernel."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState
    from swarm_os.services import outcome_fitness as of
    from runtime_v2.api import _agent_config

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")
    monkeypatch.setattr(_agent_config, "ANALYSIS_AGENTS", ("code_analyzer",))

    service = AgentServiceV2()
    service._remember = AsyncMock()
    service._record_success = lambda *a, **k: None
    state = _CallState()
    state._start_time = 1.0
    state._tool_attempts = 2
    state._tool_successes = 0
    state._turn = 2
    state.premature_finals = 1  # next premature final trips the abort
    messages = []
    gen = service._handle_final(
        {"action": "final", "response": "done"},
        "code_analyzer",
        "m",
        "p",
        messages,
        0.0,
        "audit the codebase",
        False,
        state,
    )
    [e async for e in gen]

    assert state.handler_status == "ABORT"
    recs = of.recent_fitness()
    assert recs, "premature-final abort must feed an outcome"
    assert recs[-1]["completion"] == 0.0


@pytest.mark.asyncio
async def test_reviewer_fail_abort_feeds_failed_outcome(tmp_path, monkeypatch):
    """A run aborted because the reviewer failed 3x must feed completed=False."""
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
    state._turn = 2
    state.reviewer_fails = 2  # next failure trips the abort
    messages = []
    gen = service._handle_final(
        {"action": "final", "response": "FAIL: this review failed", "verdict": "FAIL"},
        "reviewer",
        "m",
        "p",
        messages,
        0.0,
        "review this patch",
        True,
        state,
    )
    [e async for e in gen]

    assert state.handler_status == "ABORT"
    recs = of.recent_fitness()
    assert recs, "reviewer-fail abort must feed an outcome"
    assert recs[-1]["completion"] == 0.0


@pytest.mark.asyncio
async def test_circular_delegation_recovered_feeds_success(tmp_path, monkeypatch):
    """A circular delegation that RECOVERS the prior result and yields a final
    must feed completed=True — a resolved success must not be invisible."""
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
    state._turn = 2
    # The recovery branch scans history for "TOOL RESULT (delegate)" +
    # "{target} responded:" — seed that shape so the match fires.
    messages = [
        {
            "role": "user",
            "content": (
                "TOOL RESULT (delegate)\ncoder responded: earlier fix result\n\n"
                "Review this result and decide the next step."
            ),
        }
    ]
    gen = service._handle_delegate(
        {"action": "delegate", "target_agent": "coder", "task": "fix it"},
        "executor",
        ["executor", "coder"],
        "m",
        "p",
        messages,
        0.0,
        "fix the bug",
        state,
    )
    [e async for e in gen]

    assert state.handler_status == "RECOVERED"
    recs = of.recent_fitness()
    assert recs, "circular-recovery must feed an outcome"
    assert recs[-1]["completion"] == 1.0


@pytest.mark.asyncio
async def test_coordinator_done_feeds_success(tmp_path, monkeypatch):
    """A coordinator that finishes a delegation and yields the child's final
    must feed completed=True — a successful coordinator run must not be
    invisible to the kernel."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")

    service = AgentServiceV2()
    service._remember = AsyncMock()
    service._record_success = lambda *a, **k: None

    # The child stream must be mocked — _handle_delegate drives a REAL
    # step_agent_stream for the delegated target, which would spawn an LLM.
    async def _fake_child_stream(*args, **kwargs):
        yield {"type": "final", "content": "child did the work"}
        yield {"type": "content", "content": "child did the work"}

    service.step_agent_stream = _fake_child_stream
    state = _CallState()
    state._start_time = 1.0
    state._tool_attempts = 2
    state._tool_successes = 2
    state._turn = 2

    captured = []
    gen = service._handle_delegate(
        {"action": "delegate", "target_agent": "coder", "task": "implement it"},
        "coordinator",
        ["coordinator"],
        "m",
        "p",
        [],
        0.0,
        "build a feature",
        state,
    )
    async for chunk in gen:
        captured.append(chunk)

    assert state.handler_status == "COORDINATOR_DONE"
    assert any(c.get("type") == "final" for c in captured)
    recs = of.recent_fitness()
    assert recs, "coordinator success must feed an outcome"
    assert recs[-1]["completion"] == 1.0

    assert service._structural_verify("nonexistent.py", repo=tmp_path) is False


@pytest.mark.asyncio
async def test_step_agent_stream_abort_feeds_failed_outcome(tmp_path, monkeypatch):
    """A client abort (GeneratorExit) or unhandled exception escaping the inner
    generator must still feed a FAILED outcome — otherwise an aborted run is
    invisible to the evolutionary kernel (the last known exit-path gap). The
    wrapper must feed completed=False with a fresh state, then re-raise."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")

    service = AgentServiceV2()

    # A delegate that aborts: the inner stream raises after the first chunk
    # (simulating a client disconnect / GeneratorExit mid-run).
    async def _abort_inner(*args, **kwargs):
        yield {"type": "content", "content": "started"}
        raise GeneratorExit()

    service._step_agent_stream_inner = _abort_inner
    gen = service.step_agent_stream("coder", "implement the fix")
    with pytest.raises(GeneratorExit):
        async for chunk in gen:
            pass

    recs = of.recent_fitness()
    assert recs, "aborted run must still feed an outcome"
    assert recs[-1]["completion"] == 0.0, "aborted run must be recorded as failed"
    assert recs[-1]["agent_id"] == "coder"


@pytest.mark.asyncio
async def test_step_agent_stream_exception_feeds_failed_outcome(tmp_path, monkeypatch):
    """An unhandled exception escaping the inner generator must also feed a
    failed outcome (not just client-abort GeneratorExit)."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")

    service = AgentServiceV2()

    async def _boom_inner(*args, **kwargs):
        yield {"type": "content", "content": "started"}
        raise RuntimeError("boom")

    service._step_agent_stream_inner = _boom_inner
    gen = service.step_agent_stream("coder", "implement the fix")
    with pytest.raises(RuntimeError):
        async for chunk in gen:
            pass

    recs = of.recent_fitness()
    assert recs, "crashed run must still feed an outcome"
    assert recs[-1]["completion"] == 0.0
    assert recs[-1]["agent_id"] == "coder"
