"""F1 MCP denylist contract for the Experiment-J learning run.

Security property under test
---------------------------
The documented F1 web-tool control ``SWARM_F1_NO_WEB_TOOLS=1`` (F1-OP-INFRA-001)
must fail closed on MCP capability, not merely strip ``web_search``/``web_fetch``
from the coder's tool surface.

Why this is a separate property from ``tests/test_f1_offline_mcp_policy.py``
--------------------------------------------------------------------------
That suite covers ``SWARM_F1_OFFLINE_MCP=1``, which denies non-local ``mcp``
*actions*. It deliberately leaves ``mcp_register``/``mcp_batch`` CONFIRM-gated,
because that flag's documented scope is per-action MCP classification. Two real
gaps followed from ``SWARM_F1_NO_WEB_TOOLS`` not being consulted at all:

1. ``mcp_register`` spawns a persistent server subprocess, so it can attach a
   network MCP server *mid-run*. It classified as CONFIRM, and
   ``agent_tool_policy`` relaxes CONFIRM for ANY active scoped trust grant.
2. ``mcp`` configure actions were ALWAYS_CONFIRM, relaxable via
   ``_OFFLINE_GRANTABLE``.
3. Every non-Serena MCP server reaches the network through a route that
   stripping two web tools does not close.

Both gates are set by ``qwen_train/run_repair_task.py`` for a learning run, and
the learning run uses neither ``mcp_register`` nor ``mcp_batch`` — only the two
scoped Serena symbol ops — so denying them costs no capability.

DENY is the right classification here because it is structurally final:
``agent_tool_policy`` never relaxes it, and ``runtime_v2/services/tool_executor.py``
short-circuits DENY *before* raising any approval prompt, so neither a broad
``grant("mcp", ...)`` nor an auto-answering harness can reopen it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm_os.services import approval_registry as ar
from swarm_os.services import trust_ledger as tl

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_trust_ledger(tmp_path, monkeypatch):
    """Keep trust grants out of production `data/trust_grants.json`."""
    monkeypatch.setattr(tl, "_GRANTS_PATH", tmp_path / "trust_grants.json")
    yield


@pytest.fixture(autouse=True)
def _both_flags_off(monkeypatch):
    """Start from a known OFF state so ambient env cannot mask a regression."""
    for var in (ar.NO_WEB_TOOLS_ENV, ar.OFFLINE_MCP_ENV):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def no_web_tools(monkeypatch):
    """The documented F1 web-tool control is active."""
    monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, "1")
    yield


# The PHASE 2.3 baseline matrix. `server:tool` shapes use the names the call
# sites actually produce; `None` action means "no action parsed".
DENY_UNDER_FLAG = [
    ("github", "mcp", "github:create_issue"),
    ("firecrawl", "mcp", "firecrawl:scrape"),
    ("arxiv", "mcp", "arxiv:search"),
    ("s2_scholar", "mcp", "s2_scholar:search"),
    ("context7", "mcp", "context7:query"),
    ("playwright_mcp", "mcp", "playwright_mcp:navigate"),
    ("huggingface", "mcp", "huggingface:search"),
    ("sqlite", "mcp", "sqlite:query"),
    ("mcp_register", "mcp_register", None),
    ("mcp_batch", "mcp_batch", None),
]

SERENA_ALLOWED = [
    ("serena:find_symbol", "mcp"),
    ("serena:find_referencing_symbols", "mcp"),
]

# A non-MCP control: the flag must not touch it.
NON_MCP_CONTROL = ("git", "status")


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


def test_flag_on_is_detected(no_web_tools):
    assert ar._no_web_tools_enforced()


def test_flag_off_is_not_detected():
    assert not ar._no_web_tools_enforced()


def test_flag_values_other_than_exactly_one_do_not_arm_the_gate(monkeypatch):
    for bad in ("", "0", "true", "yes", "11", "01", "1x"):
        monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, bad)
        assert not ar._no_web_tools_enforced(), (
            f"{bad!r} must not arm the MCP denylist; the contract is == '1'"
        )


# ---------------------------------------------------------------------------
# Flag ON: the denylist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("server", "tool", "action"), DENY_UNDER_FLAG)
def test_flag_on_denies_non_serena_mcp_and_lifecycle_tools(
    no_web_tools, server, tool, action
):
    policy = ar.agent_tool_policy(tool, action)
    assert policy == ar.DENY, (
        f"{server}: {tool}/{action!r} classified {policy!r}, expected DENY under "
        f"{ar.NO_WEB_TOOLS_ENV}=1"
    )


@pytest.mark.parametrize(("action", "tool"), SERENA_ALLOWED)
def test_flag_on_leaves_serena_operations_unchanged(no_web_tools, action, tool):
    """Serena must remain exactly as it was: ALWAYS_CONFIRM per tool."""
    assert ar.agent_tool_policy(tool, action) == ar.ALWAYS_CONFIRM


def test_flag_on_denies_mcp_with_no_parsed_action(no_web_tools):
    """An `mcp` call with no parsed server must fail closed, not default open."""
    assert ar.agent_tool_policy("mcp") == ar.DENY
    assert ar.agent_tool_policy("mcp", "") == ar.DENY


def test_flag_on_denies_mcp_configure_actions(no_web_tools):
    """Server-lifecycle configure must not be grant-relaxable."""
    for action in ("register", "configure"):
        assert ar.agent_tool_policy("mcp", action) == ar.DENY, action


def test_flag_on_does_not_touch_a_non_mcp_tool(no_web_tools, monkeypatch):
    tool, action = NON_MCP_CONTROL
    with_flag = ar.agent_tool_policy(tool, action)
    monkeypatch.delenv(ar.NO_WEB_TOOLS_ENV, raising=False)
    without_flag = ar.agent_tool_policy(tool, action)
    assert with_flag == without_flag, (
        f"non-MCP control {tool}/{action} changed: "
        f"{without_flag!r} -> {with_flag!r}"
    )


# ---------------------------------------------------------------------------
# Flag ON: DENY must be structurally final
# ---------------------------------------------------------------------------


def test_no_grant_reopens_the_denied_mcp_surface(no_web_tools):
    """Blanket AND scoped grants must both fail to defeat the gate."""
    tl.grant("mcp", 8 * 3600)
    tl.grant("mcp_register", 8 * 3600)
    tl.grant("mcp_batch", 8 * 3600)
    tl.grant("mcp:github:create_issue", 8 * 3600)
    for _server, tool, action in DENY_UNDER_FLAG:
        policy = ar.agent_tool_policy(tool, action)
        assert policy == ar.DENY, f"{tool}/{action!r} reopened to {policy!r}"


def test_serena_scoped_grant_still_relaxes_only_serena(no_web_tools):
    tl.grant("mcp:serena:find_symbol", 600)
    assert ar.agent_tool_policy("mcp", "serena:find_symbol") == ar.ALLOW
    assert ar.agent_tool_policy("mcp", "github:create_issue") == ar.DENY
    assert ar.agent_tool_policy("mcp_register") == ar.DENY


def test_tool_executor_short_circuits_deny_before_prompting():
    """DENY is only final if the executor honours it without asking.

    The anchor is the pending-approval creation, not `_dispatch`: the ALLOW
    branch legitimately dispatches immediately and therefore appears BEFORE the
    DENY check. What must never happen is a pending action being created (i.e. a
    human prompt raised) for a call that policy already denied.
    """
    src = (REPO / "runtime_v2" / "services" / "tool_executor.py").read_text(
        encoding="utf-8"
    )
    policy_at = src.index("policy = agent_tool_policy(")
    deny_at = src.index("if policy == DENY:", policy_at)
    prompt_at = src.index("get_registry().create(", policy_at)
    assert policy_at < deny_at < prompt_at, (
        "tool_executor must classify -> short-circuit DENY -> and only then "
        "create a pending approval; otherwise a prompt could precede the DENY "
        "check and an auto-answering harness could answer it"
    )


# ---------------------------------------------------------------------------
# Flag OFF: every pre-existing classification must be byte-identical
# ---------------------------------------------------------------------------


def test_flag_off_preserves_pre_existing_classification():
    """With the flag off, nothing about MCP classification may change."""
    # Non-Serena MCP actions: CONFIRM, never silently ALLOW.
    for _server, tool, action in DENY_UNDER_FLAG:
        if tool == "mcp":
            assert ar.agent_tool_policy(tool, action) == ar.CONFIRM, (
                f"{action!r} must stay CONFIRM with the flag off"
            )
    # Serena: ALWAYS_CONFIRM per tool.
    for action, tool in SERENA_ALLOWED:
        assert ar.agent_tool_policy(tool, action) == ar.ALWAYS_CONFIRM
    # Server-lifecycle: still CONFIRM-gated, exactly as before this change.
    assert ar.agent_tool_policy("mcp_register") == ar.CONFIRM
    assert ar.agent_tool_policy("mcp_batch") == ar.CONFIRM
    assert ar.agent_tool_policy("mcp", "register") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("mcp", "configure") == ar.ALWAYS_CONFIRM


def test_flag_off_still_relaxes_confirm_via_a_grant():
    """Pre-existing relaxation behaviour must survive the new gate."""
    tl.grant("mcp:github:list_issues", 600)
    assert ar.agent_tool_policy("mcp", "github:list_issues") == ar.ALLOW


def test_offline_mcp_flag_semantics_are_unchanged():
    """This change must not have widened SWARM_F1_OFFLINE_MCP's scope."""
    import os

    os.environ[ar.OFFLINE_MCP_ENV] = "1"
    try:
        assert ar._offline_mcp_enforced()
        # Its documented scope: non-local mcp ACTIONS denied...
        assert ar.agent_tool_policy("mcp", "github:create_issue") == ar.DENY
        # ...while server-lifecycle stays CONFIRM-gated (unchanged, and covered
        # by tests/test_f1_offline_mcp_policy.py).
        assert ar.agent_tool_policy("mcp_register") == ar.CONFIRM
    finally:
        del os.environ[ar.OFFLINE_MCP_ENV]


