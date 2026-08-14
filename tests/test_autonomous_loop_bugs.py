"""Tests for the four autonomous loop bug fixes.

Bug #1: Inconsistent read-only goal detection - should be deterministic.
Bug #2: Verification loop fails read-only-style goals unconditionally - should skip
        test suite when no files were modified.
Bug #3: Retry loop doesn't recover from "circular delegation blocked" - should use
        the previous result from the target agent instead of looping.
Bug #4: Git stash prompt shown even when nothing changed - should check git status
        before prompting.
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _reload_autonomous():
    import organism_console.loops.autonomous as mod

    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Bug #1: Inconsistent read-only goal detection (root cause)
# Goal: "analyze my codebase for bugs and search internet for improvements"
#       must be deterministically classified as read-only every time.
# ---------------------------------------------------------------------------

READ_ONLY_GOALS = [
    "analyze my codebase for bugs and search internet for improvements",
    "analyze code quality",
    "review the authentication module",
    "search for TODO comments in src",
    "list all files in the runtime_v2 directory",
    "read the README and summarize it",
    "audit the security posture of the api layer",
    "scan for dead code in organism_console",
    "find all TODO markers",
    "inspect the config module",
    "check the test suite status",
    "show me the current delegation chain",
]

WRITE_GOALS = [
    "fix the bug in login.py",
    "implement a new feature for auth",
    "add unit tests for the user module",
    "refactor the database layer",
    "write a new API endpoint",
    "update the config to use env vars",
    "delete the deprecated helper",
    "remove the unused import",
    "patch the security vulnerability",
    "edit the main entry point",
    "create a new service module",
    "modify the agent router",
]


class TestBug1ReadOnlyDetection:
    """Bug #1: read-only goal detection must be deterministic and consistent."""

    @pytest.mark.parametrize("goal", READ_ONLY_GOALS)
    def test_readonly_goals_detected_consistently(self, goal):
        """Read-only goals must be classified as read-only every single run."""
        mod = _reload_autonomous()
        # Use the same keyword lists the module uses (extract via importlib source).
        # We exercise the real logic by re-importing and inspecting the function body
        # via a lightweight harness that mirrors run_autonomous_goal_loop's logic.
        import re

        src = Path(mod.__file__).read_text(encoding="utf-8")
        # Extract READ_ONLY_KEYWORDS / WRITE_KEYWORDS from the source so we test the
        # exact lists shipped in the file, not a copy.
        ro_match = re.search(r"READ_ONLY_KEYWORDS\s*=\s*\[(.*?)\]", src, re.DOTALL)
        wr_match = re.search(r"WRITE_KEYWORDS\s*=\s*\[(.*?)\]", src, re.DOTALL)
        assert ro_match and wr_match, "READ_ONLY_KEYWORDS / WRITE_KEYWORDS not found"
        ro_kw = [
            w.strip().strip('"').strip("'")
            for w in ro_match.group(1).split(",")
            if w.strip().strip('"').strip("'")
        ]
        wr_kw = [
            w.strip().strip('"').strip("'")
            for w in wr_match.group(1).split(",")
            if w.strip().strip('"').strip("'")
        ]
        goal_lower = goal.lower()
        has_ro = any(kw in goal_lower for kw in ro_kw)
        has_wr = any(kw in goal_lower for kw in wr_kw)
        assert has_ro, f"Goal should match a read-only keyword: {goal!r}"
        assert not has_wr, f"Goal should NOT match a write keyword: {goal!r}"
        assert has_ro and not has_wr

    @pytest.mark.parametrize("goal", WRITE_GOALS)
    def test_write_goals_not_readonly(self, goal):
        """Write goals must NOT be classified as read-only."""
        mod = _reload_autonomous()
        import re

        src = Path(mod.__file__).read_text(encoding="utf-8")
        ro_match = re.search(r"READ_ONLY_KEYWORDS\s*=\s*\[(.*?)\]", src, re.DOTALL)
        wr_match = re.search(r"WRITE_KEYWORDS\s*=\s*\[(.*?)\]", src, re.DOTALL)
        assert ro_match and wr_match, "READ_ONLY_KEYWORDS / WRITE_KEYWORDS not found"
        wr_kw = [
            w.strip().strip('"').strip("'")
            for w in wr_match.group(1).split(",")
            if w.strip().strip('"').strip("'")
        ]
        goal_lower = goal.lower()
        has_wr = any(kw in goal_lower for kw in wr_kw)
        assert has_wr, f"Goal should match a write keyword: {goal!r}"

    def test_deterministic_across_runs(self):
        """The same goal must produce the same classification across 10 runs."""
        goal = "analyze my codebase for bugs and search internet for improvements"
        results = []
        for _ in range(10):
            mod = _reload_autonomous()
            import re

            src = Path(mod.__file__).read_text(encoding="utf-8")
            ro_match = re.search(r"READ_ONLY_KEYWORDS\s*=\s*\[(.*?)\]", src, re.DOTALL)
            wr_match = re.search(r"WRITE_KEYWORDS\s*=\s*\[(.*?)\]", src, re.DOTALL)
            assert ro_match and wr_match, (
                "READ_ONLY_KEYWORDS / WRITE_KEYWORDS not found"
            )
            ro_kw = [
                w.strip().strip('"').strip("'")
                for w in ro_match.group(1).split(",")
                if w.strip().strip('"').strip("'")
            ]
            wr_kw = [
                w.strip().strip('"').strip("'")
                for w in wr_match.group(1).split(",")
                if w.strip().strip('"').strip("'")
            ]
            goal_lower = goal.lower()
            results.append(
                any(kw in goal_lower for kw in ro_kw)
                and not any(kw in goal_lower for kw in wr_kw)
            )
        assert all(r is True for r in results), f"Non-deterministic: {results}"

    def test_analyze_and_search_goal_takes_readonly_path(self):
        """The exact goal that failed the /goal loop — "analyze my codebase for
        bugs and search internet for improvements and upgrades" — must take the
        READ-ONLY path (single tool call via stream_prompt) instead of the
        fix-verification loop that demands file changes and fails with
        "No file changes detected". The old `and not is_multi_step` clause
        misclassified every multi-step research goal (analyze/search/review)
        because the multi-step keywords overlap the read-only keywords."""
        state = MagicMock()
        state.entry_agent = "coordinator"
        state.active_agent = "coordinator"
        state.delegation_chain = []
        state.history = []
        state.save = MagicMock()
        cmd_ctx = MagicMock()
        cmd_ctx.state = state
        cmd_ctx.console = MagicMock()

        called = {}
        import organism_console.loops.autonomous as au

        def _fake_stream_prompt(s, agent, goal, history):
            called["stream"] = True

        with patch.object(au, "stream_prompt", _fake_stream_prompt):
            au.run_autonomous_goal_loop(
                "analyze my codebase for bugs and search internet for improvements and upgrades",
                cmd_ctx,
            )
        assert called.get("stream") is True, (
            "read-only goal must run via stream_prompt (single tool call) — it "
            "was forced into the fix-verification loop instead"
        )
        # It must NOT have gone down the verification-loop path (which would
        # print the 'Autonomous Verification Loop' rule).
        assert not any(
            "Verification Loop" in str(c.return_value) for c in cmd_ctx.console.print.call_args_list
        ), "read-only goal must skip the fix-verification loop"


