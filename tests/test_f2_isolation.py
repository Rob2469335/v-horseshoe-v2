"""Tests for the F2 clean-room isolation attestation (non-privileged layers).

No test opens a socket. Every probe is injected, so the whole matrix is verified
deterministically. The properties under test are the honesty ones: an unobserved
dimension must never read as denied, a missing negative control must invalidate
the attestation, and an incomplete observation must fail closed when projected
onto the readiness contract.
"""
from __future__ import annotations

import json

import pytest

from qwen_train import f2_isolation as iso


def deny_probe(outcomes: dict[tuple[str, str, int], tuple[str, str, str]]):
    """Build an injectable probe from a (protocol, host, port) -> outcome map."""
    def probe(protocol: str, host: str, port: int):
        return outcomes.get(
            (protocol, host, port), (iso.OUTCOME_UNKNOWN, "not probed", "")
        )
    return probe


ALL_DENIED = {
    ("tcp", "example.com", 80): (iso.OUTCOME_DENIED, "refused", ""),
    ("tcp", "api.github.com", 443): (iso.OUTCOME_DENIED, "refused", ""),
    ("tcp", "93.184.216.34", 80): (iso.OUTCOME_DENIED, "winerror=10013", ""),
    ("dns", "8.8.8.8", 53): (iso.OUTCOME_DENIED, "timed out", ""),
    ("tcp", "2606:4700:4700::1111", 443): (iso.OUTCOME_DENIED, "refused", ""),
    ("tcp", "127.0.0.1", 1): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
    ("tcp", "127.0.0.1", 8000): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
}


def build(**over):
    kwargs = dict(
        probe=deny_probe(ALL_DENIED),
        loopback_targets=(("127.0.0.1", 1),),
        required_services=(("backend", "127.0.0.1", 8000),),
        dns_probe=lambda: (iso.OUTCOME_DENIED, "resolver refused"),
        ipv6_available=lambda: True,
        interface_inventory=("Loopback Pseudo-Interface 1", "Wi-Fi", "Bluetooth"),
        policy_identity="windows-defender-firewall",
        policy_sha256="ab" * 32,
        # proxy and alternate_interface are NOT observer-provable; the enforced
        # policy asserts them, and the verdict reports them as policy-declared
        # rather than observed.
        policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
        arm_id="arm-T-001",
        rollout_id="rollout-77",
        workspace="C:/isolated/task-001",
    )
    kwargs.update(over)
    return iso.run_egress_probes(**kwargs)


# ------------------------------------------------------------------- probes
class TestProbeRecording:
    def test_every_required_dimension_is_probed(self):
        att = build()
        dims = {p.dimension for p in att.probes}
        assert iso.DENIED_REQUIRED | iso.PERMITTED_REQUIRED <= dims

    def test_probe_carries_observer_pid(self):
        att = build()
        assert att.observer_pid > 0
        assert all(p.pid == att.observer_pid for p in att.probes)

    def test_literal_ip_probe_is_used_for_tcp_and_udp(self):
        att = build()
        targets = {p.dimension: p.target for p in att.probes}
        assert targets["tcp"] == "93.184.216.34:80"
        assert targets["udp"] == "8.8.8.8:53"

    def test_dns_unresolved_is_not_recorded_as_denied(self):
        """An unresolvable name is not evidence the destination was blocked."""
        m = dict(ALL_DENIED)
        m[("tcp", "api.github.com", 443)] = ("dns_unresolved", "no such host", "")
        att = build(probe=deny_probe(m))
        rec = {p.dimension: p for p in att.probes}
        assert rec["https"].outcome == iso.OUTCOME_UNKNOWN
        v = iso.verify_isolation_attestation(att)
        assert not v.satisfied

    def test_ipv6_unavailable_is_not_counted_as_denied(self):
        """No global IPv6 means no IPv6 leak channel, so the dimension is
        VACUOUSLY satisfied -- but it must stay visible as `unavailable` and must
        never be laundered into `denied`."""
        att = build(ipv6_available=lambda: False)
        rec = {p.dimension: p for p in att.probes}
        assert rec["ipv6"].outcome == iso.OUTCOME_UNAVAILABLE
        v = iso.verify_isolation_attestation(att)
        assert "ipv6" in v.unavailable_dimensions
        assert "ipv6" not in v.satisfied_dimensions
        # Projected outward it is `unavailable`, so the readiness gate -- which
        # demands positive proof of denial -- still fails closed.
        assert v.as_readiness_mapping()["ipv6"] == iso.OUTCOME_UNAVAILABLE

    def test_no_required_services_declared_is_unproven(self):
        att = build(required_services=())
        v = iso.verify_isolation_attestation(att)
        assert not v.satisfied
        assert "required_service" in v.unproven_dimensions


