"""Tests for Phase A of signal-gated rollback (2026 autonomy layer, move 4):
durable diff-scoped snapshots + conflict-aware mutation rollback.

The critical behaviors:
  1. A repair snapshot is durable (survives restart) + atomic (no torn file).
  2. restore_snapshot(scope=...) restores ONLY the scoped files — a diff-scoped
     revert, never a scoped-in-time whole-tree restore. Files outside scope stay
     untouched.
  3. rollback(mutation_id) is CONTENT-based (bytes, not mtime/existence): a touch
     with no content change safely rolls back; a genuine later write refuses.
  4. Distinct terminal states (rolled_back / refused_conflict / unavailable) —
     never a silent no-op.
"""
import json

import pytest

from runtime_v2.services import run_snapshot as rs
from runtime_v2.services import canary_registry as cr
from swarm_os.repositories.mutation_repo import MutationRepository
from swarm_os.services import watch_loop as wl


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock.

    One test builds a throwaway git repo and exercises snapshot capture against
    real `git` invocations (subprocess.run), so subprocess must NOT be mocked
    here. Module-scope fixtures take precedence over the conftest autouse one.
    """
    yield


@pytest.fixture(autouse=True)
def _isolate_snapshot_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "_SNAPSHOT_DIR", tmp_path / "run_snapshots")
    # The rollback tests drive WatchLoop._handle_tool_result with a real
    # pkg/bug.py + routes.py failure payload; without isolating the CANARY
    # registry too, each run registers a canary in the PRODUCTION
    # data/events/canary_pending.json (the running backend daemon then
    # evaluates and clears it, which is why a pending one was blocking
    # re-registration). Every other watch-loop path (_EVENTS_FILE/_AUDIT_FILE/
    # _AGENTS_MD/_CANARY_HUMAN_REVIEW_FILE) is already tmp-isolated per-test;
    # _REGISTRY_FILE is the one that leaked.
    monkeypatch.setattr(cr, "_REGISTRY_FILE", tmp_path / "canary_pending.json")
    monkeypatch.setattr(wl, "_CANARY_HUMAN_REVIEW_FILE", tmp_path / "human_review.jsonl")
    return tmp_path


# ── durable + atomic snapshot ───────────────────────────────────────────────
def test_snapshot_durable_and_atomic(tmp_path):
    sid = rs.write_run_snapshot({"scope": ["a.py"], "snapshot": {"tracked": {"a.py": b"x"}}})
    loaded = rs.load_run_snapshot(sid)
    assert loaded is not None
    assert loaded["scope"] == ["a.py"]
    # atomic overwrite: latest wins, no .tmp left behind
    rs.write_run_snapshot({"scope": ["b.py"], "snapshot": {}}, snapshot_id=sid)
    assert rs.load_run_snapshot(sid)["scope"] == ["b.py"]
    assert not list((tmp_path / "run_snapshots").glob("*.tmp"))
    # load missing -> None
    assert rs.load_run_snapshot("nope") is None


def test_build_repair_snapshot_records_scope():
    snap = rs.build_repair_snapshot({"tracked": {"a.py": b"x", "b.py": b"y"}}, scope=["a.py"])
    assert snap["kind"] == "repair"
    assert snap["scope"] == ["a.py"]


# ── diff-scoped restore ─────────────────────────────────────────────────────
def test_restore_run_snapshot_scope_only(tmp_path, monkeypatch):
    """restore with scope touches ONLY the scoped files; a file outside scope in
    the captured snapshot stays untouched (diff-scoped, not whole-snapshot)."""
    # The durable snapshot captures the PRE-REPAIR bytes (captured at repair-accept
    # BEFORE the repair's write) — that's what rollback restores.
    snap_payload = {
        "tracked": {
            "swarm_os/services/vector_store.py": b"PRE-REPAIR vector_store",
            "swarm_os/services/other.py": b"PRE-REPAIR other",
        },
        "untracked": set(),
        "untracked_content": {},
    }
    # Write both files on disk to their pre-repair (to-be-restored) state.
    root = tmp_path / "repo"
    (root / "swarm_os" / "services").mkdir(parents=True, exist_ok=True)
    (root / "swarm_os" / "services" / "vector_store.py").write_bytes(b"PRE-REPAIR vector_store")
    (root / "swarm_os" / "services" / "other.py").write_bytes(b"PRE-REPAIR other")
    # Now the repair changed them on disk to the post-repair content.
    (root / "swarm_os" / "services" / "vector_store.py").write_bytes(b"FIXED content")
    (root / "swarm_os" / "services" / "other.py").write_bytes(b"also changed")

    # Scope = only vector_store.py: restore must revert it but NOT other.py.
    restored = rs.restore_run_snapshot({"snapshot": snap_payload, "scope": ["swarm_os/services/vector_store.py"]},
                                       scope=["swarm_os/services/vector_store.py"],
                                       root=root)
    assert (root / "swarm_os" / "services" / "vector_store.py").read_bytes() == b"PRE-REPAIR vector_store"
    # other.py keeps its post-repair content (outside scope, untouched).
    assert (root / "swarm_os" / "services" / "other.py").read_bytes() == b"also changed"
    assert "swarm_os/services/vector_store.py" in restored["restored"]
    assert "swarm_os/services/other.py" not in restored["restored"]


# ── conflict-aware rollback ─────────────────────────────────────────────────
def _make_mutation(tmp_path, mutation_id="m1", target_rel="swarm_os/services/x.py"):
    mdir = tmp_path / ".data" / "pending_mutations" / mutation_id
    mdir.mkdir(parents=True, exist_ok=True)
    repo = tmp_path
    target = repo / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PRE-REPAIR")
    pending = mdir / "pending.py"
    pending.write_bytes(b"REPAIRED")
    meta = {
        "pending_file": str(pending),
        "target_path": str(target),
    }
    (mdir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return MutationRepository(root_dir=str(tmp_path / ".data" / "pending_mutations")), target


def test_rollback_clean_restores_bak(tmp_path):
    repo, target = _make_mutation(tmp_path)
    repo.approve("m1")
    assert target.read_bytes() == b"REPAIRED"
    result = repo.rollback("m1")
    assert result["ok"] is True
    assert result["reason"] == "rolled_back"
    assert target.read_bytes() == b"PRE-REPAIR"


def test_rollback_touch_without_content_change_still_rolls_back(tmp_path):
    """Content-based comparison: a touch that changes only mtime (no content
    change) must NOT be misread as a conflict — the content really is what the
    repair wrote, so rollback proceeds (opposite of the metadata failure)."""
    repo, target = _make_mutation(tmp_path)
    repo.approve("m1")
    assert target.read_bytes() == b"REPAIRED"
    # Simulate a later 'touch' (rewrite same bytes) by copying the approved
    # content back with a new mtime.
    target.write_bytes(b"REPAIRED")
    result = repo.rollback("m1")
    assert result["ok"] is True
    assert result["reason"] == "rolled_back"
    assert target.read_bytes() == b"PRE-REPAIR"


def test_rollback_conflict_refuses_not_clobber(tmp_path):
    """A genuine later write to the target (different bytes) must REFUSE — never
    silently clobber the second repair's work with the .bak."""
    repo, target = _make_mutation(tmp_path)
    repo.approve("m1")
    assert target.read_bytes() == b"REPAIRED"
    # A second, unrelated repair changes the file after approval.
    target.write_bytes(b"SECOND REPAIR")
    result = repo.rollback("m1")
    assert result["ok"] is False
    assert result["reason"] == "refused_conflict"
    assert "file modified since repair" in result["detail"]
    # The second repair's content stays intact (not clobbered).
    assert target.read_bytes() == b"SECOND REPAIR"