# ---------------------------------------------------------------------------
# Bug #2: Verification loop fails read-only-style goals unconditionally
# Goal: when the verification loop runs, a no-file-changes attempt must PASS
#       instead of running a test suite that fails.
# ---------------------------------------------------------------------------


class TestBug2NoChangesPasses:
    """Bug #2: attempts with no file changes should skip tests and pass."""

    def _extract_is_no_changes_branch(self):
        mod = _reload_autonomous()
        src = Path(mod.__file__).read_text(encoding="utf-8")
        # The fix adds a git status --porcelain check before run_test_suite.
        # The command is split across lines as ["git", "status", "--porcelain"]
        return (
            '"status"' in src
            and '"--porcelain"' in src
            and "No file changes detected" in src
        )

    def test_no_changes_check_present(self):
        """The verification loop must check git status before running tests."""
        assert self._extract_is_no_changes_branch(), (
            "Expected a `git status --porcelain` check before run_test_suite in "
            "run_autonomous_goal_loop (Bug #2 fix missing)."
        )

    def test_run_test_suite_no_diff_returns_pass(self):
        """run_test_suite should not rubber-stamp a pass when nothing maps.

        The old behavior returned (True, "No specific tests found...") whenever
        the whole working tree was dirty — which it almost always is — so every
        no-test-match run reported success without running anything. Now the
        function returns (None, ...) to signal the caller MUST fall back to
        LLM goal verification instead of declaring success."""
        mod = _reload_autonomous()
        # Patch subprocess.run so no real pytest/git subprocess spawns. The
        # function receives no `changed` set and no test target in the goal
        # text, so it must return the None sentinel (not a bool True).
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = ""
        with patch("subprocess.run", return_value=fake_result):
            passed, msg = mod.run_test_suite("some read-only goal")
        assert passed is None, (
            f"Expected None sentinel (no tests cover), got {passed!r}"
        )
        assert isinstance(msg, str)

    def test_run_test_suite_no_diff_still_runs_pytest_when_target_found(self):
        """When the goal text names a test file, pytest must actually run."""
        mod = _reload_autonomous()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "1 passed"
        with patch("subprocess.run", return_value=fake_result) as mocked:
            passed, msg = mod.run_test_suite("make tests/test_auth.py pass")
        args = mocked.call_args
        assert args is not None
        cmd = args.args[0] if isinstance(args.args[0], list) else args.args[1]
        assert "tests/test_auth.py" in cmd
        assert passed is True

    def test_run_test_suite_no_diff_with_changed_files_runs_pytest(self):
        """Changed files (this attempt) that map to a test file must run pytest."""
        mod = _reload_autonomous()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "1 passed"
        # 'autonomous.py' -> stem 'autonomous' -> matches tests/test_autonomous_loop_bugs.py
        with patch("subprocess.run", return_value=fake_result) as mocked:
            passed, msg = mod.run_test_suite(
                "some goal", changed={"organism_console/loops/autonomous.py"}
            )
        assert passed is True
        args = mocked.call_args
        cmd = args.args[0] if isinstance(args.args[0], list) else args.args[1]
        assert any("test_autonomous" in c for c in cmd)

    def test_run_test_suite_no_diff_with_untested_changes_returns_none(self):
        """Changed files with no discoverable test target must return the None
        sentinel (caller falls back to reviewer), NOT a pass."""
        mod = _reload_autonomous()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = ""
        # A changed file whose stem matches no test file in the tests/ dir.
        # Patch glob to return no test files so no target is derived.
        with (
            patch("subprocess.run", return_value=fake_result),
            patch.object(Path, "glob", return_value=[]),
        ):
            passed, msg = mod.run_test_suite("some goal", changed={"zzz_nomatch.py"})
        assert passed is None, f"Expected None sentinel, got {passed!r}"


