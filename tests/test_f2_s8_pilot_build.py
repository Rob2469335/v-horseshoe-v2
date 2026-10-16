"""Tests for the S8 host-side builder's task resolution.

The pilot's builder was written around a hand-maintained five-task repository
map, a fixed inputs directory and a fixed patch directory.  Phase 2 addresses
arbitrary registered tasks, so the clone URL is derived from the task's own
pinned record through the acquisition layer's canonical ``repo_url_for``, the
environment kind is derived from the DECLARED command, and both directories can
be pointed at a phase's own inputs without disturbing the pilot's defaults.
"""
from __future__ import annotations

import json

import pytest

from qwen_train.f2_s8_acquisition import AcquisitionError
from qwen_train.f2_s8_pilot_build import (
    REPOS,
    UV_ALL_EXTRAS,
    declared_test_cmd,
    resolve_repo_url,
    uses_uv_environment,
)


def _record(tmp_path, task_id: str, repo: str) -> None:
    (tmp_path / f"{task_id}.json").write_text(
        json.dumps({"repo": repo, "test_cmds": ["python -m pytest -rA"]}),
        encoding="utf-8",
    )


class TestRepoUrlResolution:
    def test_the_explicit_pilot_mapping_still_wins(self, tmp_path):
        task_id = sorted(REPOS)[0]
        _record(tmp_path, task_id, "elsewhere/other")
        assert resolve_repo_url(task_id, tmp_path) == REPOS[task_id]

    def test_an_unmapped_task_derives_its_url_from_its_own_record(self, tmp_path):
        _record(tmp_path, "conan-io__conan-18842", "conan-io/conan")
        assert (resolve_repo_url("conan-io__conan-18842", tmp_path)
                == "https://github.com/conan-io/conan.git")

    def test_no_mapping_and_no_record_is_a_skip_not_an_error(self, tmp_path):
        assert resolve_repo_url("nobody__nothing-1", tmp_path) == ""

    def test_a_malformed_record_is_refused_not_guessed(self, tmp_path):
        _record(tmp_path, "bad__record-1", "not-a-repo-identity")
        with pytest.raises(AcquisitionError):
            resolve_repo_url("bad__record-1", tmp_path)


class TestDeclaredCommandAndUvOwnership:
    def test_a_uv_driven_command_owns_its_environment(self):
        assert uses_uv_environment("uv run pytest -rA")
        assert uses_uv_environment(
            "uv run pytest -n auto -rA && uv run pytest -n 4 -rA")

    def test_a_command_that_only_mentions_uv_does_not_own_its_environment(self):
        assert not uses_uv_environment("cat tasks.py && python -m pytest -rA")
        assert not uses_uv_environment("python runtests.py && pytest -rA")

    def test_declared_test_cmd_uses_the_canonical_join(self):
        assert declared_test_cmd(
            {"test_cmds": ["cat tasks.py", "python -m pytest -rA"]}
        ) == "cat tasks.py && python -m pytest -rA"

    def test_the_host_dry_run_binds_the_canonical_command(self):
        """The host dry-run must run the command the registration/guest bind.

        ``f2_s8_pilot_dryrun`` read the raw ``spec["test_cmds"][0]``.  For a
        multi-entry declaration that is a DIFFERENT command than the session
        runs -- the registration and ``f2_s8_pilot_live.build_specs`` bind the
        ``" && "`` chain -- so the pre-delivery validity check would validate a
        command the guest never executes.  This guard fails if the raw read
        returns or the canonical helper stops being reused.
        """
        import inspect

        from qwen_train import f2_s8_pilot_dryrun as dry

        src = inspect.getsource(dry)
        assert '["test_cmds"]' not in src, (
            "the host dry-run must not read the raw test_cmds field; it must go "
            "through the canonical declared command"
        )
        assert "declared_test_cmd" in src, (
            "the host dry-run must reuse the single canonical command helper"
        )
        assert "uses_uv_environment" in src, (
            "the host dry-run must derive uv-ownership from the declared "
            "command, not only from the hand-maintained table"
        )
        # Behavioural anchor: the helper the dry-run now uses resolves the
        # canonical join, not the first list element.
        assert declared_test_cmd(
            {"test_cmds": ["cat tasks.py", "python -m pytest -rA"]}
        ) == "cat tasks.py && python -m pytest -rA"

    def test_pilot_uv_membership_is_reproduced_by_the_declared_command(self):
        """Equivalence for the pilot's five tasks.

        The commands were read from the pinned SWE-bench-Live corpus on
        2026-10-15; uv membership must equal what the declared command implies,
        which is what makes the derivation safe to generalise to phase 2.
        """
        commands = {
            "openai__openai-agents-python-1633": "uv run pytest -rA",
            "openai__openai-agents-python-1636": "uv run pytest -rA",
            "stanfordnlp__dspy-8739": "pytest -vv -rA tests/",
            "python__mypy-19767": "pytest -rA",
            "HypothesisWorks__hypothesis-4532":
                "pytest -rA hypothesis-python/tests/cover/",
        }
        for task_id, command in commands.items():
            assert uses_uv_environment(command) == (task_id in UV_ALL_EXTRAS)
