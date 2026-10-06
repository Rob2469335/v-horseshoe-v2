"""Enforcement-identity binding for the F2 isolation attestation.

Added 2026-10-05 after fresh SOTA research established two Microsoft platform
facts that the previous attestation could not express:

* Windows Firewall does **not** filter loopback traffic, so a reachable loopback
  is neither evidence of enforcement nor a fault.
* A firewall ``-Program`` rule matches ONE executable image and does **not**
  inherit to child processes, so every image an arm spawns must be scoped by the
  control individually.

Consequently a probe result ("this connection was denied") says nothing about
*which control* denied it. These tests pin the fail-closed behaviour that closes
that gap. No socket is opened and no firewall rule is touched.
"""
from __future__ import annotations

import pytest

from qwen_train import f2_isolation as iso
from qwen_train.f2_isolation import (
    ENFORCEMENT_SCOPE_MODELS,
    PLATFORM_LIMITS,
    IsolationAttestation,
    ProbeResult,
    SCHEMA,
    assess_enforcement_identity,
    attestation_digest,
    parse_attestation,
)

DENIED_PROBE = ProbeResult(
    dimension="http",
    target="example.com",
    protocol="tcp",
    outcome="denied",
    detail="probe recorded a denial",
)


def _att(**over) -> IsolationAttestation:
    base = dict(
        schema=SCHEMA,
        arm_id="arm-1",
        rollout_id="rollout-1",
        workspace="C:/arms/1",
        observer_pid=4242,
        policy_identity="local-test-policy",
        policy_sha256="ab" * 32,
        interface_inventory=(),
        dns_behavior="undeclared",
        negative_control="loopback-reachable",
        probes=(DENIED_PROBE,),
    )
    base.update(over)
    return IsolationAttestation(**base)


IDENTITY = dict(
    executing_user="DOMAIN\\f2arm",
    interpreter_path="C:/Python314/python.exe",
    interpreter_sha256="cd" * 32,
    spawn_image_inventory=("C:/Python314/python.exe",),
    enforcement_scope="windows_account",
)


class TestFailsClosedWithoutAnEnforcementIdentity:
    def test_absent_identity_is_not_established(self):
        verdict = assess_enforcement_identity(_att())
        assert verdict.bound is False
        assert "enforcement_scope" in verdict.missing
        assert "interpreter_path" in verdict.missing
        assert "interpreter_sha256" in verdict.missing

    def test_detail_never_claims_enforcement_when_unbound(self):
        detail = assess_enforcement_identity(_att()).detail
        assert "NOT ESTABLISHED" in detail
        assert "which control" in detail

    @pytest.mark.parametrize(
        "field", ["enforcement_scope", "interpreter_path", "interpreter_sha256"]
    )
    def test_each_required_field_is_individually_required(self, field):
        verdict = assess_enforcement_identity(_att(**{**IDENTITY, field: ""}))
        assert verdict.bound is False
        assert field in verdict.missing

    def test_whitespace_is_not_a_binding(self):
        verdict = assess_enforcement_identity(
            _att(**{**IDENTITY, "enforcement_scope": "   "})
        )
        assert verdict.bound is False


class TestRecognisedScopeModels:
    @pytest.mark.parametrize("model", ENFORCEMENT_SCOPE_MODELS)
    def test_every_declared_model_is_accepted(self, model):
        verdict = assess_enforcement_identity(_att(**{**IDENTITY, "enforcement_scope": model}))
        assert verdict.bound is True
        assert verdict.recognised_scope is True

    def test_unrecognised_model_is_recorded_but_not_accepted(self):
        verdict = assess_enforcement_identity(
            _att(**{**IDENTITY, "enforcement_scope": "vibes-based"})
        )
        assert verdict.bound is False
        assert verdict.recognised_scope is False
        assert "enforcement_scope(recognised)" in verdict.missing


class TestDigestAndRoundTrip:
    def test_identity_changes_the_digest(self):
        """The control is part of the evidence, so it is part of the digest."""
        without = attestation_digest(_att())
        with_identity = attestation_digest(_att(**IDENTITY))
        assert without != with_identity

    def test_round_trip_preserves_every_identity_field(self):
        restored = parse_attestation(_att(**IDENTITY).to_dict())
        assert restored.executing_user == IDENTITY["executing_user"]
        assert restored.interpreter_path == IDENTITY["interpreter_path"]
        assert restored.interpreter_sha256 == IDENTITY["interpreter_sha256"]
        assert restored.spawn_image_inventory == IDENTITY["spawn_image_inventory"]
        assert restored.enforcement_scope == IDENTITY["enforcement_scope"]

    def test_legacy_attestation_without_the_fields_still_parses(self):
        """Backwards compatibility: absence is NOT ESTABLISHED, not a crash."""
        legacy = _att().to_dict()
        assert "enforcement_scope" in legacy
        restored = parse_attestation(legacy)
        assert restored.enforcement_scope == ""
        assert assess_enforcement_identity(restored).bound is False


class TestPlatformLimitsAreRecorded:
    def test_child_process_non_inheritance_is_stated(self):
        assert any("child_processes" in limit for limit in PLATFORM_LIMITS)

    def test_loopback_non_filterability_is_stated(self):
        assert any("loopback" in limit for limit in PLATFORM_LIMITS)

    def test_bound_detail_surfaces_the_limits_to_the_operator(self):
        detail = assess_enforcement_identity(_att(**IDENTITY)).detail
        for limit in PLATFORM_LIMITS:
            assert limit in detail

    def test_spawn_inventory_count_is_reported(self):
        verdict = assess_enforcement_identity(
            _att(**{**IDENTITY, "spawn_image_inventory": ("a.exe", "b.exe", "c.exe")})
        )
        assert verdict.spawn_image_count == 3


class TestReadinessGateRefusesAnUnboundAttestation:
    """An unbound attestation must not satisfy Q9 even when probes pass."""

    def _satisfied_attestation(self, **identity):
        return {
            "schema": SCHEMA,
            "arm_id": "arm-1",
            "rollout_id": "rollout-1",
            "workspace": "C:/arms/1",
            "observer_pid": 1,
            "policy_identity": "p",
            "policy_sha256": "s",
            "interface_inventory": [],
            "dns_behavior": "declared",
            "negative_control": "permitted",
            "probes": [
                {"dimension": d, "outcome": o, "target": "t", "protocol": "tcp"}
                for d, o in self._passing_outcomes()
            ],
            **identity,
        }

    def _passing_outcomes(self):
        """Every required dimension observed with the outcome it requires."""
        out = [(d, "denied") for d in sorted(iso.DENIED_REQUIRED)]
        out += [(d, "permitted") for d in sorted(iso.PERMITTED_REQUIRED)]
        return out

    def test_gate_refuses_when_probes_pass_but_identity_absent(self):
        from qwen_train.f2_readiness import _check_no_egress

        ok, detail, action = _check_no_egress(
            {"no_egress_attestation": self._satisfied_attestation()}
        )
        assert ok is False
        assert "ENFORCEMENT IDENTITY" in detail
        assert "does NOT inherit to child processes" in action

    def test_gate_accepts_when_identity_is_bound(self):
        from qwen_train.f2_readiness import _check_no_egress

        ok, detail, action = _check_no_egress(
            {"no_egress_attestation": self._satisfied_attestation(**IDENTITY)}
        )
        assert ok is True, (detail, action)
        assert "enforcement=windows_account" in detail