# ---------------------------------------------------------------------------
# Bug #3: Retry loop doesn't recover from "circular delegation blocked"
# Goal: when a delegate call is blocked because the target agent is already in
#       the chain, the coordinator should return the prior result via 'final'
#       instead of looping with no-op tool calls.
# ---------------------------------------------------------------------------


class TestBug3CircularDelegationRecovery:
    """Bug #3: circular delegation should recover using the prior result."""

    def test_circular_delegation_recovery_present(self):
        """agent_service_v2.py must contain the recovery branch for circular delegation."""
        svc_path = ROOT / "runtime_v2" / "api" / "agent_service_v2.py"
        src = svc_path.read_text(encoding="utf-8")
        assert "Circular delegation blocked" in src, (
            "Original error message must still exist"
        )
        assert "Recovered from circular delegation" in src, (
            "Expected a recovery branch that returns the previous result via 'final' "
            "when circular delegation is blocked (Bug #3 fix missing)."
        )
        # The recovery branch must yield a "final" chunk, not just continue the loop.
        assert (
            'type": "final"' in src or "type': 'final'" in src or 'type="final"' in src
        )


# ---------------------------------------------------------------------------
# Bug #4: Git stash prompt shown even when nothing changed
# Goal: the max-attempts git stash prompt must be gated on `git status --porcelain`
#       having actual output; if the tree is clean, skip the prompt.
# ---------------------------------------------------------------------------


