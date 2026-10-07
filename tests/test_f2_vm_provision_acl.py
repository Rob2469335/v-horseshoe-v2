"""Source-level (static) verification of the F2 VM provisioner's ACL and NIC
construction.

This is **CODE PROOF**, not live VM/network proof: no Hyper-V VM exists during
this test, none is created, and no host network policy is touched. It catches the
exact provisioner defects the mission requires:

1. missing ``-Stateful $true`` on the allow rule;
2. an invalid timeout representation (TimeSpan vs Int32 seconds);
3. a timeout without explicit statefulness;
4. missing explicit IPv4+IPv6 catch-all coverage (must use ``ANY``);
5. missing inbound deny;
6. missing outbound deny;
7. accidental extra allow rules;
8. incorrect allow/deny weight relationship;
plus the double-NIC defect (``New-VM -SwitchName`` already attaches one adapter).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_vm_provision.ps1"


@pytest.fixture(scope="module")
def text() -> str:
    return SRC.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    # Join PowerShell backtick line-continuations into single logical lines.
    return re.sub(r"`\s*\r?\n\s*", " ", text)


def _acl_statements(text: str) -> list[str]:
    # Each ACL command is one normalized statement.
    return _normalized(text).split("Add-VMNetworkAdapterExtendedAcl")[1:]


def _allow(text: str) -> list[str]:
    return [s for s in _acl_statements(text) if "-Action Allow" in s]


def _deny(text: str) -> list[str]:
    return [s for s in _acl_statements(text) if "-Action Deny" in s]


def test_exactly_one_allow_rule(text):
    assert len(_allow(text)) == 1, "expected exactly one Allow ACL (no extra allows)"


def test_allow_is_explicitly_stateful(text):
    allow = _allow(text)[0]
    assert "-Stateful $true" in allow, "allow rule must pass -Stateful $true"
    assert "-Stateful $false" not in allow


def test_allow_timeout_is_int_seconds_not_timespan(text):
    allow = _allow(text)[0]
    assert "-IdleSessionTimeout 300" in allow, "timeout must be Int32 seconds (=300)"
    assert "New-TimeSpan" not in _normalized(text), "TimeSpan is not a valid timeout type"
    assert "IdleSessionTimeout (" not in _normalized(text)


def test_allow_is_tcp_only_to_gateway_port(text):
    allow = _allow(text)[0]
    assert "-Protocol TCP" in allow
    assert "-RemotePort $GatewayPort" in allow


def test_catch_all_deny_covers_ipv4_and_ipv6_both_directions(text):
    deny = _deny(text)
    out = [s for s in deny if "-Direction Outbound" in s and "-LocalIPAddress ANY" in s and "-RemoteIPAddress ANY" in s]
    inn = [s for s in deny if "-Direction Inbound" in s and "-LocalIPAddress ANY" in s and "-RemoteIPAddress ANY" in s]
    assert out, "missing outbound catch-all deny with explicit ANY (IPv4+IPv6)"
    assert inn, "missing inbound catch-all deny with explicit ANY (IPv4+IPv6)"


def test_allow_weight_outranks_deny_weight(text):
    allow = _allow(text)[0]
    deny = _deny(text)[0]
    a = int(re.search(r"-Weight (\d+)", allow).group(1))
    d = int(re.search(r"-Weight (\d+)", deny).group(1))
    assert a > d, f"allow weight {a} must outrank deny weight {d}"


def test_no_blind_second_nic(text):
    # New-VM -SwitchName already attaches one adapter; a bare Add-VMNetworkAdapter
    # call would create a second NIC.
    assert not re.search(r"Add-VMNetworkAdapter\s+-VMName", text), (
        "provisioner must not blindly add a second NIC"
    )
    assert "Count -ne 1" in text, "provisioner must fail closed on NIC count != 1"


def test_readback_asserts_stateful_and_ordering(text):
    assert "F2 ACL verification FAILED" in text
    assert "$allow[0].Stateful" in text
    assert "Weight -le" in text