def test_new_gate_is_independent_of_the_offline_mcp_flag():
    """Either flag alone must arm the non-Serena denial."""
    import os

    for armed in (ar.NO_WEB_TOOLS_ENV, ar.OFFLINE_MCP_ENV):
        os.environ[armed] = "1"
        try:
            assert ar.agent_tool_policy("mcp", "firecrawl:scrape") == ar.DENY
            assert ar.agent_tool_policy(
                "mcp", "serena:find_symbol"
            ) == ar.ALWAYS_CONFIRM
        finally:
            del os.environ[armed]


# ---------------------------------------------------------------------------
# Source-level guards: the gate is inert unless something arms it
# ---------------------------------------------------------------------------


def test_learning_harness_arms_both_gates():
    """`run_repair_task.py` must set both flags before the backend imports."""
    src = (REPO / "qwen_train" / "run_repair_task.py").read_text(encoding="utf-8")
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )
    assert f'os.environ["{ar.NO_WEB_TOOLS_ENV}"] = "1"' in code
    assert f'os.environ["{ar.OFFLINE_MCP_ENV}"] = "1"' in code


def test_learning_run_needs_neither_denied_tool():
    """The denials must cost the authorized learning run no capability.

    `_GRANTABLE` is the harness's least-privilege offline surface; it contains
    the two scoped Serena ops and no server-lifecycle or batch transport.
    """
    import qwen_train.run_curriculum as rc

    assert "mcp:serena:find_symbol" in rc._GRANTABLE
    assert "mcp:serena:find_referencing_symbols" in rc._GRANTABLE
    assert "mcp" not in rc._GRANTABLE
    assert "mcp_register" not in rc._GRANTABLE
    assert "mcp_batch" not in rc._GRANTABLE


