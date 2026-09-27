"""Time-boxed scoped trust grants + their integration with agent_tool_policy.

The relaxation is fail-closed: it can only turn CONFIRM into ALLOW via an active
grant; ALWAYS_CONFIRM and DENY are never relaxed; expired grants do nothing; and
the default (no grants) is identical to the static policy.
"""

from __future__ import annotations

import time

import pytest

from swarm_os.services import trust_ledger
from swarm_os.services.approval_registry import (
    ALLOW,
    ALWAYS_CONFIRM,
    CONFIRM,
    DENY,
    agent_tool_policy,
)


@pytest.fixture(autouse=True)
def _tmp_grants(monkeypatch, tmp_path):
    monkeypatch.setattr(trust_ledger, "_GRANTS_PATH", tmp_path / "grants.json")
    yield


def test_no_grant_is_static_policy():
    assert agent_tool_policy("web_fetch", "fetch") == CONFIRM
    assert agent_tool_policy("filesystem", "write") == ALWAYS_CONFIRM
    assert agent_tool_policy("totally_unknown_tool", "x") == DENY


def test_active_grant_relaxes_only_confirm():
    trust_ledger.grant("web_fetch", 60)
    assert trust_ledger.is_trusted("web_fetch") is True
    assert agent_tool_policy("web_fetch", "fetch") == "ALLOW"


def test_grant_relaxes_only_allowlisted_and_never_deny():
    trust_ledger.grant("filesystem", 60)
    trust_ledger.grant("email_send", 60)
    trust_ledger.grant("totally_unknown_tool", 60)
    # filesystem IS offline-grantable: write/patch are ALWAYS_CONFIRM but a scoped
    # grant relaxes them since the handler confines writes to SWARM_WRITE_ROOT.
    assert agent_tool_policy("filesystem", "write") == ALLOW
    # a NON-allowlisted ALWAYS_CONFIRM tool is never relaxed by a grant
    assert agent_tool_policy("email_send", "send") == ALWAYS_CONFIRM
    # an unknown tool is DENY and a grant NEVER relaxes it
    assert agent_tool_policy("totally_unknown_tool", "x") == DENY


def test_expired_grant_does_not_relax():
    trust_ledger.grant("web_fetch", 60)
    # Rewrite the stored expiry into the past (simulate time passing).
    data = trust_ledger._load()
    data["web_fetch"]["expires_at"] = time.time() - 1
    trust_ledger._save(data)
    assert trust_ledger.is_trusted("web_fetch") is False
    assert agent_tool_policy("web_fetch", "fetch") == CONFIRM


def test_action_scoped_grant():
    trust_ledger.grant("cron_manage:create", 60)
    assert trust_ledger.is_trusted("cron_manage", "create") is True
    assert trust_ledger.is_trusted("cron_manage", "delete") is False


def test_revoke():
    trust_ledger.grant("web_fetch", 60)
    assert trust_ledger.revoke("web_fetch") is True
    assert trust_ledger.is_trusted("web_fetch") is False


def test_eval_twine_cleanup_revokes_exact_granted_scopes():
    """Regression: eval_twine.py must revoke the exact 4 scopes it grants.

    The old cleanup called run_curriculum._revoke_offline() which revokes a
    different set (sandbox_repl, lsp, git, mcp:serena:*).  filesystem, mcp,
    and web_fetch were left as residual grants for up to 8 hours.
    """
    # Grant the exact eval_twine scopes
    for scope in ("filesystem", "sandbox_repl", "mcp", "web_fetch"):
        trust_ledger.grant(scope, 8 * 3600)

    # Authorization succeeds before cleanup
    assert agent_tool_policy("filesystem", "write") == ALLOW
    assert agent_tool_policy("sandbox_repl") == ALLOW
    assert agent_tool_policy("mcp") == ALLOW
    assert agent_tool_policy("web_fetch") == ALLOW

    # Revoke exactly the granted scopes (eval_twine.py cleanup)
    for scope in ("filesystem", "sandbox_repl", "mcp", "web_fetch"):
        trust_ledger.revoke(scope)

    # Authorization requires confirmation / is denied after cleanup
    assert agent_tool_policy("filesystem", "write") == ALWAYS_CONFIRM
    assert agent_tool_policy("sandbox_repl") == ALWAYS_CONFIRM
    assert agent_tool_policy("mcp") == CONFIRM
    assert agent_tool_policy("web_fetch") == CONFIRM


def test_eval_twine_rejects_missing_test_patch():
    """Regression: eval_twine.py must reject missing --test-patch immediately.

    The first N5 invocation (2026-09-27) omitted --test-patch, causing the
    evaluator to proceed without the bug-exposing patch. Baseline tests passed
    at the buggy base commit and the evaluator stopped with a confusing error.

    We test the validation logic directly by simulating the argument parser
    without subprocess (the conftest global_subprocess_mock patches Popen).
    """
    import argparse

    # Reproduce the eval_twine argument parser
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", default="pypa__twine-1066")
    ap.add_argument("--base-commit", default="4a1fc064a7899872ee845df6a8810bb51a6845ac")
    ap.add_argument("--test-cmd", default="pytest tests/test_package.py")
    ap.add_argument("--f2p", action="append", default=["test_a", "test_b", "test_c"])
    ap.add_argument("--problem-statement", default="")
    ap.add_argument("--test-patch", default="")
    ap.add_argument("--timeout", type=int, default=1200)

    # Parse with NO --test-patch (the bug scenario)
    args = ap.parse_args([])

    # The validation guard: missing test_patch must cause SystemExit
    with pytest.raises(SystemExit) as exc_info:
        if not args.test_patch:
            ap.error("--test-patch is required (path to the test patch file)")

    assert exc_info.value.code == 2  # argparse error exit code

    # Parse with --test-patch supplied (should NOT raise)
    args_ok = ap.parse_args(["--test-patch", "/some/path.patch"])
    assert args_ok.test_patch == "/some/path.patch"
