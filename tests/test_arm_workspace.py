"""Fresh-arm workspace provisioning for the SWE evaluator.

These tests exercise the REAL invariant:

    HEAD == base_commit
    working tree == base_commit + EXACTLY the authorized test patch

and, mandatorily, that a SECOND arm can re-establish the identical state after
the first arm dirtied the tree.  Everything runs against a TEMPORARY Git
repository; no real SWE workspace is touched, no model or agent runs, no network
is used.

The safety boundary under test: the authorized test patch lives OUTSIDE the
repository, so the ``git clean -fdx`` performed during provisioning cannot
remove it.  Both tests assert the patch exists and is byte-identical after
provisioning, and assert the patch is never inside the cleaned tree.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from qwen_train.arm_workspace import (  # noqa: E402
    FreshArmWorkspaceError,
    patch_touched_paths,
    prepare_fresh_arm_workspace,
    preflight_target_state_with_patch,
    verify_patch_state,
)

# conftest.py autouse-mocks `subprocess.Popen` session-wide so tests cannot
# spawn background servers. This module provisions REAL temporary git repos, so
# the genuine Popen is captured here — at import time, before any fixture runs —
# and restored for this module only.
_REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def _real_subprocess(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)

# A real `git diff` MODIFYING one tracked test file and ADDING one new test
# file. Hunk headers are byte-accurate so `git apply` accepts it unmodified.
_PATCH = """diff --git a/tests/test_added.py b/tests/test_added.py
new file mode 100644
index 0000000..77a6d06
--- /dev/null
+++ b/tests/test_added.py
@@ -0,0 +1,2 @@
+def test_added():
+    assert True
\\ No newline at end of file
diff --git a/tests/test_existing.py b/tests/test_existing.py
index ef27133..46f7255 100644
--- a/tests/test_existing.py
+++ b/tests/test_existing.py
@@ -1,2 +1,3 @@
 def test_one():
+    assert True  # patched
     assert True