class TestBug4GitStashGated:
    """Bug #4: git stash prompt must only show when there are changes to stash."""

    def test_git_status_check_before_stash_prompt(self):
        mod = _reload_autonomous()
        src = Path(mod.__file__).read_text(encoding="utf-8")
        # The fix should check git status --porcelain before the Confirm.ask prompt.
        # The command is split as ["git", "status", "--porcelain"]
        assert '"--porcelain"' in src, (
            "Expected a git status --porcelain check before the stash prompt"
        )
        # The stash prompt section should be inside a conditional that checks has_changes
        assert "has_changes" in src, (
            "Expected the stash prompt to be gated on whether there are changes "
            "(Bug #4 fix missing)."
        )

    def test_no_stash_prompt_when_clean(self):
        """When git status is clean, no Confirm.ask should fire."""
        mod = _reload_autonomous()
        # We can't easily run the full loop, but we can verify the control flow by
        # checking the source contains the guard.
        src = Path(mod.__file__).read_text(encoding="utf-8")
        # Must guard the Confirm.ask with has_changes. Match on the prompt text
        # itself (ruff may reflow `Confirm.ask(` across lines), so a plain
        # substring on the whole Confirm.ask( call is unreliable.
        prompt = "Do you want to run `git stash` to revert the broken changes"
        idx_prompt = src.find(prompt)
        assert idx_prompt != -1, "Git stash prompt text not found"
        # Look backwards from the prompt for the has_changes guard
        guard_region = src[max(0, idx_prompt - 400) : idx_prompt]
        assert "has_changes" in guard_region or "if has_changes" in guard_region, (
            "git stash Confirm.ask must be guarded by a has_changes check (Bug #4)"
        )


# ---------------------------------------------------------------------------
# Bug #5: Verification gate rubber-stamps failure as success
# Root cause: (1) run_test_suite returned (True, "No specific tests found...")
# whenever the whole working tree was dirty — which it almost always is — so a
# failed / max-turns run "passed"; (2) the max-turns final ("[System: max turns
# reached]") was yielded as a normal `final` chunk, so last_stream_status read
# "completed" and the run was verified as if it succeeded; (3) the reviewer-verify
# fallback `else: passed = True` treated an unreachable reviewer as a pass.
# ---------------------------------------------------------------------------


