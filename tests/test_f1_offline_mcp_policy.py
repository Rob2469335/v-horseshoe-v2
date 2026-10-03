"""Regression tests for the Experiment J offline-MCP authorization property.

Security property under test
---------------------------
A normal Experiment J learning run must NOT silently obtain network-capable or
repository-mutating MCP capability.

Before this change `qwen_train/run_repair_task.py` issued a blanket
``grant("mcp", 8 * 3600)``. `agent_tool_policy` relaxes a CONFIRM classification
for ANY active scoped trust grant, so that single line made every CONFIRM-gated
MCP action auto-approved for eight hours - including the GitHub MCP server's
mutation tools (``push_files``, ``create_pull_request``, ``create_or_update_file``,
``create_issue``, ``create_branch``), which `swarm_config.json` launches with NO
``--read-only``/``--tools`` restriction and a PAT credential. The harness's
``allow_approval=True`` (run_repair_task.py:872) would additionally auto-answer
any remaining approval prompt.

The fix uses the existing fail-closed mechanism rather than a parallel
authorization system: under ``SWARM_F1_OFFLINE_MCP=1`` every non-local ``mcp``
action classifies as DENY. DENY is never relaxed by a grant and the tool
executor short-circuits it BEFORE raising a prompt, so neither the blanket grant
nor the auto-answering harness can reopen it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm_os.services import approval_registry as ar
from swarm_os.services import trust_ledger as tl

REPO = Path(__file__).resolve().parents[1]

# Every non-local MCP server in swarm_config.json that reaches the network.
NETWORK_MCP_ACTIONS = [
    # GitHub MCP: reads AND repository/ref/issue/PR mutations. The server is
    # launched as `github-mcp-server.exe stdio` with no tool restriction.
    ("github", "github:create_pull_request"),
    ("github", "github:push_files"),
    ("github", "github:create_or_update_file"),
    ("github", "github:create_issue"),
    ("github", "github:issue_write"),
    ("github", "github:create_branch"),
    ("github", "github:update_ref"),
    ("github", "github:merge_pull_request"),
    ("github", "github:delete_file"),
    ("github", "github:list_issues"),
    # Other network-capable servers.
    ("firecrawl", "firecrawl:firecrawl_scrape"),
    ("playwright_mcp", "playwright:browser_navigate"),
    ("arxiv", "arxiv:search_papers"),
    ("s2_scholar", "s2:search_papers"),
    ("context7", "context7:resolve-library-id"),
    ("huggingface", "huggingface:query"),
    ("seq_thinking", "seq_thinking:think"),
    ("code_review", "code_review:review"),
    ("sqlite", "sqlite:query"),
    ("memory", "memory:read_graph"),
    ("google_calendar", "google_calendar:list_events"),
]


@pytest.fixture(autouse=True)
def _isolated_trust_ledger(tmp_path, monkeypatch):
    """Keep trust grants out of production `data/trust_grants.json`."""
    monkeypatch.setattr(tl, "_GRANTS_PATH", tmp_path / "trust_grants.json")
    yield


@pytest.fixture
def offline_run(monkeypatch):
    """Simulate an authorized Experiment J learning run."""
    monkeypatch.setenv(ar.OFFLINE_MCP_ENV, "1")
    yield


# --------------------------------------------------------------------------
# Default policy is UNCHANGED outside an authorized learning run
# --------------------------------------------------------------------------

def test_default_behaviour_preserved_outside_learning_run(monkeypatch):
    """With the flag unset the classification must be exactly what it was."""
    monkeypatch.delenv(ar.OFFLINE_MCP_ENV, raising=False)
    assert not ar._offline_mcp_enforced()
    # Still CONFIRM (never silently ALLOW) - unchanged pre-existing behaviour.
    assert ar.agent_tool_policy("mcp", "github:create_pull_request") == ar.CONFIRM
    assert ar.agent_tool_policy("mcp", "firecrawl:firecrawl_scrape") == ar.CONFIRM
    # Serena stays ALWAYS_CONFIRM per tool.
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") == ar.ALWAYS_CONFIRM


# --------------------------------------------------------------------------
# The security property
# --------------------------------------------------------------------------

def test_learning_run_denies_every_non_local_mcp_action(offline_run):
    """The core property: no network / mutating MCP capability in a learning run."""
    assert ar._offline_mcp_enforced()
    for server, action in NETWORK_MCP_ACTIONS:
        policy = ar.agent_tool_policy("mcp", action)
        assert policy == ar.DENY, (
            f"{server}: {action!r} classified {policy!r}, expected DENY in a "
            "learning run"
        )


def test_learning_run_denies_mcp_even_with_no_action(offline_run):
    """An `mcp` call with no parsed action must fail closed, not default open."""
    assert ar.agent_tool_policy("mcp") == ar.DENY
    assert ar.agent_tool_policy("mcp", "") == ar.DENY


def test_learning_run_keeps_local_serena_reads_reachable(offline_run):
    """Serena is launched locally and only offers read-only symbol lookups."""
    assert ar._offline_mcp_enforced()
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") == ar.ALWAYS_CONFIRM
    assert (
        ar.agent_tool_policy("mcp", "serena:find_referencing_symbols")
        == ar.ALWAYS_CONFIRM
    )


def test_scoped_serena_grant_relaxes_only_serena(offline_run):
    """The documented offline grant must still work, and only for serena."""
    tl.grant("mcp:serena:find_symbol", 600)
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") == ar.ALLOW
    # ...and it must not have opened anything network-capable.
    assert ar.agent_tool_policy("mcp", "github:create_pull_request") == ar.DENY
    assert ar.agent_tool_policy("mcp", "firecrawl:firecrawl_scrape") == ar.DENY


def test_blanket_mcp_grant_cannot_reopen_denied_actions(offline_run):
    """The exact regression: a broad `grant("mcp", ...)` must NOT defeat the gate.

    This is the defect that motivated the change - it must stay fixed even if
    some other caller issues a blanket grant.
    """
    tl.grant("mcp", 8 * 3600)
    assert ar.agent_tool_policy("mcp", "github:push_files") == ar.DENY
    assert ar.agent_tool_policy("mcp", "github:create_pull_request") == ar.DENY
    assert ar.agent_tool_policy("mcp", "playwright:browser_navigate") == ar.DENY


def test_mcp_register_and_configure_remain_confirm_gated(offline_run):
    """Register/configure are server-lifecycle actions; keep them non-ALLOW."""
    for action in ("mcp_register", "mcp_batch"):
        policy = ar.agent_tool_policy(action)
        assert policy in (ar.CONFIRM, ar.ALWAYS_CONFIRM), (action, policy)


def test_git_state_changing_actions_stay_denied_always(offline_run):
    """Independent invariant: the `git` tool can never commit or push."""
    for action in ("commit", "push", "add", "reset"):
        assert ar.agent_tool_policy("git", action) == ar.DENY, action
    assert ar.agent_tool_policy("git", "status") == ar.ALLOW


# --------------------------------------------------------------------------
# Source-level guards on the learning harness
# --------------------------------------------------------------------------

def _executable_source(path: Path) -> str:
    """Source with comment-only lines removed.

    The guard below asserts on CODE. A comment that documents the removed blanket
    grant (and quotes it) must not be able to fail - or pass - the assertion.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


