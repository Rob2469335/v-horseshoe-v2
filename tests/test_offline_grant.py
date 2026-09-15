"""Scoped offline-rollout grant: sandbox_repl only, expiring, auditable."""

from __future__ import annotations

from swarm_os.services import approval_registry as ar
from swarm_os.services import trust_ledger as tl


def test_offline_grant_relaxes_sandbox_repl_only(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "_GRANTS_PATH", tmp_path / "grants.json")
    # no grant -> ALWAYS_CONFIRM
    assert ar.agent_tool_policy("sandbox_repl") == ar.ALWAYS_CONFIRM
    # a sandbox_repl grant must NOT leak to a different ALWAYS_CONFIRM tool
    other_before = ar.agent_tool_policy("playwright", "click")
    # a scoped grant relaxes ONLY sandbox_repl to ALLOW
    tl.grant("sandbox_repl", 60)
    assert ar.agent_tool_policy("sandbox_repl") == ar.ALLOW
    assert ar.agent_tool_policy("playwright", "click") == other_before
    # revocation restores ALWAYS_CONFIRM
    tl.revoke("sandbox_repl")
    assert ar.agent_tool_policy("sandbox_repl") == ar.ALWAYS_CONFIRM


def test_offline_grantable_allowlist_is_tiny():
    assert ar._OFFLINE_GRANTABLE == {
        "sandbox_repl",
        "filesystem",
        "mcp:serena:find_symbol",
        "mcp:serena:find_referencing_symbols",
    }
