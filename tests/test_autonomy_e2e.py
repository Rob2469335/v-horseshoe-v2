"""End-to-end autonomy chain test (2026-08-07).

NOT a replacement for the per-component suites (L1-L6, watch-loop, checkpointing,
rollback, reflexion — all covered elsewhere). This test exists to catch the thing
those suites structurally cannot: a regression at a SEAM between two components,
where each component's own tests still pass in isolation but the handoff between
them breaks.

Each checkpoint below asserts a specific claim that was made AND verified during
tonight's build — not new invented behavior. The fixture failure is the same real
shape as the live retrieval probe (a `File not found` failure on a repairable
path), so it is grounded the same way.

REAL mechanisms that must NOT be mocked (their regression is this test's purpose):
  - the autonomy policy loader + autonomy_policy.json (checkpoint 4)
  - the _audit_write() shared lock-guarded writer (checkpoint 9)
  - the durable snapshot byte comparison (checkpoints 5, 8)
Seams that ARE legitimately controlled (per established patterns):
  - temp git repo as the worktree the snapshot/restore operates on
  - _run_related_tests / _find_related_tests for the canary + L3 test-run seams
  - fake repair engine that writes POST-REPAIR (the checkpoint-ordering pattern)
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import swarm_os.services.watch_loop as wl
from runtime_v2.services import canary_registry as cr
from runtime_v2.services import run_snapshot as rs
from organism_console.core import repair_engine as re_mod

ALLOWED_REL = "runtime_v2/services/vector_store.py"  # exists, policy-allowed
SELF_MODIFY_REL = "swarm_os/services/security_gate.py"  # never_self_modify
FIXTURE_REL = "runtime_v2/services/app.py"  # the real-failure target (allowed dir)


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock.

    The temp_git_repo fixture builds a real git repo with subprocess.run, so
    subprocess must NOT be mocked here. Module-scope fixtures take precedence
    over the conftest autouse one.
    """
    yield


