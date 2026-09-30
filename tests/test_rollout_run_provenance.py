"""Lane-2 rollout_id/run_id provenance contract tests.

These tests exercise the real production seam: the harness rollout context
(``ROLLOUT_ID_CTX``) is set, then the authoritative backend producers read it
INTERNALLY via ``_current_rollout_id()`` (they are never handed the value as an
argument), and the resulting durable artifacts are read back from disk. This is
deliberately NOT a hand-fed-dict-then-final-function test: nothing here calls
the producers with a rollout argument, so a regression in the context → persist
→ retrieve chain fails these tests.
"""
from __future__ import annotations

import json

import pytest

from runtime_v2.api import agent_service_v2 as agent
from runtime_v2.services.stream_runner import ROLLOUT_ID_CTX


@pytest.fixture()
def rollout_ctx():
    """Set a real harness rollout context for the duration of a test."""
    token = ROLLOUT_ID_CTX.set("rollout-R-1234")
    try:
        yield "rollout-R-1234"
    finally:
        ROLLOUT_ID_CTX.reset(token)


@pytest.fixture()
def svc(tmp_path):
    """A real AgentServiceV2 (no LLM), trajectory dir pointing at tmp."""
    service = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    service._TRAJ_DIR = tmp_path
    return service


class FakeEventStore:
    """Minimal event store capturing the appended EventEnvelopes."""

    def __init__(self):
        self.appended = []

    def append(self, envelope):
        self.appended.append(envelope)


