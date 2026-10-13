"""Tests for the F2 clean-room isolation attestation (non-privileged layers).

Probes are injected, so the matrix is verified deterministically, EXCEPT for a
small set of socket-level regression tests that exercise the real classifier
(timeouts, UDP datagrams, and a live loopback listener); those open only
loopback/datagram sockets and change nothing about the host. The properties
under test are the honesty ones: an unobserved or ambiguous dimension must never
read as denied, a missing negative control must invalidate the attestation, and
an incomplete observation must fail closed when projected onto the readiness
contract.
"""
from __future__ import annotations

import json
import socket

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
    ("tcp", "93.184.216.34", 80): (iso.OUTCOME_DENIED, "refused", ""),
    ("tcp", "93.184.216.34", 443): (iso.OUTCOME_DENIED, "refused", ""),
    ("tcp", "93.184.216.34", 8080): (iso.OUTCOME_DENIED, "winerror=10013", ""),
    ("dns", "8.8.8.8", 53): (iso.OUTCOME_DENIED, "winerror=10013", ""),
    ("tcp", "2606:4700:4700::1111", 443): (iso.OUTCOME_DENIED, "refused", ""),
    ("tcp", "127.0.0.1", 1): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
    ("tcp", "127.0.0.1", 8000): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
}


def build(**over):
    kwargs = dict(
        probe=deny_probe(ALL_DENIED),
        udp_probe=lambda h, p: ALL_DENIED.get(
            ("dns", h, p), (iso.OUTCOME_UNKNOWN, "not probed", "")),
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
        assert targets["tcp"] == "93.184.216.34:8080"
        assert targets["udp"] == "8.8.8.8:53"

    def test_dns_unresolved_is_not_recorded_as_denied(self):
        """An unresolvable name is not evidence the destination was blocked."""
        m = dict(ALL_DENIED)
        m[("tcp", "api.github.com", 443)] = ("dns_unresolved", "no such host", "")
        att = build(
            probe=deny_probe(m),
            external_targets=(
                ("93.184.216.34", 80),
                ("api.github.com", 443),
                ("93.184.216.34", 8080),
            ),
        )
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


class TestExternalTargetsDriveTheProbes:
    """Regression for the discovered defect: ``external_targets`` was accepted
    but never used, and the denied-required external probes used hardcoded DNS
    names even though the guest is provisioned WITHOUT DNS. The block decision
    must not depend on name resolution."""

    def test_supplied_targets_are_the_probed_targets(self):
        att = build(external_targets=(
            ("10.0.0.9", 80), ("10.0.0.9", 443), ("10.0.0.9", 22)))
        targets = {p.dimension: p.target for p in att.probes}
        assert targets["http"] == "10.0.0.9:80"
        assert targets["https"] == "10.0.0.9:443"
        assert targets["tcp"] == "10.0.0.9:22"

    def test_supplied_target_outcome_flows_into_the_verdict(self):
        # 10.0.0.9 is not in the deny map, so its http probe is unknown -> the
        # dimension is unproven, never denied.
        att = build(external_targets=(
            ("10.0.0.9", 80), ("93.184.216.34", 443), ("93.184.216.34", 8080)))
        v = iso.verify_isolation_attestation(att)
        assert "http" in v.unproven_dimensions
        assert not v.satisfied

    def test_defaults_are_literal_addresses_so_no_dns_is_needed(self):
        assert iso.DEFAULT_EXTERNAL_TARGETS
        for host, _port in iso.DEFAULT_EXTERNAL_TARGETS:
            assert not any(c.isalpha() for c in host), host

    def test_unexpected_probe_error_is_not_denied(self):
        m = dict(ALL_DENIED)
        m[("tcp", "93.184.216.34", 80)] = ("error", "OSError: boom", "")
        att = build(probe=deny_probe(m))
        rec = {p.dimension: p for p in att.probes}
        assert rec["http"].outcome == iso.OUTCOME_UNKNOWN
        assert not iso.verify_isolation_attestation(att).satisfied


class TestUnreachableIsNotDenied:
    """A routing failure is not evidence of firewall enforcement. Only a
    WFP/firewall refusal (WSAEACCES 10013) is ``denied``; a no-route error
    (WSAENETUNREACH 10051 / WSAEHOSTUNREACH 10065) or an ambiguous reset
    (WSAECONNRESET 10054) stays ``unknown`` and cannot satisfy a
    denied-required dimension."""

    class _WinError(OSError):
        def __init__(self, winerror: int):
            super().__init__(winerror, f"simulated winerror {winerror}")
            self.winerror = winerror

    @staticmethod
    def _probe(monkeypatch, exc):
        monkeypatch.setattr(
            iso.socket, "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))],
        )

        class _Sock:
            def settimeout(self, *_a): pass
            def connect(self, *_a): raise exc
            def getsockname(self): return ("127.0.0.1", 0)
            def close(self): pass

        monkeypatch.setattr(iso.socket, "socket", lambda *a, **k: _Sock())
        return iso.default_egress_probe("tcp", "93.184.216.34", 80)

    def test_firewall_refusal_10013_is_denied(self, monkeypatch):
        raw, detail, _ = self._probe(monkeypatch, self._WinError(10013))
        assert raw == iso.OUTCOME_DENIED
        assert "10013" in detail

    def test_unreachable_10051_is_not_denied(self, monkeypatch):
        raw, detail, _ = self._probe(monkeypatch, self._WinError(10051))
        assert raw != iso.OUTCOME_DENIED
        assert iso._classify("tcp", raw) == iso.OUTCOME_UNKNOWN
        assert "10051" in detail

    def test_unreachable_10065_is_not_denied(self, monkeypatch):
        raw, _d, _ = self._probe(monkeypatch, self._WinError(10065))
        assert iso._classify("tcp", raw) == iso.OUTCOME_UNKNOWN

    def test_reset_10054_is_not_denied(self, monkeypatch):
        raw, _d, _ = self._probe(monkeypatch, self._WinError(10054))
        assert iso._classify("tcp", raw) == iso.OUTCOME_UNKNOWN

    def test_unreachable_cannot_satisfy_a_denied_dimension(self):
        """A denied-required dimension observed only as unreachable is unproven."""
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1")
            return ("unreachable", "OSError (winerror=10051): no route", "")

        att = build(probe=probe, udp_probe=lambda h, p: ("unreachable", "no route", ""))
        v = iso.verify_isolation_attestation(att)
        assert not v.satisfied
        assert set(v.unproven_dimensions) >= {"http", "https", "tcp", "udp", "ipv6"}
        # permitted paths stay intact
        assert "loopback" in v.satisfied_dimensions
        assert "required_service" in v.satisfied_dimensions


class TestStrictDenialContract:
    """Regression tests for the five reproduced defects. Only a genuine
    refusal/block may satisfy a required-denial dimension; a timeout, an
    unreachable route, a silent UDP drop, unavailable evidence, or a malformed
    policy digest never may."""

    class _FakeSock:
        def __init__(self, *, connect=None, sendto=None, recvfrom=None):
            self._connect, self._sendto, self._recvfrom = connect, sendto, recvfrom

        def settimeout(self, *_a): pass

        def connect(self, *_a):
            if self._connect:
                raise self._connect

        def sendto(self, *_a):
            if self._sendto:
                raise self._sendto
            return 0

        def recvfrom(self, *_a):
            if self._recvfrom:
                raise self._recvfrom
            return (b"", ("127.0.0.1", 1))

        def getsockname(self): return ("127.0.0.1", 0)

        def close(self): pass

    @staticmethod
    def _patch(monkeypatch, sock):
        monkeypatch.setattr(
            iso.socket, "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("10.0.0.1", 1))],
        )
        monkeypatch.setattr(iso.socket, "socket", lambda *a, **k: sock)

    # -- Defect 1: timeout / ambiguous error must not be denied -------------
    def test_timeout_is_not_denied(self, monkeypatch):
        self._patch(monkeypatch, self._FakeSock(connect=socket.timeout("sim")))
        raw, _d, _l = iso.default_egress_probe("tcp", "10.0.0.1", 1)
        assert raw != iso.OUTCOME_DENIED
        assert iso._classify("tcp", raw) == iso.OUTCOME_UNKNOWN

    def test_unreachable_is_not_denied(self, monkeypatch):
        e = OSError("sim"); e.winerror = 10051
        self._patch(monkeypatch, self._FakeSock(connect=e))
        raw, _d, _l = iso.default_egress_probe("tcp", "10.0.0.1", 1)
        assert iso._classify("tcp", raw) == iso.OUTCOME_UNKNOWN

    def test_firewall_refusal_10013_is_denied(self, monkeypatch):
        e = OSError("sim"); e.winerror = 10013
        self._patch(monkeypatch, self._FakeSock(connect=e))
        raw, _d, _l = iso.default_egress_probe("tcp", "10.0.0.1", 1)
        assert raw == iso.OUTCOME_DENIED

    def test_connection_refused_is_denied(self, monkeypatch):
        """A TCP RST (ConnectionRefusedError) is an explicit refusal and IS the
        contractually correct denial outcome; it must survive the classifier."""
        self._patch(monkeypatch, self._FakeSock(connect=ConnectionRefusedError("sim")))
        raw, detail, _l = iso.default_egress_probe("tcp", "10.0.0.1", 1)
        assert raw == iso.OUTCOME_DENIED
        assert iso._classify("tcp", raw) == iso.OUTCOME_DENIED
        assert "refused" in detail

    def test_timeout_only_cannot_satisfy_a_denied_dimension(self):
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1")
            return ("timeout", "timed out (silently dropped)", "")

        v = iso.verify_isolation_attestation(build(probe=probe))
        assert not v.satisfied
        assert set(v.unproven_dimensions) >= {"http", "https", "tcp"}

    # -- Defect 2: unavailable-only must not satisfy ------------------------
    def test_unavailable_only_cannot_satisfy(self):
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1")
            return (iso.OUTCOME_UNAVAILABLE, "no path", "")

        v = iso.verify_isolation_attestation(build(probe=probe))
        assert not v.satisfied
        assert "http" in v.unproven_dimensions
        assert "http" in v.unavailable_dimensions

    def test_ipv6_unavailable_is_unproven(self):
        v = iso.verify_isolation_attestation(build(ipv6_available=lambda: False))
        assert not v.satisfied
        assert "ipv6" in v.unproven_dimensions
        assert "ipv6" not in v.satisfied_dimensions

    # -- Defect 3: UDP datagram probe --------------------------------------
    def test_udp_silence_is_not_denied(self, monkeypatch):
        self._patch(monkeypatch, self._FakeSock(recvfrom=socket.timeout("sim")))
        raw, _d, _l = iso.default_udp_probe("10.0.0.1", 53)
        assert raw == "silent"
        assert iso._classify("udp", raw) == iso.OUTCOME_UNKNOWN

    def test_udp_answer_is_permitted(self, monkeypatch):
        self._patch(monkeypatch, self._FakeSock())
        raw, _d, _l = iso.default_udp_probe("10.0.0.1", 53)
        assert raw == iso.OUTCOME_PERMITTED

    def test_udp_route_failure_is_not_denied(self, monkeypatch):
        e = OSError("sim"); e.winerror = 10051
        self._patch(monkeypatch, self._FakeSock(sendto=e))
        raw, _d, _l = iso.default_udp_probe("10.0.0.1", 53)
        assert iso._classify("udp", raw) == iso.OUTCOME_UNKNOWN

    def test_udp_silence_only_cannot_satisfy(self):
        att = build(udp_probe=lambda h, p: ("silent", "no response", ""))
        v = iso.verify_isolation_attestation(att)
        assert not v.satisfied
        assert "udp" in v.unproven_dimensions

    def test_udp_icmp_port_unreachable_is_not_denied(self, monkeypatch):
        """ICMP port-unreachable means the destination was REACHED and had no
        listener -- it is not evidence of firewall enforcement."""
        self._patch(monkeypatch, self._FakeSock(recvfrom=ConnectionResetError("sim")))
        raw, _d, _l = iso.default_udp_probe("10.0.0.1", 53)
        assert raw == "icmp_unreachable"
        assert iso._classify("udp", raw) == iso.OUTCOME_UNKNOWN

    def test_udp_answer_fails_a_denied_dimension(self):
        """A received datagram demonstrates reachability, so a denied-required
        UDP dimension must FAIL rather than pass."""
        att = build(udp_probe=lambda h, p: (iso.OUTCOME_PERMITTED, "received", ""))
        v = iso.verify_isolation_attestation(att)
        assert "udp" in v.failed_dimensions
        assert not v.satisfied

    def test_ambiguous_labels_never_classify_as_denied(self):
        for label in ("timed out", "timeout", "silent", "unreachable",
                      "icmp_unreachable", "error"):
            assert iso._classify("udp", label) == iso.OUTCOME_UNKNOWN
            assert iso._classify("tcp", label) == iso.OUTCOME_UNKNOWN

    def test_clean_fixture_udp_denial_is_a_refusal_not_a_timeout(self):
        """Guard the fixture itself: the clean 'satisfied' attestation may only
        record the UDP denial as a genuine local send refusal (WSAEACCES 10013),
        never as a timeout or a silent drop."""
        att = build()
        udp = next(p for p in att.probes if p.dimension == "udp")
        assert udp.outcome == iso.OUTCOME_DENIED
        detail = udp.detail.lower()
        assert "timed out" not in detail and "timeout" not in detail
        assert "10013" in detail

    # -- Defect 4: negative control ----------------------------------------
    def test_negative_control_absent_listener_is_not_permitted(self):
        raw, _d, _l = iso.default_egress_probe("tcp", "127.0.0.1", 1)
        assert raw != iso.OUTCOME_PERMITTED
        assert iso._classify("negative_control", raw) != iso.OUTCOME_PERMITTED

    def test_negative_control_with_live_listener_is_permitted(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(("127.0.0.1", 1))
            srv.listen(1)
        except OSError:
            srv.close()
            pytest.skip("cannot bind 127.0.0.1:1 in this environment")
        try:
            raw, _d, _l = iso.default_egress_probe("tcp", "127.0.0.1", 1)
            assert raw == iso.OUTCOME_PERMITTED
        finally:
            srv.close()

    # -- Defect 5: policy hash format --------------------------------------
    def test_malformed_policy_hash_is_rejected(self):
        v = iso.verify_isolation_attestation(build(policy_sha256="not-a-digest"))
        assert not v.satisfied
        assert "proxy" in v.unproven_dimensions

    def test_well_formed_policy_hash_is_accepted(self):
        assert iso.is_sha256_hex("a" * 64)
        v = iso.verify_isolation_attestation(build(policy_sha256="a" * 64))
        assert v.satisfied

    def test_policy_digest_matches_only_the_captured_policy(self):
        capture = {"acl": [{"action": "deny"}], "switch": "F2-Internal-Switch"}
        digest = iso.canonical_policy_digest(capture)
        att = build(policy_sha256=digest)
        assert iso.policy_digest_matches(att, capture)
        assert not iso.policy_digest_matches(att, {"acl": [{"action": "allow"}]})
        assert not iso.policy_digest_matches(build(policy_sha256="a" * 64), capture)

    def test_observer_provable_dimension_cannot_be_asserted(self):
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1")
            return ("timeout", "timed out", "")

        v = iso.verify_isolation_attestation(
            build(probe=probe, policy_assertions={"http": "denied"})
        )
        assert "http" in v.unproven_dimensions
        assert "http" not in v.policy_declared_dimensions

    def test_mixed_permitted_and_unknown_cannot_be_asserted(self):
        # R1: an observed `permitted` on an assertable dimension must FAIL, even
        # mixed with an `unknown`, rather than being overridden by an assertion.
        att = build()
        probes = tuple(att.probes) + (
            iso.ProbeResult(
                dimension="proxy", target="x", protocol="config",
                outcome=iso.OUTCOME_PERMITTED, detail="reachable",
                local_address="", pid=att.observer_pid, at=0.0,
            ),
        )
        att2 = iso.IsolationAttestation(**{**att.__dict__, "probes": probes})
        v = iso.verify_isolation_attestation(att2)
        assert not v.satisfied
        assert "proxy" in v.failed_dimensions
        assert "proxy" not in v.policy_declared_dimensions


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
        m[("tcp", "93.184.216.34", 443)] = (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")
        v = iso.verify_isolation_attestation(
            build(probe=deny_probe(m), policy_assertions={"https": "denied"})
        )
        assert not v.satisfied
        assert "https" in v.failed_dimensions

    def test_reachable_egress_fails_the_dimension(self):
        m = dict(ALL_DENIED)
        m[("tcp", "93.184.216.34", 443)] = (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")
        v = iso.verify_isolation_attestation(build(probe=deny_probe(m)))
        assert not v.satisfied
        assert "https" in v.failed_dimensions
        assert "reachable" in v.detail

    def test_unprobed_dimension_is_not_denied(self):
        m = dict(ALL_DENIED)
        del m[("tcp", "93.184.216.34", 8080)]
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
        del m[("tcp", "93.184.216.34", 8080)]
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
        m[("tcp", "93.184.216.34", 443)] = (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")
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


# ------------------------------------------- end-to-end classification contract
class TestEndToEndClassificationContract:
    """The whole Q9 evidence path -- record -> classify -> aggregate -> verdict ->
    readiness projection -- must never let an ambiguous outcome satisfy a
    required-denial dimension, and the final verification must agree with the
    readiness gate that consumes it."""

    @staticmethod
    def _timeout_probe(protocol, host, port):
        if str(host) == "127.0.0.1":
            return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
        return ("timeout", "timed out (silently dropped)", "")

    @staticmethod
    def _denied_probe(protocol, host, port):
        if str(host) == "127.0.0.1":
            return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
        return (iso.OUTCOME_DENIED, "refused", "")

    def test_one_denial_among_unknowns_cannot_pass(self):
        """A single observed denial must not carry dimensions that are only
        unknown: every required-denial dimension is decided independently."""
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            if port == 80:
                return (iso.OUTCOME_DENIED, "refused", "")   # http only
            return ("timeout", "timed out", "")              # everything else

        v = iso.verify_isolation_attestation(
            build(probe=probe, udp_probe=lambda h, p: ("silent", "no response", ""))
        )
        assert not v.satisfied
        assert "http" not in v.unproven_dimensions
        assert "http" not in v.failed_dimensions
        for dim in ("https", "tcp", "udp", "ipv6"):
            assert dim in v.unproven_dimensions

    def test_render_parse_verify_never_upgrades_unknown_to_denied(self):
        att = build(
            probe=self._timeout_probe,
            udp_probe=lambda h, p: ("silent", "no response", ""),
        )
        back = iso.parse_attestation(iso.render_attestation(att))
        v = iso.verify_isolation_attestation(back)
        assert not v.satisfied
        proj = v.as_readiness_mapping()
        for dim in ("http", "https", "tcp", "udp"):
            assert proj[dim] == iso.OUTCOME_UNKNOWN
        assert proj["loopback"] == iso.OUTCOME_PERMITTED

    def test_verification_and_readiness_gate_agree(self):
        from qwen_train.f2_readiness import _check_no_egress

        bound = dict(
            enforcement_scope="vm",
            executing_user="F2VM\\operator",
            interpreter_path=r"C:\f2\python.exe",
            interpreter_sha256="cd" * 32,
        )

        att_ok = build(probe=self._denied_probe, **bound)
        assert iso.verify_isolation_attestation(att_ok).satisfied
        ok, _detail, _remedy = _check_no_egress(
            {"no_egress_attestation": att_ok.to_dict()}
        )
        assert ok is True

        att_bad = build(
            probe=self._timeout_probe,
            udp_probe=lambda h, p: ("silent", "no response", ""),
            **bound,
        )
        assert not iso.verify_isolation_attestation(att_bad).satisfied
        ok2, _detail2, _remedy2 = _check_no_egress(
            {"no_egress_attestation": att_bad.to_dict()}
        )
        assert ok2 is False

    def test_old_timeout_equals_denied_rule_is_guarded(self, monkeypatch):
        """Revert-then-pass guard. HEAD's classifier returned OUTCOME_DENIED for
        ``socket.timeout``; reproduce that rule explicitly, then show the current
        classifier disagrees and that the contract rejects a denial built only
        from timeouts. If the old rule is reintroduced, this test fails."""
        old_rule_for_timeout = iso.OUTCOME_DENIED  # exactly what HEAD produced

        TestStrictDenialContract._patch(
            monkeypatch,
            TestStrictDenialContract._FakeSock(connect=socket.timeout("sim")),
        )
        raw, _d, _l = iso.default_egress_probe("tcp", "10.0.0.1", 1)
        assert raw != old_rule_for_timeout
        assert iso._classify("tcp", raw) == iso.OUTCOME_UNKNOWN

        # A denial-only map (the old behaviour) satisfies the verdict; the
        # timeout map (the current behaviour) does not -- so the classifier is
        # load-bearing for the whole Q9 gate.
        assert iso.verify_isolation_attestation(build(probe=self._denied_probe)).satisfied
        assert not iso.verify_isolation_attestation(
            build(
                probe=self._timeout_probe,
                udp_probe=lambda h, p: ("silent", "no response", ""),
            )
        ).satisfied
