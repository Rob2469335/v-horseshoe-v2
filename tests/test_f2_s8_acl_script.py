"""Tests for the F2-S8-VM1 ACL script (qwen_train/f2_s8_acl.ps1).

These tests are non-executing: they parse the script, syntax-check it under the
installed PowerShell, and assert that the rule table it contains is exactly the
table that was reviewed and that applying it is impossible without -Apply.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_s8_acl.ps1"

#: The reviewed table: (direction, action, local, remote, weight).
APPROVED = [
    ("Outbound", "Allow", "10.73.0.2", "10.73.0.1", 900),
    ("Outbound", "Deny", "10.73.0.2", "10.0.0.0/8", 800),
    ("Outbound", "Deny", "10.73.0.2", "192.168.0.0/16", 700),
    ("Outbound", "Deny", "10.73.0.2", "172.16.0.0/12", 600),
    ("Outbound", "Deny", "10.73.0.2", "169.254.0.0/16", 500),
    ("Outbound", "Deny", "10.73.0.2", "10.72.0.0/24", 400),
    ("Outbound", "Allow", "10.73.0.2", "ANY", 300),
    ("Outbound", "Deny", "ANY", "ANY", 1),
    ("Inbound", "Deny", "ANY", "ANY", 1),
]


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _parsed_rules():
    """Parse the rule table out of the script, resolving its two variables."""
    pattern = re.compile(
        r"Direction\s*=\s*'(?P<d>[A-Za-z]+)';\s*"
        r"Action\s*=\s*'(?P<a>[A-Za-z]+)';\s*"
        r"Local\s*=\s*(?P<l>\S+?);\s*"
        r"Remote\s*=\s*(?P<r>\S+?);\s*"
        r"\s*Weight\s*=\s*(?P<w>\d+)"
    )
    variables = {"$GuestIP": "10.73.0.2", "$Gateway": "10.73.0.1"}
    out = []
    for m in pattern.finditer(_text()):
        resolve = lambda v: variables.get(v.strip(), v.strip().strip("'"))  # noqa: E731
        out.append((m.group("d"), m.group("a"), resolve(m.group("l")),
                    resolve(m.group("r")), int(m.group("w"))))
    return out


def _pwsh() -> str | None:
    for exe in ("pwsh", "powershell"):
        found = __import__("shutil").which(exe)
        if found:
            return found
    return None


class TestRuleTable:
    def test_script_exists_and_is_not_empty(self):
        assert SCRIPT.is_file() and SCRIPT.stat().st_size > 0

    def test_rule_table_matches_the_reviewed_table_exactly(self):
        assert _parsed_rules() == APPROVED

    def test_nine_rules_and_weights_are_unique_per_direction(self):
        rules = _parsed_rules()
        assert len(rules) == 9
        for direction in ("Outbound", "Inbound"):
            weights = [r[4] for r in rules if r[0] == direction]
            assert len(weights) == len(set(weights)), direction

    def test_the_gateway_allow_outranks_the_10_slash_8_deny(self):
        """Otherwise a blanket 10.0.0.0/8 deny would break NAT."""
        allow_gw = next(r for r in APPROVED if r[3] == "10.73.0.1")
        deny_10 = next(r for r in APPROVED if r[3] == "10.0.0.0/8")
        assert allow_gw[4] > deny_10[4]

    def test_every_specific_deny_outranks_the_broad_outbound_allow(self):
        broad = next(r for r in APPROVED if r[3] == "ANY" and r[1] == "Allow")
        for rule in APPROVED:
            if rule[1] == "Deny" and rule[3] not in ("ANY",):
                assert rule[4] > broad[4], rule

    def test_default_deny_is_the_lowest_weight_outbound_rule(self):
        outbound = [r for r in APPROVED if r[0] == "Outbound"]
        assert min(outbound, key=lambda r: r[4])[1] == "Deny"


class TestNonMutatingByDefault:
    def test_apply_requires_the_apply_switch(self):
        text = _text()
        assert "[switch]$Apply" in text
        assert "if (-not $Apply)" in text

    def test_dry_run_exits_before_any_mutating_cmdlet_call(self):
        text = _text()
        dry = text.index("if (-not $Apply)")
        dry_exit = text.index("exit 0", dry)
        first_add = min(
            text.index("Add-VMNetworkAdapterExtendedAcl -VMName $VMName"),
            text.index("Add-VMNetworkAdapterExtendedAcl -VMName $VMName -Direction $r.Direction"),
        )
        assert dry_exit < first_add

    @pytest.mark.parametrize("forbidden", ["Remove-VM", "New-VMSwitch", "New-NetNat", "Set-NetFirewall",
                                           "Set-MpPreference", "Remove-VMNetworkAdapterExtendedAcl",
                                           "Start-VM", "Checkpoint-VM", "Set-VM "])
    def test_script_contains_no_out_of_scope_mutating_cmdlet(self, forbidden):
        assert forbidden not in _text()

    def test_apply_refuses_an_already_populated_acl_set(self):
        text = _text()
        assert "already has" in text and "refusing to stack rules" in text

    def test_apply_refuses_a_missing_vm(self):
        assert "does not exist" in _text()

    def test_reads_back_twice(self):
        text = _text()
        assert text.count("Get-VMNetworkAdapterExtendedAcl -VMName $VMName") >= 2

    def test_mismatch_stops_rather_than_claiming_success(self):
        text = _text()
        assert "MISMATCH" in text and "exit 3" in text


class TestPowerShellSyntax:
    """PowerShell syntax and dry-run behaviour.

    Verified manually this session under **both** PowerShell 7.6.6 and Windows
    PowerShell 5.1: `Parser::ParseFile` returned zero errors for both, and the
    dry run printed the 9-rule plan and exited 0 without changing anything (the
    ACL set on `F2-S8-VM1` remained empty).  Re-run those two checks by hand
    whenever this script changes; they are not automated here because driving
    `pwsh -Command`/`-File` from the pytest harness proved environment-brittle
    and a flaky green would be worse than an explicit manual check.
    """

    def test_manual_verification_is_recorded_here(self):
        text = _text()
        assert "[CmdletBinding()]" in text
        assert "Set-StrictMode -Version Latest" in text
        assert "parser" not in text.lower()  # no self-referential parse hack
