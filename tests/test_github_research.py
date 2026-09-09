"""Regression tests for the restored `github_research` agent tool.

The tool was rebuilt as a native async `gh` CLI subprocess (`_run_gh`) after the
previous implementation booted `pwsh -File` against `qwen_train/scripts/*.ps1`
— scripts that never existed in git, so the tool always failed while being
advertised. Also covers the MCP github-server token fallback (`_merge_env`),
without which an empty-string config token leaves that server unauthenticated.
"""

import asyncio
import json

import pytest

from swarm_os.lib.mcp.mcp_client import _merge_env


class _FakeProc:
    def __init__(self, out: bytes, rc: int = 0, err: bytes = b""):
        self._out = out
        self._err = err
        self.returncode = rc

    async def communicate(self):
        return self._out, self._err

    def kill(self):  # pragma: no cover - defensive
        self.returncode = -9

    async def wait(self):  # pragma: no cover - defensive
        return None


@pytest.mark.asyncio
async def test_run_gh_discover_parses_json(monkeypatch):
    """`_run_gh` runs `gh search` and returns the parsed JSON repo list."""
    repo_payload = [
        {
            "fullName": "ollama/ollama",
            "stargazersCount": 180490,
            "url": "https://github.com/ollama/ollama",
            "language": "Go",
        }
    ]
    fake = _FakeProc(json.dumps(repo_payload).encode())

    async def _fake_spawn(*_a, **_k):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_spawn)

    from runtime_v2.services.tool_executor import _run_gh

    r = await _run_gh(["search", "repos", "ollama"])
    assert r["ok"] is True
    assert r["rc"] == 0
    assert isinstance(r["out"], list)
    assert r["out"][0]["fullName"] == "ollama/ollama"


@pytest.mark.asyncio
async def test_run_gh_nonzero_rc_returns_error(monkeypatch):
    """A failing `gh` call surfaces stderr as an error, not false success."""
    fake = _FakeProc(b"", rc=1, err=b"invalid repo name")

    async def _fake_spawn(*_a, **_k):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_spawn)

    from runtime_v2.services.tool_executor import _run_gh

    r = await _run_gh(["api", "repos/not/valid"])
    assert r["ok"] is False
    assert "invalid repo name" in r["error"]


@pytest.mark.asyncio
async def test_run_gh_timeout_kills_child(monkeypatch):
    """On timeout the child is killed+awaited and an error is returned (no
    orphaned `gh` process — mirrors sandbox_repl's cancel-safe kill)."""

    class _HungProc:
        returncode = None

        async def communicate(self):
            raise asyncio.TimeoutError()

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return None

    hung = _HungProc()

    async def _fake_spawn(*_a, **_k):
        return hung

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_spawn)

    from runtime_v2.services.tool_executor import _run_gh

    r = await _run_gh(["api", "repos/slow/repo"], timeout=0.1)
    assert r["ok"] is False
    assert "timed out" in r["error"]
    assert hung.returncode == -9  # child was killed


def test_merge_env_empty_config_token_dropped():
    """An empty-string config env must NOT clobber a real process/.env value."""
    base = {"GITHUB_TOKEN": "real-secret", "OPENAI_API_KEY": "openai-key"}
    cfg_empty = {"GITHUB_PERSONAL_ACCESS_TOKEN": ""}
    merged = _merge_env(base, cfg_empty)
    assert merged == base  # empty entry dropped, os.environ value wins


def test_merge_env_nonempty_config_wins():
    """A non-empty config env value deliberately overrides the base."""
    base = {"GITHUB_PERSONAL_ACCESS_TOKEN": "from-env"}
    cfg = {"GITHUB_PERSONAL_ACCESS_TOKEN": "from-config"}
    merged = _merge_env(base, cfg)
    assert merged["GITHUB_PERSONAL_ACCESS_TOKEN"] == "from-config"


def test_merge_env_none_cfg_returns_base_copy():
    base = {"A": "1"}
    merged = _merge_env(base, None)
    assert merged == {"A": "1"}
    merged["A"] = "2"  # must not mutate the caller's dict
    assert base == {"A": "1"}