class TestBug5VerificationGateNoRubberStamp:
    """Bug #5: the verification gate must fail-closed, never rubber-stamp."""

    @pytest.mark.parametrize(
        "text",
        [
            "[System: max turns reached]",
            "Healing failed. Manual intervention required.",
            "Healing failed. Loop aborted.",
            "Task aborted after 3 LLM failures: timeout",
            "Task aborted after 3 consecutive errors.",
        ],
    )
    def test_system_failure_finals_detected(self, text):
        """System-termination finals must be recognized as failures."""
        mod = _reload_autonomous()
        assert mod._is_system_failure_final(text) is True, (
            f"{text!r} should be a system failure"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Done.",
            "Task completed.",
            "Here is the analysis of your codebase.",
            "I finished the task despite the timeout warning.",
        ],
    )
    def test_non_system_failure_finals_not_detected(self, text):
        """Real completion finals must NOT be flagged as system failures."""
        mod = _reload_autonomous()
        assert mod._is_system_failure_final(text) is False, (
            f"{text!r} should not be a system failure"
        )

    def test_reviewer_verify_yes_passes(self):
        """A reviewer YES verdict passes the goal."""
        mod = _reload_autonomous()
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"response": "YES, the goal was achieved."}
        with patch(
            "organism_console.loops.autonomous.call_api", return_value=fake_resp
        ):
            passed, logs = mod._verify_goal_with_reviewer("some goal", "done")
        assert passed is True

    def test_reviewer_verify_no_fails(self):
        """A reviewer NO verdict must fail the goal (not pass it)."""
        mod = _reload_autonomous()
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "response": "NO: the agent never edited any file."
        }
        with patch(
            "organism_console.loops.autonomous.call_api", return_value=fake_resp
        ):
            passed, logs = mod._verify_goal_with_reviewer("some goal", "done")
        assert passed is False
        assert "Goal verification failed" in logs

    def test_reviewer_verify_unavailable_fails_closed(self):
        """When the reviewer call fails/unreachable, the goal must FAIL (the old
        behavior passed it with 'Verification unavailable')."""
        mod = _reload_autonomous()
        with patch("organism_console.loops.autonomous.call_api", return_value=None):
            passed, logs = mod._verify_goal_with_reviewer("some goal", "done")
        assert passed is False
        assert "unavailable" in logs.lower() or "unavailable" in logs

    def test_reviewer_verify_http_error_fails_closed(self):
        """A non-200 reviewer response must fail the goal."""
        mod = _reload_autonomous()
        fake_resp = MagicMock()
        fake_resp.status_code = 500
        with patch(
            "organism_console.loops.autonomous.call_api", return_value=fake_resp
        ):
            passed, logs = mod._verify_goal_with_reviewer("some goal", "done")
        assert passed is False

    def test_reviewer_verify_exception_fails_closed(self):
        """An exception during the reviewer call must fail the goal."""
        mod = _reload_autonomous()
        with patch(
            "organism_console.loops.autonomous.call_api",
            side_effect=RuntimeError("boom"),
        ):
            passed, logs = mod._verify_goal_with_reviewer("some goal", "done")
        assert passed is False

    def test_loop_source_contains_system_failure_gate(self):
        """The loop must break on system-failure finals instead of verifying them."""
        mod = _reload_autonomous()
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "_is_system_failure_final" in src, "system-failure final gate missing"
        assert "System termination" in src, "system-termination branch missing"
        # The old fail-open pass branch must be gone (reviewer unreachable was a PASS).
        assert "No files were modified. Verification unavailable." not in src, (
            "fail-open 'Verification unavailable' pass branch must be removed"
        )

    def test_run_test_suite_never_returns_true_without_running(self):
        """run_test_suite must never return (True, ...) without actually running
        pytest — the old '(True, "No specific tests found...")' rubber stamp is
        banned."""
        mod = _reload_autonomous()
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "No specific tests found" not in src, (
            "rubber-stamp message must be removed"
        )

    def test_verification_failure_records_reflexion(self, monkeypatch):
        """Event-driven reflexion (reviewer item #3): a goal-loop verification
        failure must immediately write a ReflexionMemory rule keyed to the
        entry agent so the closed learning loop sees CLI goal failures."""
        from unittest.mock import AsyncMock
        import swarm_os.services.reflection_loop as rl

        svc = AsyncMock()
        svc.store_reflexion = AsyncMock()
        monkeypatch.setattr(rl, "get_reflection_service", lambda: svc)

        mod = _reload_autonomous()
        mod._record_verification_reflexion(
            "fix the bug",
            "coder",
            "E   AssertionError: boom\nFile: agent_service.py",
            "Task completed.",
            console=None,
        )

        svc.store_reflexion.assert_awaited_once()
        kwargs = svc.store_reflexion.await_args.kwargs
        assert kwargs["component"] == "coder"
        assert kwargs["action"] == "verification_failed"
        assert "verification failed" in kwargs["failure_reason"]
        assert "coder" in kwargs["do_not_repeat"]

    def test_verification_reflexion_never_raises(self, monkeypatch):
        """A failing reflexion store must never break the goal loop."""
        import swarm_os.services.reflection_loop as rl

        def boom():
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(rl, "get_reflection_service", boom)

        mod = _reload_autonomous()
        # Must return None (no exception propagates).
        assert (
            mod._record_verification_reflexion(
                "fix the bug", "coder", "trace", "done", console=None
            )
            is None
        )


