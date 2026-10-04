"""Fresh-arm workspace preparation for the SWE evaluator.

Why this exists
---------------
``run_repair_task._preflight_target_state`` rejects any non-empty
``git status --porcelain``.  That check runs BEFORE ``cli_baseline_swe.
_reset_instance``, so a workspace that already carries its task test patch can
never reach the reset that would establish the authorized state: the arm aborts
at preflight and the reset never runs.  Every 14 SWE workspaces currently hold
exactly that state, which makes the paired baseline/candidate evaluation unable
to start.

The invariant this module establishes is therefore not "clean".  It is:

    HEAD == base_commit
    working tree == base_commit + EXACTLY the authorized test patch

i.e. the evaluator must begin every arm from the same known state, and must be
able to re-establish it on every subsequent arm after the previous arm has
dirtied the tree.  "Exactly the authorized test patch" is a *derived* path set,
never a hard-coded task list and never a loose dirtiness threshold.

Design notes
------------
* **The test patch lives OUTSIDE the repository.**  ``WORK/<instance_id>/
  test_patch.diff`` is a sibling of ``repo/``, written by
  ``swe_rebench_probe.fetch_instance`` and ``build_swe_pool``.  That is the
  single authoritative location; this module only reads it.  Because the patch
  is outside the cleaned tree, ``git clean -fdx`` cannot delete it -- that is a
  required safety property, asserted before and after provisioning.
* **Fail closed.**  Every step is checked.  Any failure raises
  ``FreshArmWorkspaceError``; nothing is best-effort, and no step is skipped on
  error.
* **No new identity system.**  The caller supplies the already-authorized
  ``base_commit``.  This module verifies the repository can actually reach that
  commit; it never invents or substitutes one.
* **Pre-flight is not weakened.**  ``prepare_fresh_arm_workspace`` establishes
  the state; the existing preflight then *verifies* it and still rejects
  anything unexpected.  Nothing here ignores dirty state.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# `git apply` failure => the patch cannot establish the authorized state.
class FreshArmWorkspaceError(RuntimeError):
    """Raised when a fresh-arm workspace cannot be established. Fail closed."""


# `+++ b/<path>`  -> a path the patch touches
# `--- a/<path>`  -> a path the patch removes (target is /dev/null)
_ADDED_RE = re.compile(r"^\+\+\+ b/(?P<path>.+?)\s*$")
_REMOVED_RE = re.compile(r"^--- a/(?P<path>.+?)\s*$")


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command, raising on failure. Never raises on git's own exit."""
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise FreshArmWorkspaceError(message)


def patch_touched_paths(test_patch_file: Path) -> set[str]:
    """Return the repository-relative paths the patch touches.

    Derived from the patch itself, never from a task list. Handles the three
    Git statuses a patch can produce for a path:
      * modification  -> ``M  <path>``
      * addition      -> ``A  <path>``  (added file is untracked afterwards)
      * deletion      -> `` D <path>``  (path absent afterwards)

    A path that appears in both a ``+++`` and a ``---`` header is reported
    once.  `/dev/null` sentinels are ignored.
    """
    _require(test_patch_file.is_file(), f"test patch not found: {test_patch_file}")
    text = test_patch_file.read_text(encoding="utf-8", errors="replace")

    paths: set[str] = set()
    for line in text.splitlines():
        # A deletion carries NO `+++ b/<path>` header (the target is
        # /dev/null), so the SOURCE header is the only place its path appears.
        # Additions carry `--- /dev/null` and DO have a `+++ b/<path>` header.
        for pattern in (_ADDED_RE, _REMOVED_RE):
            match = pattern.match(line)
            if not match:
                continue
            candidate = match.group("path").strip()
            if candidate and candidate != "/dev/null":
                paths.add(candidate)
            break

    _require(bool(paths), f"test patch names no paths: {test_patch_file}")
    return paths


def _observed_dirty_paths(repo: Path) -> set[str]:
    """Working-tree paths git currently reports, in porcelain form.

    ``-uall`` expands untracked DIRECTORIES so an added file is reported by its
    full path rather than as a bare directory name.
    """
    proc = _run(
        ["git", "status", "--porcelain", "-uall"], cwd=repo
    )
    _require(proc.returncode == 0, f"git status failed: {proc.stderr.strip()}")
    observed: set[str] = set()
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        # Porcelain v1: 2-char status, space, then path. Renames are "R  old -> new".
        entry = raw[3:].strip() if len(raw) > 3 else ""
        if not entry:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry:
            observed.add(entry)
    return observed


def verify_patch_state(repo: Path, expected_paths: set[str]) -> None:
    """Assert the tree is base + EXACTLY ``expected_paths``. Fail closed.

    Both directions are checked: a missing expected path and an unexpected
    extra path are equally failures. This is what makes "not too dirty"
    insufficient.
    """
    observed = _observed_dirty_paths(repo)
    missing = expected_paths - observed
    unexpected = observed - expected_paths
    _require(
        not missing,
        f"authorized patch paths absent from working tree: {sorted(missing)}",
    )
    _require(
        not unexpected,
        f"working tree has paths outside the authorized patch: {sorted(unexpected)}",
    )