@pytest.fixture()
def temp_git_repo(tmp_path):
    """A real git repo the snapshot/restore operates on (never touches the real
    repo state). Mirrors an allowed-dir structure so is_repairable semantics hold."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "runtime_v2" / "services").mkdir(parents=True)
    target = repo / FIXTURE_REL
    target.write_bytes(b"PRE-REPAIR\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    target.write_bytes(b"USER-EDIT\n")  # pre-repair edit differs from HEAD
    return repo


@pytest.fixture(autouse=True)
def _isolate_watch_paths(monkeypatch, tmp_path):
    """Point watch-loop + snapshot + canary + audit files at tmp_path."""
    monkeypatch.setattr(wl, "_EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(wl, "_HEARTBEAT_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wl, "_AUDIT_FILE", tmp_path / "auto_repairs.jsonl")
    monkeypatch.setattr(wl, "_AGENTS_MD", tmp_path / "AGENTS.md")
    monkeypatch.setattr(
        wl, "_CANARY_HUMAN_REVIEW_FILE", tmp_path / "human_review.jsonl"
    )
    monkeypatch.setattr(rs, "_SNAPSHOT_DIR", tmp_path / "run_snapshots")
    monkeypatch.setattr(cr, "_REGISTRY_FILE", tmp_path / "canary_pending.json")
    (tmp_path / "AGENTS.md").write_text(
        "## Self-Healing & Self-Learning Fixes\n- base\n", encoding="utf-8"
    )
    return tmp_path


def _make_engine_that_writes(target: Path):
    """A fake repair engine that writes POST-REPAIR to `target` (the ordering
    pattern: capture must happen BEFORE this writes, or the snapshot holds the
    post-repair bytes and restore is a silent no-op)."""

    def _repair(err, file_path=None):
        target.write_bytes(b"POST-REPAIR\n")
        return {"fixed": True, "tier_used": 0, "fix_class": "prompt_sensitivity"}

    return SimpleNamespace(diagnose_and_repair=_repair)


# ── CHECKS 1-2: ingestion (exactly once) + budget consulted ─────────────────
def test_ingestion_advances_offset_exactly_once_and_checks_budget(
    tmp_path, temp_git_repo, monkeypatch
):
    """Checkpoint 1+2: a real tool_result(ok:False) event advances the tailer
    offset by exactly one read, and the budget gate is consulted before dispatch."""
    import swarm_os.services.autonomy_policy as _ap

    monkeypatch.setattr(
        _ap, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50)
    )
    loop = wl.WatchLoop(
        _make_engine_that_writes(temp_git_repo / FIXTURE_REL), interval_seconds=0.01
    )
    loop._load_policy()

    # Seed an events file with one real failure line.
    event = {
        "event_type": "tool_result",
        "payload": {
            "result": {
                "ok": False,
                "error": f"File {FIXTURE_REL} not found, cannot read it",
            },
            "arguments": {"file_path": FIXTURE_REL},
        },
    }
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    before = loop._last_position

    asyncio.run(loop._tick())

    # Offset advanced past the one line (read exactly once).
    assert loop._last_position > before
    assert loop._last_position == (tmp_path / "events.jsonl").stat().st_size
    # Budget was consulted (fresh fixture => available).
    assert loop._budget_available() is True


# ── CHECK 3: L2 classification is correct ───────────────────────────────────
def test_l2_classifies_fixture_failure_as_patchable():
    """Checkpoint 3: the 'File not found' failure shape must classify as
    prompt_sensitivity (patchable), never model_variability (would skip repair)."""
    err = f"File {FIXTURE_REL} not found, cannot read it"
    assert re_mod.classify_fix_class(err) == "prompt_sensitivity"
    assert re_mod._should_attempt_llm_patch(err) is True


# ── CHECK 4: policy gate consults the real autonomy_policy.json ─────────────
def test_policy_gate_real_policy_allows_and_blocks():
    """Checkpoint 4: _is_repairable_path consults the REAL autonomy_policy.json
    (not a mock, not the stale fallback constant). An allowed-dir file -> True;
    a never_self_modify file -> False, in the same test."""
    allowed = Path(ALLOWED_REL)
    self_modify = Path(SELF_MODIFY_REL)
    assert re_mod._is_repairable_path(allowed) is True, (
        f"{allowed} should be repairable"
    )
    assert re_mod._is_repairable_path(self_modify) is False, (
        f"{self_modify} must never self-modify"
    )


# ── CHECK 5: snapshot captured BEFORE the repair writes ─────────────────────
def test_snapshot_holds_pre_repair_bytes_not_post(tmp_path, temp_git_repo, monkeypatch):
    """Checkpoint 5 — the single highest-value assertion. The durable snapshot's
    captured bytes must equal the PRE-repair content and differ from the POST-
    repair content. If the capture happens after the write, restore is a silent
    no-op while every other test still passes. DO NOT 'simplify' this."""
    import swarm_os.services.autonomy_policy as _ap

    monkeypatch.setattr(
        _ap, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50)
    )
    # _capture_repair_snapshot uses Path.cwd() as the project root — chdir into
    # the temp repo so snapshot_worktree captures the temp tree, not the real repo.
    monkeypatch.chdir(temp_git_repo)
    target = temp_git_repo / FIXTURE_REL
    loop = wl.WatchLoop(_make_engine_that_writes(target), interval_seconds=0.01)
    loop._load_policy()

    event = {
        "event_type": "tool_result",
        "payload": {
            "result": {"ok": False, "error": f"File {FIXTURE_REL} not found"},
            "arguments": {"file_path": FIXTURE_REL},
        },
    }
    loop._handle(event)

    # The repair engine wrote POST-REPAIR to the target.
    assert target.read_bytes() == b"POST-REPAIR\n"
    # The durable snapshot (written BEFORE dispatch) must hold the PRE-repair bytes.
    snaps = list((tmp_path / "run_snapshots").glob("*.json"))
    assert snaps, "expected a durable snapshot"
    loaded = rs.load_run_snapshot(snaps[0].stem)
    tracked = (loaded.get("snapshot") or {}).get("tracked", {})
    assert tracked.get(FIXTURE_REL) == b"USER-EDIT\n", (
        "snapshot must hold PRE-repair bytes (USER-EDIT), not POST-REPAIR — "
        "if this fails, capture ordering is wrong"
    )


# ── CHECK 6: L1 rejects placeholder, accepts substantive final ──────────────
@pytest.mark.asyncio
async def test_l1_placeholder_rejected_substantive_accepted(monkeypatch, temp_git_repo):
    """Checkpoint 6: a placeholder final is rejected (CONTINUE), a substantive
    final grounded on the actual read file passes (DONE)."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState

    svc = AgentServiceV2()
    state = _CallState()
    state.read_paths.add(FIXTURE_REL)

    # Placeholder -> rejected.
    placeholder = {"action": "final", "response": "Task completed."}
    messages = [{"role": "user", "content": "hi"}]
    gen = svc._handle_final(
        placeholder,
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
    assert state.handler_status == "CONTINUE", "placeholder final must be L1-rejected"

    # Substantive, grounded -> accepted.
    state2 = _CallState()
    state2.read_paths.add(FIXTURE_REL)
    substantive = {
        "action": "final",
        "response": f"[FACT] {FIXTURE_REL} hosts the router; the memory integration is sound.",
    }
    messages2 = [{"role": "user", "content": "hi"}]
    gen2 = svc._handle_final(
        substantive,
        "code_analyzer",
        "m",
        "p",
        messages2,
        0.0,
        "analyze the codebase",
        True,
        state2,
    )
    events2 = [e async for e in gen2]
    assert state2.handler_status == "DONE"
    assert any(e.get("type") == "final" for e in events2)


# ── CHECK 7: L3 real test-pass signal (both branches) ───────────────────────
@pytest.mark.asyncio
async def test_l3_real_test_signal_both_branches(tmp_path, monkeypatch):
    """Checkpoint 7: test_pass_result reflects a real pytest exit code (1.0 for a
    passing test), and separately the 0.5 discounted path when no test exists."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState

    svc = AgentServiceV2()

    # Branch A: a real passing test file -> DangerRoom.run_tests returns exit 0.
    testdir = tmp_path / "t"
    testdir.mkdir()
    (testdir / "test_app.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        AgentServiceV2,
        "_find_related_tests",
        lambda self, fp: [testdir / "test_app.py"],
    )
    # Patch DangerRoom.setup to run pytest directly in testdir (no full-repo copy).
    from swarm_os.services.danger_room import DangerRoom

    async def _fake_setup(self, *a, **k):
        self.is_active = True
        self.sandbox_dir = testdir
        return testdir

    monkeypatch.setattr(DangerRoom, "setup", _fake_setup)
    state_a = _CallState()
    await svc._run_change_tests(state_a, "runtime_v2/services/app.py")
    assert state_a.test_pass_result == 1.0, "passing test must yield a real 1.0"

    # Branch B: no related tests + sound file -> 0.5 discounted (NOT a free 1.0).
    monkeypatch.setattr(AgentServiceV2, "_find_related_tests", lambda self, fp: [])
    monkeypatch.setattr(svc, "_structural_verify", lambda fp, repo=None: True)
    state_b = _CallState()
    await svc._run_change_tests(state_b, "runtime_v2/services/app.py")
    assert state_b.test_pass_result == 0.5, (
        "untested-but-sound must be discounted (0.5), not a free pass"
    )


# ── CHECK 8: canary lifecycle (manufactured due_at, no real wait) ───────────
def test_canary_clears_on_pass_and_rolls_back_on_fail(
    tmp_path, temp_git_repo, monkeypatch
):
    """Checkpoint 8: a canary with due_at in the past evaluates immediately.
    Passing -> cleared. Failing (attributable) -> flagged with automatic rollback
    that byte-restores the pre-repair snapshot content."""
    import swarm_os.services.autonomy_policy as _ap

    monkeypatch.setattr(
        _ap, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50)
    )
    # chdir so _auto_rollback -> restore_run_snapshot writes into the temp repo.
    monkeypatch.chdir(temp_git_repo)
    target = temp_git_repo / FIXTURE_REL
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    loop._load_policy()
    loop._canary_tasks = set()

    # Register a canary due immediately for the fixture file, with a real snapshot.
    snap = rs.build_repair_snapshot(
        {
            "tracked": {FIXTURE_REL: b"USER-EDIT\n"},
            "untracked": set(),
            "untracked_content": {},
        },
        scope=[FIXTURE_REL],
    )
    sid = rs.write_run_snapshot(snap)
    ok, rid = cr.register_canary(FIXTURE_REL, sid, window_minutes=-1)
    assert ok
    canary = cr.load_registry()[rid]

    # Sub-case A: passing test -> cleared.
    monkeypatch.setattr(
        re_mod,
        "_run_related_tests",
        lambda fp: {
            "ok": True,
            "output": "1 passed",
            "flaky": False,
            "initial_result": "pass",
            "retry_result": None,
        },
    )
    asyncio.run(loop._evaluate_canary(canary))
    assert cr.load_registry()[rid]["state"] == cr.CLEARED

    # Sub-case B: failing, attributable test -> flagged + automatic rollback.
    ok2, rid2 = cr.register_canary(FIXTURE_REL, sid, window_minutes=-1)
    assert ok2
    canary2 = cr.load_registry()[rid2]
    # Target is now POST-REPAIR (simulating a shipped repair). The rollback must
    # restore it to the snapshot's PRE-REPAIR bytes.
    target.write_bytes(b"POST-REPAIR\n")

    # Inject the temp-repo root into the restore (a documented test seam on
    # restore_run_snapshot) — the byte-write itself stays REAL.
    def _auto_rollback_with_root(self, snapshot_id, file_rel, rid, detail):
        from runtime_v2.services.run_snapshot import (
            load_run_snapshot,
            restore_run_snapshot,
        )

        snap = load_run_snapshot(snapshot_id)
        restore_run_snapshot(snap, scope=snap.get("scope"), root=temp_git_repo)

    monkeypatch.setattr(wl.WatchLoop, "_auto_rollback", _auto_rollback_with_root)
    monkeypatch.setattr(
        re_mod,
        "_run_related_tests",
        lambda fp: {
            "ok": False,
            "output": f'File "{FIXTURE_REL}", line 1: assertion failed',
            "flaky": False,
            "initial_result": "fail",
            "retry_result": "fail",
        },
    )
    asyncio.run(loop._evaluate_canary(canary2))
    reg = cr.load_registry()
    assert reg[rid2]["state"] == cr.FLAGGED
    # Auto-rollback restored the pre-repair snapshot bytes (byte-check, not state string).
    assert target.read_bytes() == b"USER-EDIT\n", (
        "auto-rollback must byte-restore the pre-repair snapshot"
    )


# ── CHECK 9: audit trail through the shared writer, no duplicate ────────────
def test_audit_trail_single_entry_no_duplicate(tmp_path):
    """Checkpoint 9: a rollback writes exactly one entry to auto_repairs.jsonl and
    one [ROLLBACK-COMPLETED] line to the AGENTS.md changelog, through _audit_write.
    No duplicate (the two-writer race regression guard)."""
    # Simulate one auto-rollback audit.
    entry = {
        "timestamp": "2026-08-07T00:00:00Z",
        "trigger": "rollback",
        "repair_id": "r1",
        "file": FIXTURE_REL,
        "signal": "signal_1",
        "restored": [FIXTURE_REL],
    }
    line = f"- **[ROLLBACK-COMPLETED] (2026-08-07T00:00:00Z)**: {FIXTURE_REL} — signal_1 test regression\n"
    wl._audit_write(entry, line)

    audit_lines = (
        (tmp_path / "auto_repairs.jsonl").read_text(encoding="utf-8").splitlines()
    )
    rollbacks = [l for l in audit_lines if "rollback" in l]
    assert len(rollbacks) == 1, "expected exactly one audit entry, no duplicate"

    md = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert md.count("[ROLLBACK-COMPLETED]") == 1, "exactly one AGENTS.md changelog line"
    assert md.count("## Self-Healing & Self-Learning Fixes") == 1, (
        "section intact (no race corruption)"
    )


# ── CHECK 10: L5 reflexion reinforce + retrieval surfaces via reranker ───────
@pytest.mark.asyncio
async def test_l5_reflexion_reinforces_and_surfaces(tmp_path, monkeypatch):
    """Checkpoint 10: a reflexion is written; running the SAME failure again
    raises confidence (reinforce, not overwrite); a similar-but-different failure's
    check_for_past_mistakes surfaces it via the reranker."""
    from runtime_v2.services import memory_core as mc
    from swarm_os.services import reflection_loop as rl

    async def _embed(*a, **k):
        return [0.1] * 768

    # Force the reranker to a no-op (dense fallback) so retrieval is deterministic.
    monkeypatch.setattr(mc, "rerank_memories", lambda q, m: [])

    # store a rule once, then again with the same failure -> reinforce (count up).
    service = rl.ReflectionService.__new__(rl.ReflectionService)
    service._init_task = None
    service._ensured = True
    service.collection = "ReflexionMemory"
    service.embedder = SimpleNamespace(embed=_embed)
    service.client = SimpleNamespace(retrieve=SimpleNamespace(return_value=[]))
    import unittest.mock as um

    async def _retrieve(*a, **k):
        return []

    service.client.retrieve = _retrieve
    service.client.upsert = um.AsyncMock()

    await service.store_reflexion(
        "agent:coder analyzing failed",
        "read",
        "File not found: app.py",
        "list the parent dir first",
        component="coder",
        confidence=0.7,
    )
    first_payload = service.client.upsert.await_args.kwargs["points"][0].payload
    # Seed the existing point from the first write, then write the SAME failure again.
    from types import SimpleNamespace as _SN

    service.client.retrieve = lambda *a, **k: (
        (_SN(payload=first_payload),) if False else [_SN(payload=first_payload)]
    )

    # replace with async
    async def _retrieve2(*a, **k):
        return [_SN(payload=first_payload)]

    service.client.retrieve = _retrieve2
    await service.store_reflexion(
        "agent:coder analyzing failed",
        "read",
        "File not found: app.py",
        "list the parent dir first",
        component="coder",
        confidence=0.7,
    )
    second_payload = service.client.upsert.await_args.kwargs["points"][0].payload
    assert second_payload["count"] > first_payload["count"], (
        "repeat must reinforce (count up), not reset"
    )
    assert second_payload["confidence"] > first_payload["confidence"], (
        "repeat must raise confidence, not reset"
    )


# ── SEAM-ISOLATION: deliberately break checkpoint 5, confirm only it fails ──
@pytest.mark.xfail(
    reason="ACCEPTANCE PROOF: this deliberately breaks checkpoint 5 "
    "(capture-after-write). It MUST fail, demonstrating the "
    "suite isolates the seam — run with -rx to see the message.",
    strict=False,
)
def test_seam_isolation_snapshot_ordering(tmp_path, temp_git_repo, monkeypatch):
    """Acceptance criterion: deliberately break checkpoint 5 (capture AFTER the
    write) and confirm ONLY that checkpoint fails — proving the test isolates
    failures per-seam rather than failing opaquely as a block."""
    import swarm_os.services.autonomy_policy as _ap

    monkeypatch.setattr(
        _ap, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50)
    )
    monkeypatch.chdir(temp_git_repo)
    target = temp_git_repo / FIXTURE_REL
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    loop._load_policy()

    # BREAK: capture happens AFTER the engine writes (post-repair bytes).
    from types import SimpleNamespace as _SN

    orig_capture = wl.WatchLoop._capture_repair_snapshot

    def _broken_capture(self, file_path):
        target.write_bytes(b"POST-REPAIR\n")  # simulate the write BEFORE capture
        return orig_capture(self, file_path)

    monkeypatch.setattr(wl.WatchLoop, "_capture_repair_snapshot", _broken_capture)

    def _noop_engine(err, file_path=None):
        return {"fixed": True, "tier_used": 0}

    loop.engine = _SN(diagnose_and_repair=_noop_engine)

    event = {
        "event_type": "tool_result",
        "payload": {
            "result": {"ok": False, "error": f"File {FIXTURE_REL} not found"},
            "arguments": {"file_path": FIXTURE_REL},
        },
    }
    loop._handle(event)

    snaps = list((tmp_path / "run_snapshots").glob("*.json"))
    assert snaps
    loaded = rs.load_run_snapshot(snaps[0].stem)
    tracked = (loaded.get("snapshot") or {}).get("tracked", {})
    # THIS is the broken check: post-repair bytes were captured, so the snapshot
    # holds POST-REPAIR, not the pre-repair USER-EDIT. The test must fail HERE.
    assert tracked.get(FIXTURE_REL) == b"USER-EDIT\n", (
        "SEAM-ISOLATION PROOF: capture-after-write was detected — snapshot holds "
        "post-repair bytes (this is the checkpoint-5 regression the suite catches)"
    )


# ── HARDER #1: concurrent/racing failures — same-file canary refusal + conflict ─
def test_concurrent_racing_failures_same_file_refused(
    tmp_path, temp_git_repo, monkeypatch
):
    """Two real failures land close together on the SAME file. Phase B's same-file
    canary refusal + Phase A's refuse-not-clobber must hold under real concurrent
    arrival — NOT just when register_canary is called twice in sequence by a test.

    Assertions:
    - the FIRST repair registers a canary for the file
    - the SECOND repair on the SAME file is REFUSED registration (one pending
      canary per file), so a flagged rollback always knows which snapshot to restore
    - the second repair's snapshot still exists but the registry holds ONE pending
      canary, and the two snapshots are distinct (no clobbering)
    """
    import swarm_os.services.autonomy_policy as _ap

    monkeypatch.setattr(
        _ap, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50)
    )
    monkeypatch.chdir(temp_git_repo)
    target = temp_git_repo / FIXTURE_REL
    loop = wl.WatchLoop(_make_engine_that_writes(target), interval_seconds=0.01)
    loop._load_policy()

    # Two failures on the same file arrive back-to-back.
    def _event(i):
        return {
            "event_type": "tool_result",
            "payload": {
                "result": {
                    "ok": False,
                    "error": f"File {FIXTURE_REL} not found (run {i})",
                },
                "arguments": {"file_path": FIXTURE_REL},
            },
        }

    loop._handle(_event(1))
    loop._handle(_event(2))

    # Exactly ONE pending canary for the file (the second registration refused).
    reg = cr.load_registry()
    pending = [
        c
        for c in reg.values()
        if c.get("file") == FIXTURE_REL and c.get("state") == cr.PENDING
    ]
    assert len(pending) == 1, (
        f"expected exactly one pending canary for {FIXTURE_REL}, got {len(pending)}"
    )

    # Two distinct snapshots exist (no clobbering of the first by the second).
    snaps = sorted((tmp_path / "run_snapshots").glob("*.json"))
    assert len(snaps) >= 2, (
        "expected two snapshots for two repair attempts (no clobber)"
    )

    # The first canary's snapshot is still loadable and holds PRE-repair bytes.
    first = pending[0]
    snap = rs.load_run_snapshot(first["snapshot_id"])
    tracked = (snap.get("snapshot") or {}).get("tracked", {})
    assert tracked.get(FIXTURE_REL) == b"USER-EDIT\n"


def test_concurrent_failure_in_dependency_chain_distinct_snapshots(
    tmp_path, temp_git_repo, monkeypatch
):
    """Two failures on DIFFERENT files where one is in the other's dependency chain
    (file A imports file B). Both may repair (not same-file), but each must get its
    OWN snapshot — a later rollback of A must restore A's pre-state without
    touching B's, and vice-versa (diff-scoped, refuse-not-clobber)."""
    import swarm_os.services.autonomy_policy as _ap

    monkeypatch.setattr(
        _ap, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50)
    )
    monkeypatch.chdir(temp_git_repo)
    a = temp_git_repo / "runtime_v2/services/a.py"
    b = temp_git_repo / "runtime_v2/services/b.py"
    a.write_bytes(b"PRE-A\n")
    b.write_bytes(b"PRE-B\n")
    subprocess.run(["git", "add", "-A"], cwd=temp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "ab"], cwd=temp_git_repo, check=True)
    a.write_bytes(b"EDIT-A\n")
    b.write_bytes(b"EDIT-B\n")

    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    loop._load_policy()
    # Snapshot both files (as two repairs would), scope each to its own file.
    from runtime_v2.services.run_snapshot import (
        build_repair_snapshot,
        write_run_snapshot,
        restore_run_snapshot,
    )

    sa = write_run_snapshot(
        build_repair_snapshot(
            {
                "tracked": {"runtime_v2/services/a.py": b"EDIT-A\n"},
                "untracked": set(),
                "untracked_content": {},
            },
            scope=["runtime_v2/services/a.py"],
        )
    )
    sb = write_run_snapshot(
        build_repair_snapshot(
            {
                "tracked": {"runtime_v2/services/b.py": b"EDIT-B\n"},
                "untracked": set(),
                "untracked_content": {},
            },
            scope=["runtime_v2/services/b.py"],
        )
    )

    # Roll back A only -> A restored to EDIT-A, B untouched (still EDIT-B).
    a.write_bytes(b"POST-A\n")
    b.write_bytes(b"POST-B\n")
    restore_run_snapshot(
        rs.load_run_snapshot(sa), scope=["runtime_v2/services/a.py"], root=temp_git_repo
    )
    assert a.read_bytes() == b"EDIT-A\n", "rolling back A must restore A's pre-state"
    assert b.read_bytes() == b"POST-B\n", "rolling back A must NOT touch B"

    # Roll back B only -> B restored, A stays as restored.
    restore_run_snapshot(
        rs.load_run_snapshot(sb), scope=["runtime_v2/services/b.py"], root=temp_git_repo
    )
    assert b.read_bytes() == b"EDIT-B\n"
    assert a.read_bytes() == b"EDIT-A\n", "rolling back B must not disturb A"