def test_rollback_unavailable_missing_metadata(tmp_path):
    repo = MutationRepository(root_dir=str(tmp_path / ".data" / "pending_mutations"))
    result = repo.rollback("nonexistent")
    assert result["ok"] is False
    assert result["reason"] == "unavailable"


def test_rollback_unavailable_missing_bak(tmp_path):
    repo, target = _make_mutation(tmp_path)
    repo.approve("m1")  # records backup_path
    meta_path = tmp_path / ".data" / "pending_mutations" / "m1" / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["backup_path"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    result = repo.rollback("m1")
    assert result["ok"] is False
    assert result["reason"] == "unavailable"


def test_rollback_legacy_metadata_without_approved_bytes_refuses(tmp_path):
    """A mutation approved before the approved_bytes capture cannot verify
    unchanged-since-repair — refuse (never guess)."""
    repo, target = _make_mutation(tmp_path)
    repo.approve("m1")
    meta_path = tmp_path / ".data" / "pending_mutations" / "m1" / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["approved_bytes_hex"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    result = repo.rollback("m1")
    assert result["ok"] is False
    assert result["reason"] == "refused_conflict"
    assert "approved_bytes_hex" in result["detail"] or "approval-time" in result["detail"]

# ── Phase A wiring: snapshot captured BEFORE the repair writes ──────────────
@pytest.mark.asyncio
async def test_watch_loop_captures_snapshot_before_repair_write(monkeypatch, tmp_path):
    """The snapshot must be captured BEFORE the repair's write — captured-after
    would hold post-repair bytes, making restore a silent no-op. Drive the real
    `_handle_tool_result` with a fake engine that writes POST-REPAIR content,
    then load the durable snapshot and assert it holds PRE-REPAIR bytes."""
    import json
    import subprocess
    from types import SimpleNamespace
    import swarm_os.services.watch_loop as wl
    import runtime_v2.services.run_snapshot as rs
    import swarm_os.services.autonomy_policy as _ap_mod

    # Build a real git repo in tmp_path so snapshot_worktree's git diff works.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg").mkdir()
    target = repo / "pkg" / "bug.py"
    target.write_bytes(b"COMMITTED\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    # Pre-repair user edit (differs from HEAD — snapshot_worktree captures this).
    target.write_bytes(b"PRE-REPAIR\n")

    # Isolate watch-loop + snapshot paths.
    monkeypatch.setattr(wl, "_EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(wl, "_HEARTBEAT_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wl, "_AUDIT_FILE", tmp_path / "auto_repairs.jsonl")
    monkeypatch.setattr(wl, "_AGENTS_MD", tmp_path / "AGENTS.md")
    monkeypatch.setattr(rs, "_SNAPSHOT_DIR", tmp_path / "run_snapshots")
    monkeypatch.setattr(_ap_mod, "get_autonomy_policy", lambda **k: SimpleNamespace(daily_budget=50))

    # Fake engine: writes POST-REPAIR (simulating the repair write). The snapshot
    # must be captured BEFORE this call runs.
    def _fake_repair(err, file_path=None):
        target.write_bytes(b"POST-REPAIR\n")
        return {"fixed": True, "tier_used": 0}
    loop = wl.WatchLoop(SimpleNamespace(diagnose_and_repair=_fake_repair), interval_seconds=0.01)
    loop._load_policy()

    # Patch snapshot_worktree to use our git repo as root.
    def _fake_worktree(root):
        from organism_console._commands_opencode import snapshot_worktree
        return snapshot_worktree(repo)
    monkeypatch.setattr(wl.WatchLoop, "_capture_repair_snapshot",
                        lambda self, fp: rs.write_run_snapshot(
                            rs.build_repair_snapshot(_fake_worktree(None), scope=["pkg/bug.py"])))

    _write = json.dumps({"event_type": "tool_result", "payload": {
        "result": {"ok": False, "error": "boom"}, "arguments": {"file_path": "pkg/bug.py"}}}) + "\n"
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(_write)
    loop._handle(json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1]))

    # The repair wrote POST; but the durable snapshot must hold PRE.
    assert target.read_bytes() == b"POST-REPAIR\n"
    snaps = list((tmp_path / "run_snapshots").glob("*.json"))
    assert snaps, "expected at least one durable snapshot"
    loaded = rs.load_run_snapshot(snaps[0].stem)
    assert loaded is not None
    tracked = (loaded.get("snapshot") or {}).get("tracked", {})
    assert tracked.get("pkg/bug.py") == b"PRE-REPAIR\n", "snapshot must hold PRE-repair bytes"

