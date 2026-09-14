"""Scoped Serena per-tool MCP grant (docs/T1_IMPLEMENTATION_SPECS.md:191).

Least privilege: the two Serena read-only symbol ops are ALWAYS_CONFIRM *per
tool* and relax ONLY via a grant on their exact ``mcp:<server>:<tool>`` scope —
a broad ``mcp`` grant must NOT open them. Un-granted, a headless run auto-denies
them (→ ineligible), never silently scoring. Revert-proof: on pre-change source
the mcp branch is plain CONFIRM and the action key is None, so the exact-scope
grant cannot relax and a broad grant would.
"""

import pytest

from swarm_os.services import approval_registry as ar
from swarm_os.services import trust_ledger as tl


@pytest.fixture(autouse=True)
def _isolated_grants(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "_GRANTS_PATH", tmp_path / "grants.json")
    return tl


def test_ungranted_serena_op_is_always_confirm():
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") == ar.ALWAYS_CONFIRM


def test_granted_serena_op_is_allow():
    tl.grant("mcp:serena:find_symbol", 3600)
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") == ar.ALLOW


def test_grant_is_per_tool_not_per_server():
    tl.grant("mcp:serena:find_symbol", 3600)
    assert (
        ar.agent_tool_policy("mcp", "serena:find_referencing_symbols")
        == ar.ALWAYS_CONFIRM
    )


def test_broad_mcp_grant_does_not_open_serena():
    tl.grant("mcp", 3600)
    # a broad mcp grant relaxes ordinary mcp reads...
    assert ar.agent_tool_policy("mcp", "read_file") == ar.ALLOW
    # ...but NOT the ALWAYS_CONFIRM Serena symbol ops (exact scope required).
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") != ar.ALLOW


def test_wildcard_serena_scope_never_relaxes():
    tl.grant("mcp:serena:*", 3600)
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") != ar.ALLOW


def test_other_mcp_ops_stay_confirm():
    assert ar.agent_tool_policy("mcp") == ar.CONFIRM
    assert ar.agent_tool_policy("mcp", "read_file") == ar.CONFIRM


def test_tool_executor_resolves_mcp_server_tool():
    from runtime_v2.services.tool_executor import _policy_action_key

    assert (
        _policy_action_key("mcp", {"server": "serena", "tool": "find_symbol"})
        == "serena:find_symbol"
    )
    # non-mcp tools are unchanged
    assert _policy_action_key("filesystem", {"operation": "read"}) == "read"
    assert _policy_action_key("github_research", {"mode": "discover"}) == "discover"