# ---------------------------------------------------------------------------
# Goal-loop verification hardening (2026 autonomy gate)
# Goal: (a) run_test_suite is flake-aware — a first-run failure re-runs only the
#       last-failed subset (--lf); a passing re-run means a flaky test, not a
#       broken agent patch. (b) files changed by the agent are security-scanned
#       (SecurityGate.scan_file) before acceptance, so an LLM patch (possibly
#       drafted from internet research) cannot introduce a banned construct.
# ---------------------------------------------------------------------------


class TestGoalLoopFlakeAwareTests:
    """run_test_suite must re-run the failed subset once (--lf) before declaring
    failure — matching repair_engine._run_related_tests semantics."""

    def _target(self):
        return "make tests/test_auth.py pass"

    def test_first_run_fail_rerun_pass_is_flaky_pass(self):
        mod = _reload_autonomous()
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = "E   AssertionError: boom"
        fail.stderr = ""
        ok = MagicMock()
        ok.returncode = 0
        ok.stdout = "1 passed"
        ok.stderr = ""
        with patch("subprocess.run", side_effect=[fail, ok]) as mocked:
            passed, msg = mod.run_test_suite(self._target())
        assert passed is True, (
            "a first-run failure that passes on the last-failed re-run is a "
            "flake, not a broken patch"
        )
        assert "[flaky]" in msg
        cmds = [c.args[0] for c in mocked.call_args_list]
        assert any("--lf" in c for c in cmds), "must re-run the failed subset with --lf"

    def test_first_run_fail_rerun_fail_is_failure(self):
        mod = _reload_autonomous()
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = "E   AssertionError: boom"
        fail.stderr = ""
        with patch("subprocess.run", side_effect=[fail, fail]):
            passed, msg = mod.run_test_suite(self._target())
        assert passed is False
        assert "[flaky]" not in msg
        assert "AssertionError" in msg

    def test_first_run_pass_no_rerun(self):
        mod = _reload_autonomous()
        ok = MagicMock()
        ok.returncode = 0
        ok.stdout = "1 passed"
        ok.stderr = ""
        with patch("subprocess.run", side_effect=[ok]) as mocked:
            passed, msg = mod.run_test_suite(self._target())
        assert passed is True
        assert mocked.call_count == 1, "passing run must not trigger a --lf rerun"


class TestGoalLoopSecurityGate:
    """Files changed by the agent must pass SecurityGate.scan_file before the
    goal loop accepts them (an LLM patch from internet research must not carry a
    banned construct)."""

    def test_banned_construct_rejected(self, tmp_path, monkeypatch):
        mod = _reload_autonomous()
        bad = tmp_path / "evil.py"
        bad.write_text("import subprocess\nsubprocess.run(['x'])\n", encoding="utf-8")
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        ok, msg = mod._scan_changed_for_security({"evil.py"})
        assert ok is False
        assert "evil.py" in msg
        assert "subprocess" in msg

    def test_clean_file_passes(self, tmp_path, monkeypatch):
        mod = _reload_autonomous()
        good = tmp_path / "good.py"
        good.write_text("def f():\n    return 1 + 2\n", encoding="utf-8")
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        ok, msg = mod._scan_changed_for_security({"good.py"})
        assert ok is True
        assert msg == ""

    def test_non_py_ignored(self, tmp_path, monkeypatch):
        mod = _reload_autonomous()
        # A non-.py file with suspicious content must not be scanned at all.
        (tmp_path / "notes.txt").write_text(
            "import subprocess\n", encoding="utf-8"
        )
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        ok, msg = mod._scan_changed_for_security({"notes.txt"})
        assert ok is True, "non-.py files must not be security-scanned"

    def test_wired_into_accept_path(self):
        """The goal loop's accept path must call the security scan on changed
        files before the test suite."""
        mod = _reload_autonomous()
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "_scan_changed_for_security" in src
        # The scan must be invoked on the files-changed path (before run_test_suite).
        assert "Running security gate on changed files" in src