\\ No newline at end of file
"""

# Same shape as the base file content so the modification hunk applies cleanly.
_BASE_TEST = "def test_one():\n    assert True"

EXPECTED_PATHS = {"tests/test_existing.py", "tests/test_added.py"}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout.strip()

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a temp Git repo with a source file and a tests/ dir.

    Returns (repo, external_patch_file, base_commit). The patch file is created
    OUTSIDE the repository on purpose.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q"], cwd=str(repo), capture_output=True, check=True
    )
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    # Keep the working copy byte-identical to what git recorded in the patch:
    # on Windows the default autocrlf would rewrite LF -> CRLF and the hunk
    # would no longer apply. The patch under test uses LF.
    _git(repo, "config", "core.autocrlf", "false")

    (repo / "src.py").write_text("VALUE = 1\n", encoding="utf-8", newline="")
    (repo / "tests" / "test_existing.py").write_text(
        _BASE_TEST, encoding="utf-8", newline=""
    )
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8", newline="")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    patch_file = tmp_path / "test_patch.diff"
    patch_file.write_text(_PATCH, encoding="utf-8")
    return repo, patch_file, base


def _dirty(repo: Path, tag: str) -> None:
    """Deliberately dirty: tracked source, ordinary untracked, ignored file."""
    (repo / "src.py").write_text(f"VALUE = 999  # {tag}\n", encoding="utf-8")
    (repo / f"untracked_{tag}.txt").write_text(f"{tag}\n", encoding="utf-8", newline="")
    (repo / f"junk_{tag}.log").write_text(f"{tag}\n", encoding="utf-8", newline="")


def _porcelain(repo: Path) -> set[str]:
    out = _git(repo, "status", "--porcelain", "-uall")
    paths = set()
    for line in out.splitlines():
        if line.strip():
            entry = line[3:].strip().strip('"')
            paths.add(entry.split(" -> ")[-1])
    return paths


# ---------------------------------------------------------------------------
# TEST 1 - first arm establishes the exact state
# ---------------------------------------------------------------------------


def test_first_arm_establishes_exact_state(tmp_path):
    repo, patch_file, base = _build_repo(tmp_path)
    # Patch must NOT be inside the cleaned tree.
    assert not str(patch_file.resolve()).startswith(str(repo.resolve()))
    patch_hash_before = _sha256(patch_file)

    _dirty(repo, "arm1")
    assert _porcelain(repo)  # precondition: tree is dirty

    expected = prepare_fresh_arm_workspace(repo, base, patch_file)

    assert expected == EXPECTED_PATHS
    assert _git(repo, "rev-parse", "HEAD") == base
    assert _porcelain(repo) == EXPECTED_PATHS
    # deliberate dirt is gone
    assert (repo / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (repo / "untracked_arm1.txt").exists()
    assert not (repo / "junk_arm1.log").exists()
    # external patch survived the clean, unchanged
    assert patch_file.is_file()
    assert _sha256(patch_file) == patch_hash_before
    assert patch_touched_paths(patch_file) == EXPECTED_PATHS


# ---------------------------------------------------------------------------
# TEST 2 - SECOND ARM: mandatory re-establishment
# ---------------------------------------------------------------------------


def test_second_arm_reestablishes_identical_state(tmp_path):
    repo, patch_file, base = _build_repo(tmp_path)
    assert not str(patch_file.resolve()).startswith(str(repo.resolve()))
    patch_hash_before = _sha256(patch_file)

    _dirty(repo, "arm1")
    first_expected = prepare_fresh_arm_workspace(repo, base, patch_file)
    first_status = _porcelain(repo)
    first_head = _git(repo, "rev-parse", "HEAD")

    # --- second arm: dirty it again, differently ---
    _dirty(repo, "arm2")
    assert _porcelain(repo) > first_status  # precondition: extra dirt

    second_expected = prepare_fresh_arm_workspace(repo, base, patch_file)

    assert second_expected == first_expected == EXPECTED_PATHS
    assert _git(repo, "rev-parse", "HEAD") == first_head == base
    assert _porcelain(repo) == first_status == EXPECTED_PATHS
    # both arms' dirt is gone
    assert not (repo / "untracked_arm1.txt").exists()
    assert not (repo / "junk_arm1.log").exists()
    assert not (repo / "untracked_arm2.txt").exists()
    assert not (repo / "junk_arm2.log").exists()
    assert (repo / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    # external patch survived BOTH cleans, unchanged
    assert patch_file.is_file()
    assert _sha256(patch_file) == patch_hash_before


def test_third_arm_still_reestablishes(tmp_path):
    """Three consecutive arms: the invariant is not a two-arm coincidence."""
    repo, patch_file, base = _build_repo(tmp_path)
    seen = set()
    for i in range(3):
        _dirty(repo, f"arm{i}")
        prepare_fresh_arm_workspace(repo, base, patch_file)
        seen.add((_git(repo, "rev-parse", "HEAD"), frozenset(_porcelain(repo))))
    assert len(seen) == 1
    assert seen.pop() == (base, frozenset(EXPECTED_PATHS))


# ---------------------------------------------------------------------------
# TEST 3 - flag OFF preserves current behaviour
# ---------------------------------------------------------------------------


def test_flag_off_preflight_still_rejects_dirty_tree(tmp_path):
    """The EXISTING pre-flight is unchanged: dirty tree -> rejected."""
    repo, patch_file, base = _build_repo(tmp_path)
    sys.path.insert(0, str(_REPO_ROOT / "qwen_train"))
    from run_repair_task import _preflight_target_state

    _dirty(repo, "off")
    errors = _preflight_target_state(repo, base)
    assert errors, "existing pre-flight must still reject a dirty workspace"
    assert any("not clean" in e for e in errors)


def test_flag_off_accepts_clean_tree(tmp_path):
    """Existing pre-flight still passes a clean tree at the right commit."""
    repo, _patch, base = _build_repo(tmp_path)
    sys.path.insert(0, str(_REPO_ROOT / "qwen_train"))
    from run_repair_task import _preflight_target_state

    assert _preflight_target_state(repo, base) == []


def test_flag_default_is_off():
    """The flag is opt-in: default OFF in the parser."""
    import argparse
    import inspect

    import run_repair_task

    src = inspect.getsource(run_repair_task)
    assert '"--fresh-arm-workspace", action="store_true"' in src
    _ = argparse


# ---------------------------------------------------------------------------
# TEST 4 - wrong / unauthorized base commit fails closed
# ---------------------------------------------------------------------------


def test_wrong_base_commit_fails_closed(tmp_path):
    repo, patch_file, _base = _build_repo(tmp_path)
    with pytest.raises(FreshArmWorkspaceError):
        prepare_fresh_arm_workspace(
            repo, "0" * 40, patch_file
        )


def test_empty_base_commit_fails_closed(tmp_path):
    repo, patch_file, _base = _build_repo(tmp_path)
    with pytest.raises(FreshArmWorkspaceError):
        prepare_fresh_arm_workspace(repo, "", patch_file)


def test_non_git_dir_fails_closed(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    patch_file = tmp_path / "p.diff"
    patch_file.write_text(_PATCH, encoding="utf-8")
    with pytest.raises(FreshArmWorkspaceError):
        prepare_fresh_arm_workspace(plain, "abc123", patch_file)


def test_patch_inside_repo_is_rejected(tmp_path):
    """Safety: the patch may not live inside the cleaned tree."""
    repo, _patch, base = _build_repo(tmp_path)
    inside = repo / "test_patch.diff"
    inside.write_text(_PATCH, encoding="utf-8")
    with pytest.raises(FreshArmWorkspaceError) as exc:
        prepare_fresh_arm_workspace(repo, base, inside)
    assert "OUTSIDE" in str(exc.value)


def test_missing_patch_file_fails_closed(tmp_path):
    repo, _patch, base = _build_repo(tmp_path)
    with pytest.raises(FreshArmWorkspaceError):
        prepare_fresh_arm_workspace(repo, base, tmp_path / "nope.diff")


# ---------------------------------------------------------------------------
# TEST 5 - exact patch-path verification semantics
# ---------------------------------------------------------------------------


def test_patch_touched_paths_derives_from_patch(tmp_path):
    p = tmp_path / "x.diff"
    p.write_text(_PATCH, encoding="utf-8")
    assert patch_touched_paths(p) == EXPECTED_PATHS


def test_patch_touched_paths_handles_deletion(tmp_path):
    p = tmp_path / "d.diff"
    p.write_text(
        "diff --git a/tests/gone.py b/tests/gone.py\n"
        "deleted file mode 100644\n"
        "index 1111111..0000000\n"
        "--- a/tests/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def test_gone():\n"
        "-\n",        encoding="utf-8",
    )
    assert patch_touched_paths(p) == {"tests/gone.py"}


def test_verify_rejects_unexpected_extra_dirty_path(tmp_path):
    """Exact matching, not 'not too dirty'."""
    repo, patch_file, base = _build_repo(tmp_path)
    prepare_fresh_arm_workspace(repo, base, patch_file)
    # introduce an EXTRA unexpected modification
    (repo / "src.py").write_text("VALUE = 42\n", encoding="utf-8")
    with pytest.raises(FreshArmWorkspaceError) as exc:
        verify_patch_state(repo, EXPECTED_PATHS)
    assert "outside the authorized patch" in str(exc.value)


def test_verify_rejects_missing_expected_path(tmp_path):
    repo, patch_file, base = _build_repo(tmp_path)
    prepare_fresh_arm_workspace(repo, base, patch_file)
    # `git apply --3way` STAGES what it applies, so the added test file is
    # present in the index. Unstage and remove it so the expected path is
    # genuinely absent, then the exact-set check must fail.
    _git(repo, "rm", "-q", "--cached", "tests/test_added.py")
    (repo / "tests" / "test_added.py").unlink()
    with pytest.raises(FreshArmWorkspaceError) as exc:
        verify_patch_state(repo, EXPECTED_PATHS)
    assert "absent" in str(exc.value)


def test_added_patch_file_is_staged_not_untracked(tmp_path):
    """Documented consequence of --3way: additions land in the index.

    Both statuses are ordinary working-tree changes, which is why the exact
    check compares PATHS rather than status letters.
    """
    repo, patch_file, base = _build_repo(tmp_path)
    prepare_fresh_arm_workspace(repo, base, patch_file)
    raw = _git(repo, "status", "--porcelain", "-uall")
    assert "A  tests/test_added.py" in raw
    assert "M  tests/test_existing.py" in raw


def test_preflight_with_patch_accepts_prepared_workspace(tmp_path):
    repo, patch_file, base = _build_repo(tmp_path)
    prepare_fresh_arm_workspace(repo, base, patch_file)
    assert preflight_target_state_with_patch(repo, base, patch_file) == []


def test_preflight_with_patch_rejects_unprepared_dirty(tmp_path):
    """Pre-flight is NOT weakened: unexpected dirt still fails."""
    repo, patch_file, base = _build_repo(tmp_path)
    _dirty(repo, "pre")
    errors = preflight_target_state_with_patch(repo, base, patch_file)
    assert errors, "patch-aware pre-flight must reject unexpected dirt"


# ---------------------------------------------------------------------------
# TEST 6 - evaluator identity unchanged
# ---------------------------------------------------------------------------


def test_evaluator_identity_unchanged():
    from swarm_os.services.prompt_repairer import EVALUATOR_ID, EVALUATOR_VERSION

    assert EVALUATOR_ID == "benchmark-evaluator"
    assert str(EVALUATOR_VERSION) == "1"


def test_run_swe_harness_passes_both_flags():
    """Command construction: --fresh-arm-workspace AND --test-patch."""
    import inspect

    from swarm_os.services import prompt_repairer

    src = inspect.getsource(prompt_repairer.BenchmarkEvaluator._run_swe_harness)
    assert '"--fresh-arm-workspace"' in src
    assert '"--test-patch"' in src
    assert "test_patch.diff" in src
