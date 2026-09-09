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


def test_readonly_github_modes_are_allow_not_confirm():
    """Read-only github_research (discover/verify) must classify as ALLOW so a
    research chain runs without a human approval; install stays gated.

    Regression for the 2026-09-08 audit (C1): run() only extracted
    operation/action/op, but github_research uses the 'mode' key — so the
    policy's github_research branch (which ALLOWs discover/verify) never fired
    and every read-only call was over-gated to CONFIRM.
    """
    from runtime_v2.services.tool_executor import run
    from swarm_os.services.approval_registry import ALLOW, agent_tool_policy

    assert agent_tool_policy("github_research", "discover") == ALLOW
    assert agent_tool_policy("github_research", "verify") == ALLOW
    assert agent_tool_policy("github_research", "install") != ALLOW

    # End-to-end: discover (read-only) must NOT request confirmation.
    first = asyncio.run(run("github_research", {"mode": "discover", "query": "ollama"}, auth=None))
    assert first.get("status") != "confirmation_required", first
    assert first.get("ok") is True


@pytest.mark.asyncio
async def test_github_malformed_and_huge_limit_do_not_raise(monkeypatch):
    """A non-numeric or oversized 'limit' must degrade to a sane value (default 8
    or clamped [1,50]), not raise a raw ValueError out of the dispatch try-block.

    Regression for the 2026-09-08 audit (C3): `limit = int(payload["limit"])`
    sat OUTSIDE the try, so a malformed limit escaped as ValueError.
    """
    class _P:
        def __init__(self, out, rc=0): self._o=out; self.returncode=rc
        async def communicate(self): return self._o, b""
        def kill(self): self.returncode=-9
        async def wait(self): return None

    async def _fake_spawn(*_a, **_k):
        return _P(json.dumps([{"fullName": "a/b", "stargazersCount": 1}]).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_spawn)
    from runtime_v2.services import tool_executor as te
    te.reset_exploration_state()

    # read-only discover -> ALLOW -> executes; malformed/huge limit must not raise
    r = await te.run("github_research", {"mode": "discover", "query": "ollama", "limit": "eight"}, auth=None)
    assert r.get("ok") is True, r
    r2 = await te.run("github_research", {"mode": "discover", "query": "ollama", "limit": 50000}, auth=None)
    assert r2.get("ok") is True, r2