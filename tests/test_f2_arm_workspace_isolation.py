"""F2 per-arm filesystem isolation (design §13.5).

Proves the actual filesystem invariant, not merely that a helper was called:
each F2 arm starts from the declared ``base_commit`` + authorized test patch, so
an arm never inherits another arm's (or a failed attempt's) repository state.

Uses real temporary git repositories and the real ``arm_workspace`` helpers.
No F2 arm, model, backend or Qdrant is started.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from qwen_train.arm_workspace import (
    FreshArmWorkspaceError,
    prepare_arm_workspace,
    prepare_fresh_arm_workspace,
    resolve_task_repo,
    resolve_task_test_patch,
)

# Captured at import (before `tests/conftest.py`'s autouse global_subprocess_mock
# patches it) so these tests exercise REAL `git`.
_REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def _use_real_subprocess(global_subprocess_mock):
    """Restore the genuine Popen: this module drives real git subprocesses."""
    subprocess.Popen = _REAL_POPEN
    yield

_TEST_PATCH = (
    "diff --git a/target.txt b/target.txt\n"
    "--- a/target.txt\n"
    "+++ b/target.txt\n"
    "@@ -1 +1 @@\n"
    "-orig\n"
    "+patched\n"
)


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
    )
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout.strip()


def _make_task(tmp_path: Path) -> tuple[Path, Path, str]:
    """A probe-layout task: ``<inst>/repo`` at base_commit + ``test_patch.diff``."""
    inst = tmp_path / "instance"
    repo = inst / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "src.txt").write_text("base\n", encoding="utf-8")
    (repo / "target.txt").write_text("orig\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (inst / "test_patch.diff").write_text(_TEST_PATCH, encoding="utf-8")
    return inst, repo, base


def _add_future_history(repo: Path) -> str:
    """Simulate a full clone: a future commit (the 'gold fix') on another branch
    plus a tag, reachable from refs that are not ancestors of base_commit."""
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-q", "-b", "future")
    (repo / "target.txt").write_text("GOLD-FIX\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "gold fix")
    future = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "gold-fix")
    _git(repo, "checkout", "-q", branch)
    return future


def _dirty(repo: Path) -> None:
    (repo / "src.txt").write_text("DIRTY-MUTATION\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")


class TestPrepareArmWorkspace:
    def test_fresh_workspace_resets_to_base_plus_patch(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        _dirty(repo)
        returned = prepare_arm_workspace(inst, base)
        assert returned == repo
        assert _git(repo, "rev-parse", "HEAD") == base
        assert (repo / "src.txt").read_text(encoding="utf-8") == "base\n"  # mutation gone
        assert (repo / "target.txt").read_text(encoding="utf-8") == "patched\n"  # patch applied
        assert not (repo / "untracked.txt").exists()
        assert not (repo / "ignored.txt").exists()  # clean -fdx removes ignored too

    def test_cross_arm_contamination_removed(self, tmp_path):
        """Arm A's mutation cannot be observed by arm B."""
        inst, repo, base = _make_task(tmp_path)
        prepare_arm_workspace(inst, base)  # arm A starts isolated
        (repo / "src.txt").write_text("ARM-A-EDIT\n", encoding="utf-8")  # arm A mutates
        prepare_arm_workspace(inst, base)  # arm B reset
        assert (repo / "src.txt").read_text(encoding="utf-8") == "base\n"

    def test_repeated_attempt_contamination_removed(self, tmp_path):
        """A dirty/failed previous attempt cannot contaminate the next."""
        inst, repo, base = _make_task(tmp_path)
        _dirty(repo)
        (repo / "target.txt").write_text("BROKEN-HALF-APPLIED\n", encoding="utf-8")
        prepare_arm_workspace(inst, base)
        assert (repo / "target.txt").read_text(encoding="utf-8") == "patched\n"
        assert (repo / "src.txt").read_text(encoding="utf-8") == "base\n"

    def test_wrong_base_commit_fails_closed(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        with pytest.raises(FreshArmWorkspaceError):
            prepare_arm_workspace(inst, "0" * 40)

    def test_empty_base_commit_fails_closed(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        with pytest.raises(FreshArmWorkspaceError):
            prepare_arm_workspace(inst, "")

    def test_unauthorized_modification_not_inherited(self, tmp_path):
        """Only the authorized test patch may remain dirty after preparation."""
        inst, repo, base = _make_task(tmp_path)
        (repo / "unauthorized.txt").write_text("stale\n", encoding="utf-8")
        prepare_arm_workspace(inst, base)
        porcelain = _git(repo, "status", "--porcelain", "-uall")
        dirty_paths = {ln[3:].strip() for ln in porcelain.splitlines() if ln.strip()}
        assert dirty_paths == {"target.txt"}  # exactly the authorized patch

    def test_missing_patch_fails_closed(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        (inst / "test_patch.diff").unlink()
        with pytest.raises(FreshArmWorkspaceError):
            prepare_arm_workspace(inst, base)

    def test_patch_inside_repo_fails_closed(self, tmp_path):
        """The patch must live outside the cleaned tree."""
        inst, repo, base = _make_task(tmp_path)
        inside = repo / "test_patch.diff"
        inside.write_text(_TEST_PATCH, encoding="utf-8")
        with pytest.raises(FreshArmWorkspaceError):
            prepare_fresh_arm_workspace(repo, base, inside)


class TestHistoryContaminationPrevention:
    """A full clone carries the task's future history (gold fix); the arm must
    not be able to read it (SWE-Bench Pro Verified anti-leak)."""

    def test_future_commit_is_unrecoverable_after_prepare(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        future = _add_future_history(repo)
        assert future in _git(repo, "rev-list", "--all").split()  # reachable BEFORE
        prepare_arm_workspace(inst, base)
        assert _git(repo, "rev-parse", "HEAD") == base
        # the future (gold) commit object is gone
        probe = subprocess.run(
            ["git", "cat-file", "-e", future], cwd=str(repo),
            capture_output=True, text=True, check=False,
        )
        assert probe.returncode != 0
        # no unreachable commit objects survive gc
        fsck = _git(repo, "fsck", "--unreachable", "--no-reflogs")
        assert "unreachable commit" not in fsck

    def test_future_branch_and_tag_removed(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        _add_future_history(repo)
        prepare_arm_workspace(inst, base)
        refs = set(_git(repo, "for-each-ref", "--format=%(refname)").split())
        assert "refs/heads/future" not in refs
        assert "refs/tags/gold-fix" not in refs
        # base tree still intact (patch applied) after stripping
        assert (repo / "target.txt").read_text(encoding="utf-8") == "patched\n"

    def test_orig_head_pseudo_ref_is_removed(self, tmp_path):
        """ORIG_HEAD is a plain file that for-each-ref never enumerates."""
        inst, repo, base = _make_task(tmp_path)
        future = _add_future_history(repo)
        # Put HEAD ON the future commit, then reset away from it: `git reset`
        # writes ORIG_HEAD naming the commit being reset FROM.
        _git(repo, "checkout", "--detach", future)
        _git(repo, "reset", "--hard", base)
        orig = _git(repo, "rev-parse", "ORIG_HEAD")
        assert orig == future  # pre-condition: the leak really exists
        assert (Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "ORIG_HEAD").exists()
        prepare_arm_workspace(inst, base)
        git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
        assert not (git_dir / "ORIG_HEAD").exists(), "ORIG_HEAD survived stripping"
        # And the future commit it named is genuinely unreadable.
        probe = subprocess.run(
            ["git", "cat-file", "-e", future], cwd=str(repo),
            capture_output=True, text=True, check=False,
        )
        assert probe.returncode != 0

    def test_no_pseudo_ref_survives_stripping(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        _add_future_history(repo)
        git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
        for pseudo in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                       "BISECT_HEAD", "AUTO_MERGE", "FETCH_HEAD", "ORIG_HEAD"):
            (git_dir / pseudo).write_text("deadbeef\n", encoding="utf-8")
        prepare_arm_workspace(inst, base)
        survivors = [
            p for p in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                        "BISECT_HEAD", "AUTO_MERGE", "FETCH_HEAD", "ORIG_HEAD")
            if (git_dir / p).exists()
        ]
        assert survivors == []

    def test_dangling_blob_carrying_future_content_is_pruned(self, tmp_path):
        """R10: the unreachable-object assertion must cover EVERY object type.

        A surviving future TREE or BLOB carries the gold patch exactly as
        effectively as a surviving commit, so a commit-only assertion leaves
        the channel open. Here the future commit is made unreachable WITHOUT
        relying on gc to collect its objects, then preparation must fail closed
        or leave nothing behind.
        """
        inst, repo, base = _make_task(tmp_path)
        future = _add_future_history(repo)
        # Drop every ref so the future commit AND its tree/blob become
        # unreachable, then confirm the objects really are present on disk.
        for ref in _git(repo, "for-each-ref", "--format=%(refname)").split():
            if ref != "HEAD":
                _git(repo, "update-ref", "-d", ref)
        fsck_before = _git(repo, "fsck", "--unreachable", "--no-reflogs")
        # git reports an unreferenced TIP as "dangling", an interior object as
        # "unreachable"; either proves future objects are present on disk.
        assert ("unreachable commit" in fsck_before) or ("dangling commit" in fsck_before)
        # prepare_arm_workspace must (a) succeed or (b) fail closed, and in
        # neither case may a future object survive.
        try:
            prepare_arm_workspace(inst, base)
        except FreshArmWorkspaceError:
            pass
        fsck_after = _git(repo, "fsck", "--unreachable", "--no-reflogs")
        for kind in ("unreachable commit", "unreachable blob", "unreachable tree",
                     "dangling commit", "dangling blob", "dangling tree"):
            assert kind not in fsck_after, (
                f"{kind} survived preparation; the gold patch would be readable"
            )
        probe = subprocess.run(
            ["git", "cat-file", "-e", future], cwd=str(repo),
            capture_output=True, text=True, check=False,
        )
        assert probe.returncode != 0


class TestResolveLayout:
    def test_resolves_instance_dir_and_repo_dir(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        assert resolve_task_repo(inst) == repo
        assert resolve_task_repo(repo) == repo

    def test_no_repo_fails_closed(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FreshArmWorkspaceError):
            resolve_task_repo(empty)

    def test_test_patch_is_sibling_of_repo(self, tmp_path):
        inst, repo, base = _make_task(tmp_path)
        assert resolve_task_test_patch(repo) == inst / "test_patch.diff"


def _frozen_manifest(tmp_path: Path, *, with_readiness: bool) -> Path:
    """Freeze + persist a real F2 manifest carrying (or omitting) readiness."""
    from dataclasses import replace

    from runtime_v2.services.f2_freeze import freeze_artifact, persist_manifest, verify_manifest
    from runtime_v2.services.task_readiness import (
        READINESS_CONDITIONS,
        ReadinessEvidence,
        TaskReadiness,
        endpoint_measurable,
        evaluate_readiness,
        manifest_readiness_payload,
    )

    payload = None
    if with_readiness:
        tr = TaskReadiness(task_id="TASK-1", base_commit="deadbeef",
                           relevant_file_set=("src/module.py",))
        ev = ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS})
        v = evaluate_readiness(replace(ev, R8_endpoint_measurable=endpoint_measurable(tr)))
        payload = manifest_readiness_payload(tr, ev, v)
    art = freeze_artifact(rendered_artifact="active block", arm="T",
                          task_id="TASK-1", task_readiness=payload)
    verify_manifest(art)
    return persist_manifest(art, tmp_path)


class TestWorkerPlumbing:
    """The real F2 worker isolates the workspace BEFORE the gated adapter runs."""

    def _manifest(self, tmp_path: Path, *, with_readiness: bool) -> Path:
        return _frozen_manifest(tmp_path, with_readiness=with_readiness)

    @pytest.fixture(autouse=True)
    def _cleanup(self, monkeypatch):
        from runtime_v2.services.f2_replay import clear_replay_state

        monkeypatch.setenv("SWARM_F2_REPLAY", "1")
        yield
        clear_replay_state()
        import os
        os.environ.pop("SWARM_F2_TRAJ_DIR", None)

    def test_worker_isolates_before_adapter_execution(self, tmp_path, monkeypatch):
        from qwen_train import f2_arm_worker as WORKER
        from qwen_train import f2_execution_adapter as ADAPTER

        order: list[tuple] = []
        monkeypatch.setattr(ADAPTER, "bind_task_environment",
                            lambda **k: {"base_commit": "deadbeef"})
        import qwen_train.arm_workspace as AW
        monkeypatch.setattr(AW, "prepare_arm_workspace",
                            lambda ws, bc: order.append(("isolate", str(ws), bc)))

        class _FakeAdapter:
            def __init__(self, *a, **k):
                pass

            def execute_arm_real(self, **k):
                order.append(("execute",))
                return {"mode": "fake", "p2_pid": None, "p2_launcher_pid": None,
                        "serving_pid_source": "none", "backend_healthy": False}

        monkeypatch.setattr(ADAPTER, "F2ExecutionAdapter", _FakeAdapter)
        monkeypatch.setenv("SWARM_F2_REPO_ROOT", str(tmp_path))
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)  # an EXECUTING arm requires an existing workspace
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        manifest = self._manifest(tmp_path, with_readiness=True)
        WORKER.run_worker(argv=[
            "--manifest", str(manifest), "--arm", "T", "--execute",
            "--instance-id", "pypa__twine-1066", "--task-id", "pypa__twine-1066",
        ])
        assert order and order[0][0] == "isolate"
        assert order[0][1] == str((tmp_path / "ws").resolve())
        assert order[0][2] == "deadbeef"
        assert order[-1][0] == "execute"  # isolation happened BEFORE execution

    def test_execute_without_declared_task_fails_closed(self, tmp_path, monkeypatch):
        """--execute with no --instance-id cannot be isolated => fail before spawn."""
        from qwen_train import f2_arm_worker as WORKER
        from qwen_train import f2_execution_adapter as ADAPTER

        constructed = {"n": 0}

        class _FakeAdapter:
            def __init__(self, *a, **k):
                constructed["n"] += 1

            def execute_arm_real(self, **k):
                constructed["n"] += 1
                return {}

        monkeypatch.setattr(ADAPTER, "F2ExecutionAdapter", _FakeAdapter)
        monkeypatch.setenv("SWARM_F2_REPO_ROOT", str(tmp_path))
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)  # an EXECUTING arm requires an existing workspace
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        manifest = self._manifest(tmp_path, with_readiness=True)
        rc = WORKER.run_worker(argv=["--manifest", str(manifest), "--arm", "T", "--execute"])
        assert rc != 0
        assert constructed["n"] == 0  # no adapter / no P2 spawn

    def test_workspace_isolation_does_not_bypass_readiness(self, tmp_path, monkeypatch):
        """Even with isolation succeeding, a no-readiness manifest still fails closed."""
        from qwen_train import f2_arm_worker as WORKER
        from qwen_train import f2_execution_adapter as ADAPTER

        monkeypatch.setattr(ADAPTER, "bind_task_environment",
                            lambda **k: {"base_commit": "deadbeef"})
        import qwen_train.arm_workspace as AW
        monkeypatch.setattr(AW, "prepare_arm_workspace", lambda ws, bc: None)
        monkeypatch.setenv("SWARM_F2_REPO_ROOT", str(tmp_path))
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)  # an EXECUTING arm requires an existing workspace
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        manifest = self._manifest(tmp_path, with_readiness=False)  # NO readiness
        rc = WORKER.run_worker(argv=[
            "--manifest", str(manifest), "--arm", "T", "--execute",
            "--instance-id", "pypa__twine-1066", "--task-id", "pypa__twine-1066",
        ])
        assert rc != 0  # the readiness gate still refuses


