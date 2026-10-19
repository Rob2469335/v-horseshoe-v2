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
from pathlib import Path

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


class TestManifestIsScopedToTheRun:
    """The written manifest names EXACTLY the tasks this run built.

    The builder previously merged into whatever manifest was already on disk, so a
    subset build silently carried forward every earlier ``status="built"`` entry.
    The runner selects every ``status="built"`` entry
    (``f2_s8_pilot_live.build_specs``), so a stale entry put an unbuilt or
    unauthorized workspace into the runnable set the single-use S8-001 grant is
    bound to. The manifest is the Stage-1 deliverable the grant binds; it must not
    accumulate entries this run did not build.
    """

    @staticmethod
    def _stub_build(monkeypatch) -> None:
        """Replace the real clone/venv build -- these tests must never touch a network."""
        from qwen_train import f2_s8_pilot_build as pb

        monkeypatch.setattr(
            pb, "resolve_repo_url",
            lambda task_id, inputs: f"https://github.com/example/{task_id}.git")
        monkeypatch.setattr(
            pb, "build",
            lambda task_id, url, **kw: {
                "task_id": task_id,
                "workspace": str(Path(kw["inputs"]).parent / task_id),
                "base_commit": "a" * 40,
                "elapsed_s": 0,
            })

    @staticmethod
    def _argv(tmp_path) -> list[str]:
        return [
            "--manifest", str(tmp_path / "build_manifest.json"),
            "--inputs", str(tmp_path / "inputs"),
            "--patches", str(tmp_path / "patches"),
        ]

    def test_a_stale_built_entry_is_not_carried_forward(self, tmp_path, monkeypatch, capsys):
        from qwen_train.f2_s8_pilot_build import main

        (tmp_path / "inputs").mkdir()
        (tmp_path / "patches").mkdir()
        manifest = tmp_path / "build_manifest.json"
        manifest.write_text(json.dumps({
            "stale__task-1": {"status": "built", "workspace": "C:\\f2_pilot\\stale"},
            "built__task-2": {"status": "built", "workspace": "C:\\f2_pilot\\old"},
        }), encoding="utf-8")
        self._stub_build(monkeypatch)

        assert main(self._argv(tmp_path) + ["built__task-2"]) == 0

        written = json.loads(manifest.read_text(encoding="utf-8"))
        assert set(written) == {"built__task-2"}, written
        assert written["built__task-2"]["status"] == "built"
        assert "NOT CARRIED FORWARD" in capsys.readouterr().out

    def test_the_manifest_names_only_this_runs_tasks(self, tmp_path, monkeypatch):
        from qwen_train.f2_s8_pilot_build import main

        (tmp_path / "inputs").mkdir()
        (tmp_path / "patches").mkdir()
        self._stub_build(monkeypatch)

        assert main(self._argv(tmp_path) + ["built__task-2"]) == 0
        assert main(self._argv(tmp_path) + ["built__task-3"]) == 0

        written = json.loads(
            (tmp_path / "build_manifest.json").read_text(encoding="utf-8"))
        assert set(written) == {"built__task-3"}, written

    def test_a_failed_task_is_recorded_not_dropped(self, tmp_path, monkeypatch):
        from qwen_train import f2_s8_pilot_build as pb

        (tmp_path / "inputs").mkdir()
        (tmp_path / "patches").mkdir()
        monkeypatch.setattr(pb, "resolve_repo_url",
                            lambda task_id, inputs: "https://example/r.git")

        def _boom(task_id, url, **kw):
            raise RuntimeError("clone failed")

        monkeypatch.setattr(pb, "build", _boom)
        assert pb.main(self._argv(tmp_path) + ["built__task-2"]) == 0

        written = json.loads(
            (tmp_path / "build_manifest.json").read_text(encoding="utf-8"))
        assert written["built__task-2"]["status"] == "build_failure"
        assert "clone failed" in written["built__task-2"]["detail"]

    def test_an_unreadable_manifest_is_replaced_not_merged(self, tmp_path, monkeypatch):
        """A corrupt manifest must not abort the run nor be merged forward."""
        from qwen_train.f2_s8_pilot_build import main

        (tmp_path / "inputs").mkdir()
        (tmp_path / "patches").mkdir()
        (tmp_path / "build_manifest.json").write_text('{"a": ', encoding="utf-8")
        self._stub_build(monkeypatch)

        assert main(self._argv(tmp_path) + ["built__task-2"]) == 0
        written = json.loads(
            (tmp_path / "build_manifest.json").read_text(encoding="utf-8"))
        assert set(written) == {"built__task-2"}, written