def prepare_fresh_arm_workspace(
    repo: Path,
    base_commit: str,
    test_patch_file: Path,
) -> set[str]:
    """Establish ``HEAD == base_commit`` and tree == base + authorized patch.

    Returns the derived expected-path set. Raises ``FreshArmWorkspaceError``
    on any failure.

    Steps, in order, each verified:
      1. the patch file exists and resolves OUTSIDE ``repo`` (so the
         ``git clean -fdx`` below cannot remove it);
      2. ``repo`` is a Git work tree and ``base_commit`` exists in it;
      3. ``git reset --hard <base_commit>``;
      4. ``git clean -fdx`` (authorised: removes ordinary AND ignored
         leftovers);
      5. ``git apply --check``;
      6. ``git apply``;
      7. ``HEAD == base_commit`` and tree == expected paths exactly.
    """
    repo = Path(repo)
    base_commit = (base_commit or "").strip()
    _require(bool(base_commit), "no authorized base_commit supplied")
    _require(
        (repo / ".git").exists(),
        f"not a git repository (no .git): {repo}",
    )

    # (1) Patch must exist and be OUTSIDE the cleaned tree.
    _require(
        test_patch_file.is_file(),
        f"external test patch does not exist: {test_patch_file}",
    )
    patch_resolved = test_patch_file.resolve()
    repo_resolved = repo.resolve()
    try:
        patch_resolved.relative_to(repo_resolved)
    except ValueError:
        pass  # outside the repo -- the safe and required case
    else:
        raise FreshArmWorkspaceError(
            f"test patch must live OUTSIDE the workspace being cleaned: "
            f"{patch_resolved} is inside {repo_resolved}"
        )

    # (2) base_commit must be a real commit in THIS repository.
    rev = _run(["git", "rev-parse", "--verify", f"{base_commit}^{{commit}}"], cwd=repo)
    _require(
        rev.returncode == 0,
        f"authorized base_commit {base_commit!r} is not a commit in {repo}",
    )

    # (3) Restore tracked files to the authorized commit.
    reset = _run(["git", "reset", "--hard", base_commit], cwd=repo)
    _require(
        reset.returncode == 0,
        f"git reset --hard {base_commit} failed: {reset.stderr.strip()}",
    )

    # (4) Remove ordinary AND ignored leftovers.
    clean = _run(["git", "clean", "-fdx"], cwd=repo)
    _require(
        clean.returncode == 0,
        f"git clean -fdx failed: {clean.stderr.strip()}",
    )

    # (5) Patch must apply to the freshly reset tree. The flag set is the SAME
    # one the existing SWE infrastructure already uses for its first attempt
    # (swe_rebench_probe.fetch_instance, build_swe_pool):
    #   ["git", "apply", "-v", "--3way", "--recount", "--ignore-space-change", tp]
    # Reusing it keeps ONE application mechanism; it is not a new one.
    apply_flags = ["-v", "--3way", "--recount", "--ignore-space-change"]
    check = _run(
        ["git", "apply", "--check", *apply_flags, str(patch_resolved)], cwd=repo
    )
    _require(
        check.returncode == 0,
        f"git apply --check failed for {patch_resolved}: "
        f"{(check.stderr or check.stdout).strip()}",
    )

    # (6) Apply.
    apply_proc = _run(["git", "apply", *apply_flags, str(patch_resolved)], cwd=repo)
    _require(
        apply_proc.returncode == 0,
        f"git apply failed for {patch_resolved}: "
        f"{(apply_proc.stderr or apply_proc.stdout).strip()}",
    )

    # (7) Verify HEAD and the exact expected path set.
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    _require(head.returncode == 0, f"git rev-parse HEAD failed: {head.stderr.strip()}")
    _require(
        head.stdout.strip() == repo_commit_id(base_commit, repo),
        "HEAD does not match the authorized base_commit after preparation",
    )

    expected = patch_touched_paths(test_patch_file)
    verify_patch_state(repo, expected)

    # The patch must still be there after the clean. Defence in depth against a
    # future regression that would place the patch inside the tree.
    _require(
        test_patch_file.is_file(),
        f"external test patch vanished during preparation: {test_patch_file}",
    )
    return expected


def resolve_task_repo(workspace_root: Path | str) -> Path:
    """Locate the git repository the agent edits within an F2 task workspace.

    The probe layout is ``<WORK>/<instance_id>/{repo/, venv/, test_patch.diff}``;
    a caller may hand us either the instance directory or the ``repo`` itself.
    Fail closed when neither is a git repository -- an F2 arm must never run
    against an unidentifiable workspace.
    """
    ws = Path(workspace_root)
    if (ws / ".git").exists():
        return ws
    nested = ws / "repo"
    if (nested / ".git").exists():
        return nested
    raise FreshArmWorkspaceError(
        f"no git repository in F2 task workspace {ws} "
        f"(expected {ws}/.git or {ws}/repo/.git)"
    )


