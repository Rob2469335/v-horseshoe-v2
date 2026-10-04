"""F2 P3/task filesystem binding.

Proves the actual P3 task CLI subprocess runs with the authoritative isolated
workspace as its effective cwd, so a RELATIVE filesystem write lands in the
workspace and never the main repository, and that the console's task-facing
PROJECT_ROOT follows the same workspace.

No real F2 arm / model / backend / Qdrant is started.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qwen_train import run_curriculum as RC

REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def _real_subprocess(global_subprocess_mock):
    """This module drives real git/python subprocesses; restore the real Popen."""
    subprocess.Popen = _REAL_POPEN
    yield


class TestP3ProcessBinding:
    def test_uses_workspace_cwd_when_set(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        cwd, env = RC._p3_process_binding()
        assert Path(cwd) == ws.resolve()
        # code root stays importable via PYTHONPATH (code root != task root)
        assert env is not None
        assert str(REPO_ROOT) in env["PYTHONPATH"].split(os.pathsep)

    def test_defaults_to_code_root_when_unset(self, monkeypatch):
        monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
        cwd, env = RC._p3_process_binding()
        assert Path(cwd) == REPO_ROOT
        assert env is None  # behaviour unchanged when no isolated workspace

    def test_fails_closed_on_relative_workspace(self, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "relative/path")
        with pytest.raises(ValueError):
            RC._p3_process_binding()

    def test_fails_closed_on_missing_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path / "does-not-exist"))
        with pytest.raises(ValueError):
            RC._p3_process_binding()

    def test_attempt_once_passes_workspace_cwd(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        captured: dict = {}

        class _P:
            def __init__(self, *a, **k):
                captured.update(k)
                raise SystemExit("captured")

        monkeypatch.setattr(RC.subprocess, "Popen", _P)
        with pytest.raises(SystemExit):
            RC._attempt_once({"prompt": "p"}, timeout=1, allow_approval=False, record=False)
        assert Path(captured["cwd"]) == ws.resolve()
        assert captured.get("env") is not None


class TestFilesystemContainment:
    def test_relative_write_lands_in_workspace_not_main_repo(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        cwd, env = RC._p3_process_binding()  # the SAME binding _attempt_once uses
        subprocess.run(
            [sys.executable, "-c", "open('p3_marker.txt', 'w').write('x')"],
            cwd=cwd, env=env, check=True,
        )
        assert (ws / "p3_marker.txt").exists()
        assert not (REPO_ROOT / "p3_marker.txt").exists()

    def test_cross_arm_workspaces_are_independent(self, tmp_path, monkeypatch):
        a = tmp_path / "arm_a"
        b = tmp_path / "arm_b"
        a.mkdir()
        b.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(a))
        cwd_a, env_a = RC._p3_process_binding()
        subprocess.run(
            [sys.executable, "-c", "open('m.txt', 'w').write('A')"],
            cwd=cwd_a, env=env_a, check=True,
        )
        # Arm B is bound to a DIFFERENT workspace and never sees A's marker.
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(b))
        cwd_b, _ = RC._p3_process_binding()
        assert Path(cwd_b) == b.resolve()
        assert (a / "m.txt").exists()
        assert not (b / "m.txt").exists()


class TestConsoleConfigBinding:
    def _run_config(self, env: dict) -> dict:
        code = (
            "import json; from organism_console.config import PROJECT_ROOT, CODE_ROOT, LOG_DIR; "
            "print(json.dumps({'p': str(PROJECT_ROOT), 'c': str(CODE_ROOT), 'l': str(LOG_DIR)}))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT), env=env,
            capture_output=True, text=True, check=True,
        )
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_project_root_follows_workspace(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        env = dict(os.environ)
        env["SWARM_WORKSPACE_ROOT"] = str(ws)
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        d = self._run_config(env)
        assert Path(d["p"]) == ws.resolve()          # task-facing root = workspace
        assert Path(d["c"]) == REPO_ROOT             # code root unchanged
        assert Path(d["l"]) == REPO_ROOT / "swarm_os" / "logs"  # repo state stays

    def test_project_root_defaults_to_code_root(self):
        env = dict(os.environ)
        env.pop("SWARM_WORKSPACE_ROOT", None)
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        d = self._run_config(env)
        assert Path(d["p"]) == REPO_ROOT