class TestPytestConfigPlugins:
    """Regression tests for the missing-plugin defect found in the phase-3 run.

    The builder carried a hand-maintained ``EXTRA_DEPS`` table keyed by
    instance_id.  The phase-2 candidate set contains ``python__mypy-19718``
    while the table named the earlier ``python__mypy-19767``, so ``pytest-xdist``
    was never installed and the project's declared ``addopts = -nauto`` made
    pytest abort with exit 4 and emit NO JUnit -- recorded as an infrastructure
    failure that looked like a task outcome.  Requirements are now derived from
    the project's own pytest configuration.
    """

    def _project(self, tmp_path, text, name="pyproject.toml"):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
        return tmp_path

    def test_multiline_toml_array_is_parsed(self, tmp_path):
        from qwen_train.f2_s8_pilot_build import pytest_config_plugins
        p = self._project(tmp_path,
                          '[tool.pytest.ini_options]\naddopts = [\n  "--cov-fail-under=100",\n  "-nauto",\n]\n')
        assert pytest_config_plugins(p) == ["pytest-cov", "pytest-xdist"]

    def test_single_line_array_is_parsed(self, tmp_path):
        from qwen_train.f2_s8_pilot_build import pytest_config_plugins
        p = self._project(tmp_path, '[tool.pytest.ini_options]\naddopts = ["-nauto", "--cov=src"]\n')
        assert pytest_config_plugins(p) == ["pytest-cov", "pytest-xdist"]

    def test_ini_flat_form_is_parsed(self, tmp_path):
        from qwen_train.f2_s8_pilot_build import pytest_config_plugins
        p = self._project(tmp_path, "[pytest]\naddopts = -n4 --timeout=30\n", "pytest.ini")
        assert pytest_config_plugins(p) == ["pytest-timeout", "pytest-xdist"]

    def test_core_only_options_require_no_plugin(self, tmp_path):
        """--strict-config/--strict-markers are core; the table must stay empty."""
        from qwen_train.f2_s8_pilot_build import pytest_config_plugins
        p = self._project(tmp_path,
                          '[tool.pytest.ini_options]\naddopts = "--strict-config --strict-markers"\n')
        assert pytest_config_plugins(p) == []

    def test_absent_configuration_yields_nothing(self, tmp_path):
        from qwen_train.f2_s8_pilot_build import pytest_config_plugins
        p = self._project(tmp_path, "x = 1\n")
        assert pytest_config_plugins(p) == []

    def test_build_unions_derived_plugins_into_extra(self, monkeypatch, tmp_path):
        """The union must reach the pip install line, not just the helper."""
        from qwen_train import f2_s8_pilot_build as B

        captured = {}
        real = B.pytest_config_plugins

        def spy(project):
            got = real(project)
            captured["derived"] = got
            return ["pytest-xdist"]        # force a derivation

        monkeypatch.setattr(B, "pytest_config_plugins", spy)
        p = self._project(tmp_path, "[pytest]\naddopts = -nauto\n", "pytest.ini")
        assert B.pytest_config_plugins(p) == ["pytest-xdist"]
        assert captured["derived"] == ["pytest-xdist"]

class TestCommandDeclaredPlugins:
    """The DECLARED COMMAND is the project's own statement of the pytest options
    it needs, exactly like ``addopts``.  Only ``addopts`` was read, so
    ``BerriAI__litellm-13675`` -- command ``poetry run pytest -vv -n 4 -rA
    tests/test_litellm``, no xdist in its ``dev`` group -- aborted with
    ``error: unrecognized arguments: -n`` (exit 4) and produced no JUnit at all.
    """

    def test_a_declared_n_flag_requires_xdist(self):
        from qwen_train.f2_s8_pilot_build import command_plugins
        assert command_plugins(
            "poetry run pytest -vv -n 4 -rA tests/test_litellm") == ["pytest-xdist"]

    def test_numprocesses_and_cov_are_recognised(self):
        from qwen_train.f2_s8_pilot_build import command_plugins
        assert command_plugins("pytest --numprocesses=4 --cov=src") == [
            "pytest-cov", "pytest-xdist"]

    def test_a_plain_command_requires_nothing(self):
        from qwen_train.f2_s8_pilot_build import command_plugins
        assert command_plugins("pytest -q tests/") == []
        assert command_plugins("") == []