def _summary(svc, run_id):
    """Read the last (summary) record of a run's trajectory file."""
    lines = [
        ln
        for ln in (svc._TRAJ_DIR / f"{run_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if ln.strip()
    ]
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# 1. Trajectory provenance
# ---------------------------------------------------------------------------

def test_run_trajectory_persists_rollout_id(tmp_path, rollout_ctx):
    """ROLLOUT_ID_CTX → _write_run_trajectory (reads ctx internally) → disk."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    svc._write_run_trajectory(
        run_id="run-A",
        agent_id="coder",
        parent_id="",
        delegated_by="",
        prompt="fix the twine provides_extra bug",
        genome_id="",
        last_chunk={"action": "final", "content": "patched"},
    )
    rec = _summary(svc, "run-A")
    assert rec["run_id"] == "run-A"
    assert rec["rollout_id"] == rollout_ctx


def test_trajectory_steps_do_not_carry_rollout(tmp_path, rollout_ctx):
    """Lane 2 only annotates the run SUMMARY (the joinable record); ATIF tool
    steps keep their schema shape (rollout is not a step field)."""
    service = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    service._TRAJ_DIR = tmp_path
    state = agent._CallState()
    state.run_id = "run-A"
    service._write_run_step(
        run_id="run-A",
        agent_id="coder",
        model="robs4b",
        decision={"action": "filesystem", "operation": "read", "path": "x.py"},
        state=state,
    )
    step = json.loads(
        (tmp_path / "run-A.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert "rollout_id" not in step


# ---------------------------------------------------------------------------
# 2. Event provenance
# ---------------------------------------------------------------------------

def test_event_persists_rollout_id(rollout_ctx):
    """The authoritative invocation event (agent_action with a run_id) must
    carry the same rollout id in its payload."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    store = FakeEventStore()
    svc.event_store = store
    svc._record_event(
        "agent_action",
        "coder",
        {"action": "filesystem", "turn": 0},
        run_id="run-A",
        parent_id="",
    )
    assert store.appended, "event must be recorded"
    ev = store.appended[0]
    assert ev.event_type == "agent_action"
    assert ev.payload["run_id"] == "run-A"
    assert ev.payload["rollout_id"] == rollout_ctx


def test_non_invocation_event_keeps_legacy_shape(rollout_ctx):
    """Events WITHOUT a run id (generation_completed / authorization) are not
    invocation-scoped — they must NOT gain a rollout_id key (legacy shape)."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    store = FakeEventStore()
    svc.event_store = store
    svc._record_event(
        "generation_completed",
        "agent_service_v2",
        {"model": "robs4b", "status": "success"},
    )
    assert store.appended
    assert "rollout_id" not in store.appended[0].payload
    assert "run_id" not in store.appended[0].payload


# ---------------------------------------------------------------------------
# 3. Fitness provenance
# ---------------------------------------------------------------------------

def test_fitness_persists_rollout_id(tmp_path, monkeypatch, rollout_ctx):
    """_feed_outcome captures the rollout from context BEFORE the executor jump
    and persists it alongside run_id in the fitness record."""
    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")

    service = agent.AgentServiceV2()
    state = agent._CallState()
    state.run_id = "run-fitness-A"
    service._feed_outcome(
        "coder",
        "analyze the twine packaging metadata bug",
        state,
        completed=True,
        tool_success_rate=1.0,
        turns_used=4,
        genome_id="agent:coder",
    )
    recs = of.recent_fitness()
    assert recs
    last = recs[-1]
    assert last["run_id"] == "run-fitness-A"
    assert last["rollout_id"] == rollout_ctx


# ---------------------------------------------------------------------------
# 4. Legacy behavior without a rollout
# ---------------------------------------------------------------------------

def test_missing_rollout_id_preserves_legacy_behavior(tmp_path, monkeypatch):
    """No rollout context → trajectory/event/fitness must keep their exact
    legacy shape (no rollout_id key), while run_id still persists."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path

    svc._write_run_trajectory(
        run_id="run-legacy",
        agent_id="coder",
        parent_id="",
        delegated_by="",
        prompt="analyze the codebase",
        genome_id="",
        last_chunk={"action": "final", "content": "done"},
    )
    rec = _summary(svc, "run-legacy")
    assert rec["run_id"] == "run-legacy"
    assert "rollout_id" not in rec

    store = FakeEventStore()
    svc.event_store = store
    svc._record_event(
        "agent_action", "coder", {"action": "read", "turn": 0}, run_id="run-legacy"
    )
    assert "run_id" in store.appended[0].payload
    assert "rollout_id" not in store.appended[0].payload

    from swarm_os.services import outcome_fitness as of

    monkeypatch.setattr(of, "FITNESS_PATH", tmp_path / "fitness.jsonl")
    monkeypatch.setenv("SWARM_EVOLUTION", "1")
    service = agent.AgentServiceV2()
    state = agent._CallState()
    state.run_id = "run-legacy"
    service._feed_outcome("coder", "legacy task prompt", state, completed=True)
    recs = of.recent_fitness()
    assert recs and recs[-1]["run_id"] == "run-legacy"
    assert "rollout_id" not in recs[-1]


# ---------------------------------------------------------------------------
# 5. One rollout → many backend run_ids (retry)
# ---------------------------------------------------------------------------

def test_retry_runs_share_rollout_id(tmp_path, rollout_ctx):
    """Two backend invocations (retry => two run_ids) under ONE rollout must
    both persist the same rollout id — never collapse into one run_id."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    for run_id in ("run-A", "run-B"):
        svc._write_run_trajectory(
            run_id=run_id,
            agent_id="coder",
            parent_id="",
            delegated_by="",
            prompt=f"retry attempt for {run_id}",
            genome_id="",
            last_chunk={"action": "final", "content": "x"},
        )
    rec_a = _summary(svc, "run-A")
    rec_b = _summary(svc, "run-B")
    assert rec_a["run_id"] != rec_b["run_id"]
    assert rec_a["rollout_id"] == rollout_ctx
    assert rec_b["rollout_id"] == rollout_ctx


# ---------------------------------------------------------------------------
# 6. The rollout↔run join is reconstructable from persistence
# ---------------------------------------------------------------------------

def test_rollout_run_join_is_queryable(tmp_path, rollout_ctx):
    """Write a concrete rollout/run pair through the real path, then read the
    persisted artifact back and prove the association reconstructs."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    svc._write_run_trajectory(
        run_id="run-join-A",
        agent_id="coder",
        parent_id="",
        delegated_by="",
        prompt="join test task",
        genome_id="",
        last_chunk={"action": "final", "content": "ok"},
    )
    # The join helper reads the durable summary from disk.
    assert svc.trajectory_rollout_id("run-join-A") == rollout_ctx
    # And a full read-back confirms both halves of the pair.
    rec = _summary(svc, "run-join-A")
    assert (rec["rollout_id"], rec["run_id"]) == (rollout_ctx, "run-join-A")


# ---------------------------------------------------------------------------
# 7. Mismatched rollout/run is not silently accepted
# ---------------------------------------------------------------------------

def test_mismatched_rollout_run_is_not_accepted(tmp_path):
    """A run persisted under rollout R_A must NOT be silently treated as
    belonging to rollout R_B — the durable join returns the truth."""
    token_a = ROLLOUT_ID_CTX.set("rollout-R_A")
    try:
        svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
        svc._TRAJ_DIR = tmp_path
        svc._write_run_trajectory(
            run_id="run-M",
            agent_id="coder",
            parent_id="",
            delegated_by="",
            prompt="mismatch test",
            genome_id="",
            last_chunk={"action": "final", "content": "x"},
        )
    finally:
        ROLLOUT_ID_CTX.reset(token_a)

    # A consumer claiming run-M belongs to rollout-R_B must be contradicted by
    # the durable provenance: the join answers R_A, and R_B has no such run.
    assert svc.trajectory_rollout_id("run-M") == "rollout-R_A"
    assert svc.trajectory_rollout_id("run-M") != "rollout-R_B"
    # Lane 2 is persistence-side: the durable record is the single source of
    # truth, so a mismatched claim is disproven by the join, not silently
    # accepted (the bridge's own evidence guard is unchanged and covered by
    # tests/test_experiment_lifecycle.py).