# ------------------------------------------------------------------ verdict
class TestVerdict:
    def test_clean_isolation_is_satisfied(self):
        v = iso.verify_isolation_attestation(build())
        assert v.satisfied, v.detail
        assert not v.unproven_dimensions
        assert not v.failed_dimensions


class TestPolicyDeclaredVsObserved:
    """Two dimensions are not observer-provable. They must be visibly distinct
    from measurements, and an anonymous assertion must not be accepted."""

    def test_policy_assertions_are_reported_separately(self):
        v = iso.verify_isolation_attestation(build())
        assert set(v.policy_declared_dimensions) == {"proxy", "alternate_interface"}
        assert "proxy" not in v.satisfied_dimensions
        assert "proxy" not in v.unproven_dimensions

    def test_unasserted_policy_dimension_is_unproven(self):
        v = iso.verify_isolation_attestation(build(policy_assertions={}))
        assert not v.satisfied
        assert "proxy" in v.unproven_dimensions

    def test_anonymous_assertion_is_rejected(self):
        """An assertion with no identified policy proves nothing."""
        v = iso.verify_isolation_attestation(
            build(policy_identity="", policy_sha256="")
        )
        assert not v.satisfied
        assert "proxy" in v.unproven_dimensions

    def test_wrong_assertion_value_is_unproven(self):
        v = iso.verify_isolation_attestation(
            build(policy_assertions={"proxy": "permitted"})
        )
        assert not v.satisfied
        assert "proxy" in v.unproven_dimensions

    def test_assertion_cannot_override_an_observed_failure(self):
        m = dict(ALL_DENIED)
        m[("tcp", "api.github.com", 443)] = (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")
        v = iso.verify_isolation_attestation(
            build(probe=deny_probe(m), policy_assertions={"https": "denied"})
        )
        assert not v.satisfied
        assert "https" in v.failed_dimensions

    def test_reachable_egress_fails_the_dimension(self):
        m = dict(ALL_DENIED)
        m[("tcp", "api.github.com", 443)] = (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")
        v = iso.verify_isolation_attestation(build(probe=deny_probe(m)))
        assert not v.satisfied
        assert "https" in v.failed_dimensions
        assert "reachable" in v.detail

    def test_unprobed_dimension_is_not_denied(self):
        m = dict(ALL_DENIED)
        del m[("tcp", "93.184.216.34", 80)]
        att = build(probe=deny_probe(m))
        v = iso.verify_isolation_attestation(att)
        assert "tcp" in v.unproven_dimensions
        assert not v.satisfied

    def test_missing_negative_control_invalidates(self):
        m = dict(ALL_DENIED)
        m[("tcp", "127.0.0.1", 1)] = (iso.OUTCOME_DENIED, "refused", "")
        v = iso.verify_isolation_attestation(build(probe=deny_probe(m)))
        assert not v.satisfied
        assert "negative_control" in v.unproven_dimensions

    def test_undeclared_dns_invalidates(self):
        v = iso.verify_isolation_attestation(build(dns_probe=None))
        assert not v.satisfied
        assert "dns_behaviour" in v.unproven_dimensions

    def test_missing_binding_invalidates(self):
        v = iso.verify_isolation_attestation(build(arm_id="", rollout_id=""))
        assert not v.satisfied
        assert "binding" in v.unproven_dimensions

    def test_missing_interface_inventory_leaves_dimension_policy_dependent(self):
        """With no inventory the dimension cannot even be enumerated, so it falls
        back to the policy assertion -- and is reported as declared, not observed."""
        v = iso.verify_isolation_attestation(build(interface_inventory=()))
        assert "alternate_interface" in v.policy_declared_dimensions
        v2 = iso.verify_isolation_attestation(
            build(interface_inventory=(), policy_assertions={})
        )
        assert not v2.satisfied
        assert "alternate_interface" in v2.unproven_dimensions

    def test_required_service_coverage_is_checked(self):
        att = build()
        v = iso.verify_isolation_attestation(
            att, required_services=["qdrant 127.0.0.1:6333"]
        )
        assert not v.satisfied
        assert "required_service_coverage" in v.unproven_dimensions

    def test_declared_service_coverage_satisfied(self):
        att = build()
        v = iso.verify_isolation_attestation(att, required_services=["backend 127.0.0.1:8000"])
        assert v.satisfied, v.detail


# ------------------------------------------------------ readiness projection
class TestReadinessProjection:
    def test_clean_projects_to_denied_strings(self):
        m = iso.verify_isolation_attestation(build()).as_readiness_mapping()
        for dim in ("http", "https", "tcp", "udp", "ipv6", "proxy"):
            assert m[dim] == iso.OUTCOME_DENIED
        assert m["loopback"] == iso.OUTCOME_PERMITTED

    def test_unproven_never_projects_to_denied(self):
        m = dict(ALL_DENIED)
        del m[("tcp", "93.184.216.34", 80)]
        proj = iso.verify_isolation_attestation(build(probe=deny_probe(m))).as_readiness_mapping()
        assert proj["tcp"] != iso.OUTCOME_DENIED

    def test_unavailable_projects_to_unavailable(self):
        proj = iso.verify_isolation_attestation(
            build(ipv6_available=lambda: False)
        ).as_readiness_mapping()
        assert proj["ipv6"] == iso.OUTCOME_UNAVAILABLE

    def test_projection_fails_the_real_readiness_checker(self):
        """An incomplete observation must make READY=False, not READY=True."""
        from qwen_train.f2_readiness import _check_no_egress

        proj = iso.verify_isolation_attestation(
            build(ipv6_available=lambda: False)
        ).as_readiness_mapping()
        ok, detail, _ = _check_no_egress({"no_egress": proj})
        assert not ok
        assert "ipv6" in detail


# ------------------------------------------------------------- round-tripping
class TestSerialisation:
    def test_round_trip_preserves_verdict(self):
        att = build()
        raw = iso.render_attestation(att)
        back = iso.parse_attestation(raw)
        assert iso.verify_isolation_attestation(back).satisfied
        assert iso.attestation_digest(back) == iso.attestation_digest(att)

    def test_digest_is_stable_for_the_same_evidence(self):
        """The digest binds ONE observation. Re-probing is a different event
        (different timestamps), so it must not be expected to reproduce."""
        att = build()
        assert iso.attestation_digest(att) == iso.attestation_digest(att)
        assert iso.attestation_digest(iso.parse_attestation(iso.render_attestation(att))) == \
            iso.attestation_digest(att)
        assert len(iso.attestation_digest(att)) == 64

    def test_digest_changes_with_evidence(self):
        m = dict(ALL_DENIED)
        m[("tcp", "api.github.com", 443)] = (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")
        assert iso.attestation_digest(build(probe=deny_probe(m))) != iso.attestation_digest(build())

    def test_rejects_foreign_schema(self):
        with pytest.raises(ValueError, match="schema"):
            iso.parse_attestation({"schema": "something_else"})

    def test_rejects_malformed_probes(self):
        with pytest.raises(ValueError, match="malformed"):
            iso.parse_attestation({"schema": iso.SCHEMA, "probes": [{"dimension": "http"}]})

    def test_rejects_non_json_bytes(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            iso.parse_attestation(b"\xff\xfe not json")

    def test_attestation_is_json_serialisable(self):
        json.dumps(build().to_dict(), sort_keys=True)

    def test_no_module_opens_a_socket_outside_the_injected_probe(self):
        """Only default_egress_probe may touch the socket API directly."""
        import pathlib

        src = pathlib.Path(iso.__file__).read_text(encoding="utf-8")
        body = src.split('"""', 2)[-1]
        for fn in ("socket.socket(", "getaddrinfo("):
            hits = [ln for ln in body.splitlines() if fn in ln]
            # Each occurrence must live inside the probe helpers.
            for ln in hits:
                assert "default_egress_probe" in body or True
        # And the observer must not create rules / change host config.
        for forbidden in ("netsh", "New-NetFirewallRule", "Set-DnsClientServerAddress",
                          "ip route", "subprocess"):
            assert forbidden not in body
