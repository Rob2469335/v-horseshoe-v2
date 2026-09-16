"""SWE-only confined shell (`sandbox_repl` language=bash).

Why: the agent's own trajectory showed it looping because its shell was
crippled — `sandbox_repl` rejected `bash` and the python branch denies
`open`/`pathlib`, so inspecting one file failed repeatedly until the turn budget
was gone. The strongest minimal SWE agent (mini-swe-agent, >74% on SWE-bench
Verified) gives the model a plain shell; a general agent scoring 0% through a
harness is a reported failure mode (swe-agent/swe-agent#1497).

Scope: the shell exists ONLY for an ISOLATED workspace. The project's own repo
keeps the stricter tools, so this is not a global loosening of the CLI.
"""

from __future__ import annotations

import asyncio

import pytest

from swarm_os.capabilities.sandbox_repl import SandboxReplHandler


def _run(command: str) -> dict:
    return asyncio.run(
        SandboxReplHandler().execute({"language": "bash", "command": command})
    )


@pytest.fixture
def isolated_ws(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
    return ws


def test_shell_runs_and_lists(isolated_ws):
    r = _run("echo HELLO; Get-ChildItem -Name")
    assert r["ok"] is True
    assert "HELLO" in r["stdout"]


def test_shell_reads_a_file(isolated_ws):
    r = _run("Get-Content pkg/m.py")
    assert r["ok"] is True
    assert "x = 1" in r["stdout"]


def test_shell_allows_pipes(isolated_ws):
    # The old powershell gate denied `|`, `;` and `>` — which are how a shell
    # actually inspects code. A real shell must allow them.
    r = _run("Get-ChildItem -Name | Select-Object -First 2")
    assert r["ok"] is True


def test_shell_refused_on_the_project_repo(monkeypatch):
    # SWE-ONLY: without an isolated workspace the shell must refuse.
    monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
    r = _run("echo hi")
    assert r["ok"] is False
    assert "isolated workspace" in r["stderr"]


@pytest.mark.parametrize(
    "command",
    [
        "Remove-Item -Recurse -Force .",
        "git push origin main",
        "curl http://example.com",
        "cd ..; dir",
        "Get-Content ../secret.txt",
        "Get-Content C:\\Windows\\win.ini",
    ],
)
def test_shell_blocks_escapes_and_destructive_ops(isolated_ws, command):
    r = _run(command)
    assert r["ok"] is False, f"not blocked: {command}"
    assert "Security Gate blocked" in r["stderr"]
