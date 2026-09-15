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