class TestExecuteRequiresDeclaredWorkspace:
    """D2: an EXECUTING arm must be told explicitly which filesystem is the
    evaluated task workspace. There is no fallback to the code root.

    The code root is itself a git repository, so a defaulting caller would hand
    the MAIN REPOSITORY to ``prepare_fresh_arm_workspace`` -- whose third step is
    ``git reset --hard <base_commit>`` + ``git clean -fdx``. The invariant is
    enforced here, not left to incidental later failures.
    """

    def test_missing_env_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import (
            FreshArmWorkspaceError,
            resolve_required_workspace_root,
        )

        monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
        with pytest.raises(FreshArmWorkspaceError, match="SWARM_WORKSPACE_ROOT"):
            resolve_required_workspace_root(code_root=tmp_path)

    def test_blank_env_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import (
            FreshArmWorkspaceError,
            resolve_required_workspace_root,
        )

        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "   ")
        with pytest.raises(FreshArmWorkspaceError):
            resolve_required_workspace_root(code_root=tmp_path)

    def test_relative_env_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import (
            FreshArmWorkspaceError,
            resolve_required_workspace_root,
        )

        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "relative/ws")
        with pytest.raises(FreshArmWorkspaceError, match="absolute"):
            resolve_required_workspace_root(code_root=tmp_path)

    def test_nonexistent_env_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import (
            FreshArmWorkspaceError,
            resolve_required_workspace_root,
        )

        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path / "nope"))
        with pytest.raises(FreshArmWorkspaceError, match="not an existing directory"):
            resolve_required_workspace_root(code_root=tmp_path)

    def test_code_root_cannot_be_the_task_workspace(self, tmp_path, monkeypatch):
        """The main repository is rejected even when explicitly declared."""
        from qwen_train.arm_workspace import (
            FreshArmWorkspaceError,
            resolve_required_workspace_root,
        )

        code = tmp_path / "code_root"
        code.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(code))
        with pytest.raises(FreshArmWorkspaceError, match="code root"):
            resolve_required_workspace_root(code_root=code)

    def test_valid_isolated_workspace_resolves(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import resolve_required_workspace_root

        ws = tmp_path / "instance"
        ws.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        assert resolve_required_workspace_root(code_root=tmp_path) == ws.resolve()


class TestWorkerRejectsUndeclaredWorkspace:
    """D2 at the real execution boundary: the worker refuses BEFORE any
    workspace preparation, adapter construction, or model spawn."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, monkeypatch):
        from runtime_v2.services.f2_replay import clear_replay_state

        monkeypatch.setenv("SWARM_F2_REPLAY", "1")
        yield
        clear_replay_state()
        import os

        os.environ.pop("SWARM_F2_TRAJ_DIR", None)

    def _assert_refused_before_execution(self, tmp_path, monkeypatch, manifest):
        from qwen_train import f2_arm_worker as WORKER
        from qwen_train import f2_execution_adapter as ADAPTER
        import qwen_train.arm_workspace as AW

        touched: list[str] = []
        monkeypatch.setattr(AW, "prepare_arm_workspace",
                            lambda *a, **k: touched.append("prepare"))
        monkeypatch.setattr(AW, "resolve_required_workspace_root",
                            _real_resolver_or_raise(touched))

        class _FakeAdapter:
            def __init__(self, *a, **k):
                touched.append("adapter_constructed")

            def execute_arm_real(self, **k):
                touched.append("execute_arm_real")
                return {}

        monkeypatch.setattr(ADAPTER, "F2ExecutionAdapter", _FakeAdapter)
        monkeypatch.setattr(ADAPTER, "bind_task_environment",
                            lambda **k: touched.append("bind") or {"base_commit": "deadbeef"})
        monkeypatch.setenv("SWARM_F2_REPO_ROOT", str(tmp_path))

        rc = WORKER.run_worker(argv=[
            "--manifest", str(manifest), "--arm", "T", "--execute",
            "--instance-id", "pypa__twine-1066", "--task-id", "pypa__twine-1066",
        ])
        assert rc != 0
        # Nothing destructive and no spawn may be reached.
        assert "prepare" not in touched
        assert "adapter_constructed" not in touched
        assert "execute_arm_real" not in touched

    def test_execute_without_workspace_env_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
        manifest = _frozen_manifest(tmp_path, with_readiness=True)
        self._assert_refused_before_execution(tmp_path, monkeypatch, manifest)

    def test_execute_with_relative_workspace_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "relative/ws")
        manifest = _frozen_manifest(tmp_path, with_readiness=True)
        self._assert_refused_before_execution(tmp_path, monkeypatch, manifest)

    def test_execute_with_missing_workspace_dir_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path / "absent"))
        manifest = _frozen_manifest(tmp_path, with_readiness=True)
        self._assert_refused_before_execution(tmp_path, monkeypatch, manifest)

    def test_execute_pointing_at_main_repo_fails_closed(self, tmp_path, monkeypatch):
        """Declaring the MAIN repository as the workspace is refused."""
        code_root = Path(__file__).resolve().parent.parent
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(code_root))
        manifest = _frozen_manifest(tmp_path, with_readiness=True)
        from qwen_train import f2_arm_worker as WORKER
        from qwen_train import f2_execution_adapter as ADAPTER
        import qwen_train.arm_workspace as AW

        touched: list[str] = []
        monkeypatch.setattr(AW, "prepare_arm_workspace",
                            lambda *a, **k: touched.append("prepare"))

        class _FakeAdapter:
            def __init__(self, *a, **k):
                touched.append("adapter_constructed")

        monkeypatch.setattr(ADAPTER, "F2ExecutionAdapter", _FakeAdapter)
        monkeypatch.setenv("SWARM_F2_REPO_ROOT", str(tmp_path))
        rc = WORKER.run_worker(argv=[
            "--manifest", str(manifest), "--arm", "T", "--execute",
            "--instance-id", "pypa__twine-1066", "--task-id", "pypa__twine-1066",
        ])
        assert rc != 0
        assert "prepare" not in touched
        assert "adapter_constructed" not in touched


def _real_resolver_or_raise(touched: list[str]):
    """Wrap the real resolver so a test can see whether it was reached."""
    from qwen_train.arm_workspace import resolve_required_workspace_root

    def _wrapped(*a, **k):
        touched.append("resolve")
        return resolve_required_workspace_root(*a, **k)

    return _wrapped