def resolve_task_test_patch(repo: Path) -> Path:
    """The authorized task test patch is a SIBLING of the task repo.

    ``<WORK>/<instance_id>/test_patch.diff`` sits outside ``repo/`` so the
    ``git clean -fdx`` inside the repo cannot remove it (same invariant as the
    F1 evaluator path). Fail closed when absent.
    """
    candidate = Path(repo).parent / "test_patch.diff"
    if candidate.is_file():
        return candidate
    raise FreshArmWorkspaceError(
        f"authorized test patch not found beside task repo {repo}: {candidate}"
    )


def _strip_future_history(repo: Path, base_commit: str) -> None:
    """Reduce the workspace git history to EXACTLY ``base_commit``.

    A full clone carries future commits -- including the task's gold fix --
    reachable from other branches, remote-tracking refs, tags or the reflog, and
    recoverable from unreachable objects via ``git fsck``/``cat-file``. That is
    an evaluation-time answer-leak channel (SWE-Bench Pro Verified, arXiv
    2609.08149; "reconstruct the repository as a fresh single-commit"). Remove
    every ref except HEAD at ``base_commit``, expire reflogs, prune unreachable
    objects, then fail closed if any commit remains reachable beyond base or any
    unreachable commit object survives.
    """
    base = repo_commit_id(base_commit, repo)
    _require(
        _run(["git", "checkout", "--detach", base], repo).returncode == 0,
        "could not detach HEAD at base_commit",
    )
    # Drop the remote so remote-tracking refs cannot be used to recover the fix.
    _run(["git", "remote", "remove", "origin"], repo)
    refs = _run(["git", "for-each-ref", "--format=%(refname)"], repo).stdout.split()
    for ref in refs:
        ref = ref.strip()
        if ref and ref != "HEAD":
            _run(["git", "update-ref", "-d", ref], repo)
    _run(["git", "reflog", "expire", "--expire=now", "--all"], repo)
    gc = _run(["git", "gc", "--prune=now", "--quiet"], repo)
    _require(gc.returncode == 0, f"git gc failed: {gc.stderr.strip()}")
    # Fail closed: nothing reachable beyond base, and no unreachable commits.
    reachable = _run(
        ["git", "rev-list", "HEAD", "--not", base, "--count"], repo
    ).stdout.strip()
    _require(reachable == "0", f"future history still reachable: {reachable} commit(s)")
    fsck = _run(["git", "fsck", "--unreachable", "--no-reflogs"], repo).stdout
    leaked = [ln for ln in fsck.splitlines() if "unreachable commit" in ln]
    _require(not leaked, f"unreachable future commit objects remain: {leaked[:3]}")


def prepare_arm_workspace(workspace_root: Path | str, base_commit: str) -> Path:
    """Establish per-arm isolation for an F2 arm, reusing the F1 machinery.

    Resets the task repository to ``base_commit`` + EXACTLY the authorized test
    patch (via :func:`prepare_fresh_arm_workspace`) before the arm's task
    execution can mutate it. Idempotent across arms because the reset discards
    the previous arm's state before re-applying the patch. Fail closed: returns
    the repo path on success; raises ``FreshArmWorkspaceError`` otherwise.
    """
    repo = resolve_task_repo(workspace_root)
    patch = resolve_task_test_patch(repo)
    prepare_fresh_arm_workspace(repo, base_commit, patch)
    # Contamination prevention: the reset above restores the base tree but a full
    # clone still carries the task's future history (gold fix) reachable from
    # other refs/objects. Strip it so the arm cannot read the answer.
    _strip_future_history(repo, base_commit)
    return repo


def repo_commit_id(rev: str, repo: Path) -> str:
    """Resolve ``rev`` to a full commit id inside ``repo``."""
    proc = _run(["git", "rev-parse", "--verify", f"{rev}^{{commit}}"], cwd=repo)
    _require(
        proc.returncode == 0,
        f"cannot resolve {rev!r} in {repo}: {proc.stderr.strip()}",
    )
    return proc.stdout.strip()


def preflight_target_state_with_patch(
    repo: Path,
    expected_commit: str,
    test_patch_file: Path,
) -> list[str]:
    """Pre-flight for the flag-ON path.

    Keeps every guarantee of ``run_repair_task._preflight_target_state``
    (git repo present, ``HEAD == expected_commit``) and additionally requires
    that the only ordinary working-tree changes are the paths the authorized
    patch represents. Empty list == pass.
    """
    errors: list[str] = []
    if not (Path(repo) / ".git").exists():
        return [f"Target workspace has no .git directory: {repo}"]

    proc = _run(["git", "rev-parse", "HEAD"], cwd=Path(repo))
    actual = proc.stdout.strip()
    if actual != repo_commit_id(expected_commit, Path(repo)):
        errors.append(
            f"Target HEAD mismatch: expected {expected_commit}, got {actual}"
        )

    try:
        expected_paths = patch_touched_paths(test_patch_file)
    except FreshArmWorkspaceError as exc:
        errors.append(f"Authorized test patch unusable: {exc}")
        return errors

    try:
        verify_patch_state(Path(repo), expected_paths)
    except FreshArmWorkspaceError as exc:
        errors.append(f"Target working tree is not base + authorized test patch: {exc}")

    return errors
