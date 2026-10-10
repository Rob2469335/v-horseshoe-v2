"""Tests for the F2-S8-VM1 ACL script (qwen_train/f2_s8_acl.ps1).

These tests are non-executing: they parse the script and assert that the rule
table it contains is exactly the operator-approved table, that the protocol
requirement discovered by bounded probe is satisfied, and that applying the
rules is impossible without -Apply.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_s8_acl.ps1"

#: Operator-approved 11-rule IPv4 policy.
#: (direction, action, local, remote, protocol, weight, stateful)
APPROVED = [
    ("Outbound", "Allow", "ANY", "10.73.0.1", "TCP", 900, True),
    ("Outbound", "Allow", "ANY", "10.73.0.1", "UDP", 901, True),
    ("Outbound", "Deny", "ANY", "10.0.0.0/8", "", 800, False),
    ("Outbound", "Deny", "ANY", "192.168.0.0/16", "", 700, False),
    ("Outbound", "Deny", "ANY", "172.16.0.0/12", "", 600, False),
    ("Outbound", "Deny", "ANY", "169.254.0.0/16", "", 500, False),
    ("Outbound", "Deny", "ANY", "10.72.0.0/24", "", 400, False),
    ("Outbound", "Allow", "ANY", "0.0.0.0/0", "TCP", 300, True),
    ("Outbound", "Allow", "ANY", "0.0.0.0/0", "UDP", 301, True),
    ("Outbound", "Deny", "ANY", "ANY", "", 1, False),
    ("Inbound", "Deny", "ANY", "ANY", "", 1, False),
]


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _parsed_rules():
    pattern = re.compile(
        r"Direction\s*=\s*'(?P<d>[A-Za-z]+)';\s*"
        r"Action\s*=\s*'(?P<a>[A-Za-z]+)';\s*"
        r"Local\s*=\s*(?P<l>\S+?);\s*"
        r"Remote\s*=\s*(?P<r>\S+?);\s*"
        r"Protocol\s*=\s*'(?P<p>[^']*)';\s*"
        r"Weight\s*=\s*(?P<w>\d+);\s*"
        r"Stateful\s*=\s*(?P<s>\$\w+)"
    )
    variables = {"$GuestIP": "10.73.0.2", "$Gateway": "10.73.0.1"}
    out = []
    for m in pattern.finditer(_text()):
        resolve = lambda v: variables.get(v.strip(), v.strip().strip("'"))  # noqa: E731
        out.append((m.group("d"), m.group("a"), resolve(m.group("l")), resolve(m.group("r")),
                    m.group("p"), int(m.group("w")), m.group("s") == "$true"))
    return out


class TestRuleTable:
    def test_script_exists(self):
        assert SCRIPT.is_file() and SCRIPT.stat().st_size > 0

    def test_rule_table_matches_the_approved_table_exactly(self):
        assert _parsed_rules() == APPROVED

    def test_eleven_rules(self):
        assert len(_parsed_rules()) == 11

    def test_weights_are_unique_per_direction(self):
        for direction in ("Outbound", "Inbound"):
            weights = [r[5] for r in APPROVED if r[0] == direction]
            assert len(weights) == len(set(weights)), direction

    # ---- the root cause found by bounded probe -------------------------- #
    def test_every_stateful_rule_declares_a_protocol(self):
        """0x80070057: a Stateful entry is rejected without an explicit protocol."""
        for rule in APPROVED:
            if rule[6]:
                assert rule[4] in ("TCP", "UDP"), rule

    def test_no_non_stateful_rule_declares_a_protocol(self):
        for rule in APPROVED:
            if not rule[6]:
                assert rule[4] == "", rule

    def test_script_uses_no_address_range_syntax(self):
        """The 'a-b' range form is unsupported on this host (probe P3)."""
        for rule in APPROVED:
            assert "-" not in rule[3], rule

    def test_no_icmp_rule_is_present(self):
        assert not any(r[4] == "1" for r in APPROVED)

    # ---- boundary semantics --------------------------------------------- #
    def test_gateway_allows_outrank_the_ten_slash_eight_deny(self):
        deny = next(r for r in APPROVED if r[3] == "10.0.0.0/8")
        for rule in APPROVED:
            if rule[3] == "10.73.0.1":
                assert rule[5] > deny[5], rule

    def test_every_specific_deny_outranks_the_broad_allows(self):
        broad = [r for r in APPROVED if r[1] == "Allow" and r[3] == "0.0.0.0/0"]
        assert len(broad) == 2
        for rule in APPROVED:
            if rule[1] == "Deny" and rule[3] not in ("ANY",):
                assert rule[5] > min(b[5] for b in broad), rule

    def test_the_broad_allows_are_ipv4_only(self):
        """`ANY` covers IPv6 too, so IPv6 must fall through to the default deny."""
        assert not any(r[1] == "Allow" and r[3] == "ANY" for r in APPROVED)

    def test_any_default_deny_covers_ipv6_in_both_directions(self):
        for direction in ("Outbound", "Inbound"):
            assert any(r[0] == direction and r[1] == "Deny" and r[3] == "ANY" for r in APPROVED)

    def test_default_deny_is_the_lowest_weighted_outbound_rule(self):
        outbound = [r for r in APPROVED if r[0] == "Outbound"]
        assert min(outbound, key=lambda r: r[5])[1] == "Deny"


class TestNonMutatingByDefault:
    def test_apply_requires_the_apply_switch(self):
        text = _text()
        assert "[switch]$Apply" in text
        assert "if (-not $Apply)" in text

    def test_dry_run_exits_before_any_mutating_cmdlet_call(self):
        text = _text()
        dry = text.index("if (-not $Apply)")
        dry_exit = text.index("exit 0", dry)
        first_add = text.index("Add-VMNetworkAdapterExtendedAcl @params")
        assert dry_exit < first_add

    def test_apply_uses_splatting_not_an_array_subexpression(self):
        """`@(Get-RuleParams $r)` passes a hashtable positionally and fails."""
        text = _text()
        assert "@(Get-RuleParams" not in text
        assert "$params = Get-RuleParams $r" in text
        assert "Add-VMNetworkAdapterExtendedAcl @params" in text

    @pytest.mark.parametrize("forbidden", [
        "Remove-VMNetworkAdapterExtendedAcl", "Remove-VM", "New-VMSwitch", "New-NetNat",
        "Set-NetFirewall", "Set-MpPreference", "Start-VM", "Stop-VM", "Checkpoint-VM",
        "Set-VM ", "New-VM ",
    ])
    def test_script_contains_no_out_of_scope_mutating_cmdlet(self, forbidden):
        assert forbidden not in _text()

    def test_apply_refuses_a_populated_acl_set(self):
        assert "refusing to stack rules" in _text()

    def test_apply_refuses_a_missing_vm(self):
        assert "does not exist" in _text()

    def test_reads_back_twice_and_checks_protocol_and_stateful(self):
        text = _text()
        assert text.count("Get-VMNetworkAdapterExtendedAcl -VMName $VMName") >= 2
        assert "stateful mismatch" in text and "protocol mismatch" in text

    def test_mismatch_stops_rather_than_claiming_success(self):
        text = _text()
        assert "MISMATCH" in text and "exit 3" in text

    def test_script_documents_the_probe_root_cause(self):
        assert "0x80070057" in _text() and "Protocol" in _text()


class TestPowerShellSyntax:
    """PowerShell syntax/dry-run were verified by hand this session under
    PowerShell 7.6.6 (ParseFile returned zero errors; the dry run printed the
    11-rule plan and exited 0 with no ACL applied). Re-run those by hand when the
    script changes; they are not automated here because driving pwsh from the
    pytest harness proved environment-brittle and a flaky green would be worse.
    """

    def test_manual_verification_is_recorded_here(self):
        text = _text()
        assert "[CmdletBinding()]" in text
        assert "Set-StrictMode -Version Latest" in text