# ---------------------------------------------------------------------------
# D1: the two NATIVE web tools are structurally DENY under the same flag.
#
# Previously the flag removed web_search/web_fetch only from the agent's tool
# SURFACE, via `_strip_web_tools_for_local_analysis`, which deliberately skips
# stripping when the goal matches `_INTERNET_GOAL_RE` ("latest", "how to",
# "best practices", "current state of", ...). web_search classified ALLOW and
# web_fetch CONFIRM, so on an "internet-flavoured" task prompt under
# SWARM_F1_NO_WEB_TOOLS=1 the surface kept them and the policy permitted them.
# The denial is now at the policy layer and takes no prompt input at all.
# ---------------------------------------------------------------------------


class TestNativeWebToolsDeniedUnderNoWebPolicy:
    def test_web_search_is_deny_under_flag(self, monkeypatch):
        monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, "1")
        assert ar.agent_tool_policy("web_search") == ar.DENY

    def test_web_fetch_is_deny_under_flag(self, monkeypatch):
        monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, "1")
        assert ar.agent_tool_policy("web_fetch") == ar.DENY

    @pytest.mark.parametrize(
        "goal",
        [
            "fix the latest twine release bug",
            "how to fix MockResponse.ok",
            "apply best practices to this module",
            "current state of the upstream repository",
            "search the internet for the answer",
        ],
    )
    def test_denial_is_independent_of_task_prompt(self, monkeypatch, goal):
        """No prompt wording can re-open web_search while the flag is armed."""
        monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, "1")
        assert ar.agent_tool_policy("web_search") == ar.DENY
        assert ar.agent_tool_policy("web_fetch") == ar.DENY
        # Even when the surface filter declines to strip them, the policy denies.
        from runtime_v2.api._agent_helpers import _strip_web_tools_for_local_analysis

        kept = _strip_web_tools_for_local_analysis(
            "coder", ["web_search", "web_fetch", "read"], goal
        )
        if "web_search" in kept:
            assert ar.agent_tool_policy("web_search") == ar.DENY

    def test_deny_survives_a_scoped_trust_grant(self, monkeypatch):
        """DENY is structurally final: no grant can relax it."""
        monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, "1")
        tl.grant("web_search", 600)
        tl.grant("web_fetch", 600)
        assert ar.agent_tool_policy("web_search") == ar.DENY
        assert ar.agent_tool_policy("web_fetch") == ar.DENY

    def test_mcp_network_capable_tools_remain_deny(self, monkeypatch):
        """The pre-existing MCP denial must not regress."""
        monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, "1")
        assert ar.agent_tool_policy("mcp", "github:list_issues") == ar.DENY
        assert ar.agent_tool_policy("mcp", "firecrawl:scrape") == ar.DENY
        assert ar.agent_tool_policy("mcp_register") == ar.DENY
        assert ar.agent_tool_policy("mcp_batch") == ar.DENY

    def test_local_readonly_tools_still_allowed_under_flag(self, monkeypatch):
        """The denial must not disturb the offline least-privilege surface."""
        monkeypatch.setenv(ar.NO_WEB_TOOLS_ENV, "1")
        assert ar.agent_tool_policy("filesystem", "read") == ar.ALLOW
        assert ar.agent_tool_policy("git", "diff") == ar.ALLOW
        assert ar.agent_tool_policy("semantic_search") == ar.ALLOW

    def test_default_behaviour_outside_the_governed_run_is_unchanged(self, monkeypatch):
        """With the flag absent, web tools keep their pre-existing policy."""
        monkeypatch.delenv(ar.NO_WEB_TOOLS_ENV, raising=False)
        assert ar.agent_tool_policy("web_search") == ar.ALLOW
        assert ar.agent_tool_policy("web_fetch") == ar.CONFIRM

    def test_f2_p2_arms_the_flag(self):
        """The F2 production spawn must arm the policy for its P2 child."""
        src = (REPO / "qwen_train" / "f2_execution_adapter.py").read_text(encoding="utf-8")
        code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        assert f'env["{ar.NO_WEB_TOOLS_ENV}"] = "1"' in code
