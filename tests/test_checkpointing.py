"""Tests for durable agent-run checkpointing (2026 autonomy layer, move 3).

The critical behaviors:
  1. checkpoint_id is STABLE across the same logical goal (canonicalized prompt),
     so a slightly-different resume call never silently fails to find its own
     checkpoint.
  2. The checkpoint round-trips all L1-L6-critical _CallState fields (read_paths,
     test_pass_result, _tests_ran, _contract_finals, guards, fitness counters) —
     dropping any would silently reintroduce the bug that pattern closes.
  3. DELETE happens ONLY when the final was ACCEPTED by L1 (handler_status==DONE).
     An L1-rejected final (a real placeholder final / unread-file reference from
     this session's own L1 fixtures) must KEEP the checkpoint so the run can
     continue. Deleting on loop-exit would defeat the safety net on precisely the
     runs L1 was built to catch.
  4. Atomic overwrite-latest (os.replace) — a torn write never corrupts.
  5. The checkpoint is invisible to the watch-loop repair budget.
"""

import pytest

from runtime_v2.services import checkpointing as ck
from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState


@pytest.fixture(autouse=True)
def _isolate_checkpoint_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(ck, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    return tmp_path


# ── checkpoint_id stability ─────────────────────────────────────────────────
def test_checkpoint_id_stable_across_logical_goal():
    assert ck.checkpoint_id("coder", "fix the bug") == ck.checkpoint_id(
        "coder", "fix the bug"
    )
    # canonicalization: trailing space / collapsed whitespace / case preserved
    assert ck.checkpoint_id("coder", "  fix   the   bug  ") == ck.checkpoint_id(
        "coder", "fix the bug"
    )
    # different agent or different goal -> different id
    assert ck.checkpoint_id("coder", "fix the bug") != ck.checkpoint_id(
        "debugger", "fix the bug"
    )
    assert ck.checkpoint_id("coder", "fix the bug") != ck.checkpoint_id(
        "coder", "fix the other bug"
    )


def test_checkpoint_id_is_stored_and_resume_uses_stored_id():
    """Resume looks up by the STORED id — never re-derives from caller text."""
    cid = ck.checkpoint_id("coder", "implement the feature")
    ck.write_checkpoint(
        cid,
        {
            "checkpoint_id": cid,
            "turn": 5,
            "messages": [],
            "state": {},
            "prompt": "implement the feature",
        },
    )
    loaded = ck.load_checkpoint(cid)
    assert loaded is not None
    assert loaded["checkpoint_id"] == cid


# ── round-trip of L1-L6-critical state ──────────────────────────────────────
def test_state_round_trip_preserves_critical_fields():
    svc = AgentServiceV2()
    state = _CallState()
    state.read_paths = {"swarm_os/api/routes.py", "runtime_v2/api/agent_service_v2.py"}
    state.test_pass_result = 0.5
    state._tests_ran = True
    state._contract_finals = 1
    state.premature_finals = 2
    state.reviewer_fails = 1
    state.did_code_change = True
    state.pending_verify = False
    state._verify_final_rejected = True
    state.did_web_search = True
    state.did_web_fetch = True
    state._web_final_rejected = True
    state.last_search_urls = ["https://example.com"]
    state._web_fetch_injected = True
    state._executor_research_delegated = True
    state._executor_impl_delegated = False
    state.todos = [{"id": 1, "item": "read the code"}]
    state.todo_id = 1
    state._tool_attempts = 5
    state._tool_successes = 4
    state._turn = 12
    state.genome_id = "genome_123"

    d = svc._state_to_dict(state)
    restored = svc._state_from_dict(_CallState(), d)
    assert restored.read_paths == state.read_paths
    assert restored.test_pass_result == 0.5
    assert restored._tests_ran is True
    assert restored._contract_finals == 1
    assert restored.premature_finals == 2
    assert restored.did_code_change is True
    assert restored.did_web_search is True
    assert restored.did_web_fetch is True
    assert restored.last_search_urls == state.last_search_urls
    assert restored._executor_research_delegated is True
    assert restored.todos == state.todos
    assert restored._tool_attempts == 5
    assert restored._tool_successes == 4
    assert restored._turn == 12
    assert restored.genome_id == "genome_123"


# ── atomic write ────────────────────────────────────────────────────────────
def test_checkpoint_write_is_atomic_overwrite_latest(tmp_path):
    cid = "abc123"
    ck.write_checkpoint(cid, {"turn": 1, "messages": ["one"]})
    ck.write_checkpoint(cid, {"turn": 2, "messages": ["one", "two"]})
    loaded = ck.load_checkpoint(cid)
    assert loaded["turn"] == 2  # latest overwrites
    assert loaded["messages"] == ["one", "two"]
    # no stale .tmp left behind
    assert not list((tmp_path / "checkpoints" / cid).glob("*.tmp"))
    assert not list((tmp_path / "checkpoints" / cid).glob("*.lock"))


def test_load_missing_returns_none(tmp_path):
    assert ck.load_checkpoint("nope") is None


def test_delete_removes_checkpoint(tmp_path):
    cid = "to-delete"
    ck.write_checkpoint(cid, {"turn": 1, "messages": []})
    assert ck.load_checkpoint(cid) is not None
    ck.delete_checkpoint(cid)
    assert ck.load_checkpoint(cid) is None


# ── delete-on-DONE only: real L1 fixtures ───────────────────────────────────
@pytest.mark.asyncio
async def test_delete_only_on_accepted_final_real_l1_placeholder(tmp_path):
    """The real delete-timing case: a code_analyzer emitting a PLACEHOLDER final
    ('Task completed.') is L1-REJECTED (handler_status=CONTINUE). The checkpoint
    must survive so the run can continue — deleting on 'loop exited' would have
    deleted the safety net on precisely the run L1 exists to catch."""
    svc = AgentServiceV2()
    state = _CallState()
    cid = ck.checkpoint_id("code_analyzer", "analyze the codebase for bugs")
    ck.write_checkpoint(
        cid,
        {
            "turn": 3,
            "messages": [],
            "state": {},
            "prompt": "analyze the codebase for bugs",
        },
    )

    messages = [{"role": "user", "content": "hi"}]
    gen = svc._handle_final(
        {"action": "final", "response": "Task completed."},
        "code_analyzer",
        "m",
        "p",
        messages,
        0.0,
        "analyze the codebase for bugs",
        True,
        state,
    )
    async for _ in gen:
        pass
    assert state.handler_status == "CONTINUE"  # L1 rejected it
    assert ck.load_checkpoint(cid) is not None  # checkpoint MUST survive


@pytest.mark.asyncio
async def test_delete_only_on_accepted_final_real_l1_unread_file(tmp_path):
    """Same delete-timing rule with the OTHER real L1 fixture: a final citing a
    file that was never read this run is rejected (CONTINUE) — checkpoint survives."""
    svc = AgentServiceV2()
    state = _CallState()
    cid = ck.checkpoint_id("code_analyzer", "analyze the codebase")
    ck.write_checkpoint(
        cid, {"turn": 2, "messages": [], "state": {}, "prompt": "analyze the codebase"}
    )

    messages = [{"role": "user", "content": "hi"}]
    gen = svc._handle_final(
        {
            "action": "final",
            "response": "I audited swarm_os/core/orchestrator.py and it has a bug.",
        },
        "code_analyzer",
        "m",
        "p",
        messages,
        0.0,
        "analyze the codebase",
        True,
        state,
    )
    async for _ in gen:
        pass
    assert state.handler_status == "CONTINUE"
    assert ck.load_checkpoint(cid) is not None


@pytest.mark.asyncio
async def test_delete_happens_on_accepted_final(tmp_path):
    """A legitimate, L1-accepted final (real reads, substantive response) reaches
    DONE — the checkpoint is then deleted (run truly finished)."""
    svc = AgentServiceV2()
    state = _CallState()
    state.read_paths.add("swarm_os/api/routes.py")
    cid = ck.checkpoint_id("code_analyzer", "analyze the codebase")
    ck.write_checkpoint(
        cid, {"turn": 2, "messages": [], "state": {}, "prompt": "analyze the codebase"}
    )

    messages = [{"role": "user", "content": "hi"}]
    gen = svc._handle_final(
        {
            "action": "final",
            "response": "[FACT] swarm_os/api/routes.py hosts the router; the memory integration is sound.",
        },
        "code_analyzer",
        "m",
        "p",
        messages,
        0.0,
        "analyze the codebase",
        True,
        state,
    )
    events = [e async for e in gen]
    assert state.handler_status == "DONE"
    assert any(e.get("type") == "final" for e in events)
    # NOTE: the delete hook lives in the turn loop (after _handle_final returns
    # DONE), not inside _handle_final — so at this level the checkpoint survives.
    # The loop-level delete is exercised via the full-run test below.
    assert ck.load_checkpoint(cid) is not None


# ── checkpoint invisible to watch-loop budget ───────────────────────────────
def test_checkpoint_invisible_to_watch_loop_budget(monkeypatch, tmp_path):
    """Writing/loading a checkpoint never touches the watch-loop's budget or the
    repair breaker — a resumed run is invisible to the daily ceiling."""
    from types import SimpleNamespace
    import swarm_os.services.watch_loop as wl

    monkeypatch.setattr(wl, "_EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(wl, "_HEARTBEAT_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wl, "_AUDIT_FILE", tmp_path / "auto_repairs.jsonl")
    monkeypatch.setattr(wl, "_AGENTS_MD", tmp_path / "AGENTS.md")

    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    loop._load_policy()
    loop._record_repair()  # one repair consumed
    assert loop._repairs_in_window == 1
    # Checkpoint operations do NOT touch the budget counter.
    ck.write_checkpoint("some-run", {"turn": 1, "messages": []})
    ck.load_checkpoint("some-run")
    assert loop._repairs_in_window == 1


@pytest.mark.asyncio
async def test_loop_level_delete_after_response_delivered(monkeypatch, tmp_path):
    """Full-loop integration: a run that reaches a genuine L1-accepted DONE must
    delete its checkpoint ONLY AFTER the final response has been fully yielded to
    the stream. The delete fires after `async for _ in _handle_final(...): yield _`
    completes (agent_service_v2.py:1608-1616), so a response is never
    'accepted-and-then-lost' with the checkpoint already gone."""
    import runtime_v2.api.agent_service_v2 as mod
    from runtime_v2.api.agent_service_v2 import AgentServiceV2

    svc = AgentServiceV2()

    # Make code_analyzer a NON-analysis agent for this test so `_fetched_content`
    # starts True (no-read guard passes) and the L1 contract check is bypassed —
    # we are testing DELETE ORDERING, not L1 (covered by its own fixtures).
    monkeypatch.setattr(mod, "ANALYSIS_AGENTS", ())

    # Drive the loop with exactly one real final decision (code_analyzer).
    async def _fake_decision(*args, **kwargs):
        return {
            "action": "final",
            "response": "[FACT] swarm_os/api/routes.py hosts the router; the memory integration is sound.",
        }

    monkeypatch.setattr(mod.AgentServiceV2, "_get_decision", _fake_decision)

    # Pre-seed state.read_paths so the REAL _handle_final accepts the final (L1
    # grounding check). L1 itself is covered by its own fixtures; here we only
    # need the final to reach DONE so the loop-level delete fires. Patch the
    # state factory so every _CallState starts with the file already read.
    orig_call = mod._CallState

    def _patched_state():
        s = orig_call()
        s.read_paths.add("swarm_os/api/routes.py")
        return s

    monkeypatch.setattr(mod, "_CallState", _patched_state)

    cid = ck.checkpoint_id("code_analyzer", "analyze the codebase")
    events = []
    async for chunk in svc.step_agent_stream(
        "code_analyzer", "analyze the codebase", resume=None
    ):
        events.append(chunk)

    finals = [e for e in events if e.get("type") == "final"]
    assert finals, "expected a final chunk delivered to the stream"
    # After full consumption of the generator, the DONE delete has run — and it
    # ran only after the final chunk was yielded (the `yield _` precedes the
    # delete in the loop). Ordering is enforced by code structure (yield before
    # delete), asserted here as delivered-then-gone.
    assert ck.load_checkpoint(cid) is None