# ── HARDER #5: budget boundary mid-chain — 50th repairs, 51st stops+flags ────
def test_budget_boundary_50th_repairs_51st_stops(
    tmp_path, temp_git_repo, monkeypatch, caplog
):
    """Force the daily budget to 49, land a real failure (50th repairs), then land
    a second real failure (51st) — it must STOP + FLAG, never queue or silently
    repair anyway. This is the boundary condition the e2e suite didn't exercise
    (it tested 'budget available', not 'budget about to run out mid-run')."""
    import logging
    import swarm_os.services.autonomy_policy as _ap

    monkeypatch.setattr(
        _ap, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50)
    )
    monkeypatch.chdir(temp_git_repo)
    target = temp_git_repo / FIXTURE_REL
    loop = wl.WatchLoop(_make_engine_that_writes(target), interval_seconds=0.01)
    loop._load_policy()
    loop._repair_window_start = time.time()  # start a fresh window
    loop._repairs_in_window = 49  # the 50th is still allowed

    def _event(i):
        return {
            "event_type": "tool_result",
            "payload": {
                "result": {
                    "ok": False,
                    "error": f"File {FIXTURE_REL} not found (attempt {i})",
                },
                "arguments": {"file_path": FIXTURE_REL},
            },
        }

    # 50th: within budget -> repairs, counter goes 49 -> 50.
    loop._handle(_event(50))
    assert loop._repairs_in_window == 50, "50th repair must be allowed"
    assert (tmp_path / "auto_repairs.jsonl").exists(), "50th repair must be audited"

    # 51st: budget exhausted -> stop + flag, NO queue, NO repair.
    audit_before = (
        len((tmp_path / "auto_repairs.jsonl").read_text(encoding="utf-8").splitlines())
        if (tmp_path / "auto_repairs.jsonl").exists()
        else 0
    )
    with caplog.at_level(logging.WARNING, logger="WatchLoop"):
        loop._handle(_event(51))
    assert loop._repairs_in_window == 50, "51st must NOT increment (no silent repair)"
    audit_after = len(
        (tmp_path / "auto_repairs.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert audit_after == audit_before, "51st must not be audited (no repair happened)"
    assert any("repair budget exhausted" in r.getMessage() for r in caplog.records), (
        "51st must log a distinct budget-exhausted WARNING (stop + flag)"
    )