def test_learning_harness_issues_no_blanket_mcp_or_web_grant():
    """`run_repair_task.py` must not blanket-grant mcp / web_fetch / filesystem."""
    src = _executable_source(REPO / "qwen_train" / "run_repair_task.py")
    assert 'grant("mcp"' not in src, (
        "run_repair_task.py re-introduced a blanket grant(\"mcp\", ...); the "
        "learning run must rely on SWARM_F1_OFFLINE_MCP + _grant_offline()"
    )
    assert 'grant("web_fetch"' not in src
    assert 'grant("filesystem"' not in src


def test_learning_harness_sets_the_offline_mcp_flag():
    """The policy gate is inert unless the learning run actually enables it."""
    src = _executable_source(REPO / "qwen_train" / "run_repair_task.py")
    assert f'os.environ["{ar.OFFLINE_MCP_ENV}"] = "1"' in src


def test_harness_relies_on_existing_narrow_grantable_set():
    """`_grant_offline()` is the least-privilege mechanism; it must stay in use."""
    import qwen_train.run_curriculum as rc

    assert "mcp" not in rc._GRANTABLE, (
        "run_curriculum._GRANTABLE must not contain the blanket `mcp` scope"
    )
    assert "mcp:serena:find_symbol" in rc._GRANTABLE
    assert "mcp:serena:find_referencing_symbols" in rc._GRANTABLE
    src = (REPO / "qwen_train" / "run_repair_task.py").read_text(encoding="utf-8")
    assert "rc._grant_offline()" in src