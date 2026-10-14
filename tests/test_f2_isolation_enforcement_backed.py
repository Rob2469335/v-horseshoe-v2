"""Regression tests for enforcement-backed denial (2026-10-13).

The defect these pin down: the F2 clean-room contract demanded an observed
``WSAEACCES``/RST refusal for every denied-required dimension, but a Hyper-V VM
extended ACL enforces by SILENTLY DROPPING in the vSwitch, which an unprivileged
observer records as a timeout.  A correctly enforced ACL boundary was therefore
unattestable.

The fix must never degrade the honesty contract, so each test below asserts what
is still refused as much as what is now accepted:

* a timeout is still never promoted on its own;
* a capture whose effective rule for the target is an ALLOW does not back;
* a capture can never erase an observed reachable path;
* the negative control can never be satisfied by a capture;
* a capture that does not hash to the attestation's claim is refused.

Every capture used here is a literal Hyper-V extended-ACL read-back in the
``f2_enforcement_capture_v1`` shape, so the tests exercise the real coverage
derivation rather than a stand-in.
"""
from __future__ import annotations

import json

import pytest

from qwen_train import f2_isolation as iso


# The live 12-rule F2-S8-VM1 ACL (post 2026-10-13 change), in capture shape.
S8_RULES = [
    {"direction": "outbound", "action": "allow", "remote_ip": "10.73.0.1",
     "protocol": "tcp", "weight": 900, "stateful": True},
    {"direction": "outbound", "action": "allow", "remote_ip": "10.73.0.1",
     "protocol": "udp", "weight": 901, "stateful": True},
    {"direction": "outbound", "action": "deny", "remote_ip": "10.0.0.0/8",
     "protocol": "any", "weight": 800, "stateful": False},
    {"direction": "outbound", "action": "deny", "remote_ip": "192.168.0.0/16",
     "protocol": "any", "weight": 700, "stateful": False},
    {"direction": "outbound", "action": "deny", "remote_ip": "172.16.0.0/12",
     "protocol": "any", "weight": 600, "stateful": False},
    {"direction": "outbound", "action": "deny", "remote_ip": "169.254.0.0/16",
     "protocol": "any", "weight": 500, "stateful": False},
    {"direction": "outbound", "action": "deny", "remote_ip": "10.72.0.0/24",
     "protocol": "any", "weight": 400, "stateful": False},
    {"direction": "outbound", "action": "deny", "remote_ip": "0.0.0.0/0",
     "protocol": "any", "weight": 350, "stateful": False},
    {"direction": "outbound", "action": "allow", "remote_ip": "0.0.0.0/0",
     "protocol": "tcp", "weight": 300, "stateful": True},
    {"direction": "outbound", "action": "allow", "remote_ip": "0.0.0.0/0",
     "protocol": "udp", "weight": 301, "stateful": True},
    {"direction": "outbound", "action": "deny", "remote_ip": "ANY",
     "protocol": "any", "weight": 1, "stateful": False},
    {"direction": "inbound", "action": "deny", "remote_ip": "ANY",
     "protocol": "any", "weight": 1, "stateful": False},
]

S8_SUBJECT = {"vm_name": "F2-S8-VM1", "vm_id": "c5d294f5-5515-46b3-bc80-ddc70dd94fed",
              "adapter_mac": "00155D56D603"}

PUBLIC_TARGETS = iso.DEFAULT_EXTERNAL_TARGETS


def capture(rules=S8_RULES, subject=S8_SUBJECT, **over):
    body = {
        "schema": iso.ENFORCEMENT_CAPTURE_SCHEMA,
        "kind": "hyper-v-extended-acl",
        "subject": dict(subject),
        "captured_at": "2026-10-13T18:53:48Z",
        "rules": [dict(r) for r in rules],
    }
    body.update(over)
    return body


def timeout_probe(protocol, host, port):
    if str(host) == "127.0.0.1":
        return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
    return ("timeout", "timed out (silently dropped or filtered; not a refusal)", "")


def silent_udp(_host, _port):
    return ("silent", "sent; no response (silent drop or absent listener)", "")


def build_att(**over):
    kwargs = dict(
        probe=timeout_probe,
        udp_probe=silent_udp,
        loopback_targets=(("127.0.0.1", 1),),
        required_services=(("channel", "127.0.0.1", 2179),),
        dns_probe=lambda: ("timeout", "timed out"),
        # IPv6 is present and also only drops, so it must be backed like the
        # IPv4 dimensions -- the S8 capture's `deny ANY` rule covers it.
        ipv6_available=lambda: True,
        interface_inventory=("Ethernet 2",),
        policy_identity="hyper-v-extended-acl",
        policy_sha256="ab" * 32,
        policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
        arm_id="s8-arm-001",
        rollout_id="s8-rollout-1",
        workspace="C:/f2_s8",
        external_targets=PUBLIC_TARGETS,
        udp_target=("8.8.8.8", 53),
        enforcement_scope="vm",
        executing_user="DESKTOP-H6A0HQS\\rob3",
        interpreter_path=r"C:\f2_tooling\python.exe",
        interpreter_sha256="cd" * 32,
    )
    kwargs.update(over)
    return iso.run_egress_probes(**kwargs)


def backed(cap, **over):
    """An attestation claiming ``cap``'s digest, as the host driver would build."""
    return build_att(
        enforcement_capture_sha256=iso.canonical_enforcement_digest(cap), **over
    )


# ---------------------------------------------------------- capture parsing
class TestCaptureParsing:
    def test_real_acl_capture_parses(self):
        parsed = iso.parse_enforcement_capture(capture())
        assert parsed["kind"] == "hyper-v-extended-acl"
        assert len(parsed["rules"]) == len(S8_RULES)
        assert parsed["subject"]["vm_id"] == S8_SUBJECT["vm_id"]

    def test_digest_is_deterministic_and_content_bound(self):
        assert iso.canonical_enforcement_digest(capture()) == \
            iso.canonical_enforcement_digest(capture())
        assert iso.canonical_enforcement_digest(capture()) != \
            iso.canonical_enforcement_digest(capture(rules=S8_RULES[:-1]))

    @pytest.mark.parametrize("mutate,message", [
        ({"schema": "something_else"}, "schema"),
        ({"kind": "windows-firewall"}, "derive"),
        ({"subject": {"vm_name": "", "vm_id": "x", "adapter_mac": "y"}}, "vm_name"),
        ({"rules": []}, "no rules"),
        ({"rules": [{"direction": "sideways", "action": "deny",
                     "remote_ip": "0.0.0.0/0", "protocol": "any", "weight": 1}]},
         "direction"),
        ({"rules": [{"direction": "outbound", "action": "shrug",
                     "remote_ip": "0.0.0.0/0", "protocol": "any", "weight": 1}]},
         "action"),
        ({"rules": [{"direction": "outbound", "action": "deny",
                     "remote_ip": "0.0.0.0/0", "protocol": "any"}]},
         "weight"),
    ])
    def test_malformed_capture_is_refused(self, mutate, message):
        body = capture()
        body.update(mutate)
        with pytest.raises(ValueError, match=message):
            iso.parse_enforcement_capture(body)

    def test_non_object_capture_is_refused(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            iso.parse_enforcement_capture(b"\xff\xfe not json")
        with pytest.raises(ValueError, match="object"):
            iso.parse_enforcement_capture(["nope"])


# ------------------------------------------------------- coverage derivation
class TestCoverageDerivation:
    def test_public_egress_is_effectively_denied(self):
        assert iso.enforcement_denies(capture(), "1.1.1.1", "tcp")
        assert iso.enforcement_denies(capture(), "8.8.8.8", "udp")

    def test_gateway_allow_outranks_the_deny(self):
        """The whole point of weight 350: the 10.73.0.1 gateway path survives."""
        rule = iso.effective_outbound_rule(capture(), "10.73.0.1", "tcp")
        assert rule is not None and rule["action"] == "allow" and rule["weight"] == 900
        assert not iso.enforcement_denies(capture(), "10.73.0.1", "tcp")

    def test_a_buried_deny_is_not_coverage(self):
        """A deny beneath a higher-weight allow must never back a denial."""
        buried = [
            {"direction": "outbound", "action": "allow", "remote_ip": "0.0.0.0/0",
             "protocol": "any", "weight": 500, "stateful": True},
            {"direction": "outbound", "action": "deny", "remote_ip": "0.0.0.0/0",
             "protocol": "any", "weight": 350, "stateful": False},
        ]
        assert not iso.enforcement_denies(capture(rules=buried), "1.1.1.1", "tcp")

    def test_a_tie_is_ambiguous_and_never_coverage(self):
        tied = [
            {"direction": "outbound", "action": "deny", "remote_ip": "0.0.0.0/0",
             "protocol": "any", "weight": 400, "stateful": False},
            {"direction": "outbound", "action": "allow", "remote_ip": "0.0.0.0/0",
             "protocol": "any", "weight": 400, "stateful": True},
        ]
        assert iso.effective_outbound_rule(capture(rules=tied), "1.1.1.1", "tcp") is None
        assert not iso.enforcement_denies(capture(rules=tied), "1.1.1.1", "tcp")

    def test_protocol_must_match(self):
        tcp_only = [{"direction": "outbound", "action": "deny", "remote_ip": "0.0.0.0/0",
                     "protocol": "tcp", "weight": 900, "stateful": False}]
        cap = capture(rules=tcp_only)
        assert iso.enforcement_denies(cap, "1.1.1.1", "tcp")
        assert not iso.enforcement_denies(cap, "1.1.1.1", "udp")

    def test_subnet_and_any_specs(self):
        cap = capture(rules=[
            {"direction": "outbound", "action": "deny", "remote_ip": "10.72.0.0/24",
             "protocol": "any", "weight": 900, "stateful": False},
            {"direction": "outbound", "action": "deny", "remote_ip": "ANY",
             "protocol": "any", "weight": 800, "stateful": False},
        ])
        assert iso.enforcement_denies(cap, "10.72.0.2", "tcp")
        assert iso.enforcement_denies(cap, "203.0.113.9", "tcp")

    def test_ipv4_only_deny_does_not_cover_an_ipv6_target(self):
        cap = capture(rules=[
            {"direction": "outbound", "action": "deny", "remote_ip": "0.0.0.0/0",
             "protocol": "any", "weight": 900, "stateful": False},
        ])
        assert not iso.enforcement_denies(cap, "2606:4700:4700::1111", "tcp")

    def test_unparsable_address_or_spec_is_never_coverage(self):
        assert not iso.enforcement_denies(capture(), "not-an-ip", "tcp")
        broken = [{"direction": "outbound", "action": "deny", "remote_ip": "999.0.0.1/8",
                   "protocol": "any", "weight": 900, "stateful": False}]
        assert not iso.enforcement_denies(capture(rules=broken), "1.1.1.1", "tcp")


# ------------------------------------------------ the accepted, and only the
class TestEnforcementBackedAcceptance:
    def test_timeout_plus_capture_satisfies_but_is_reported_separately(self):
        cap = capture()
        v = iso.verify_isolation_attestation(backed(cap), enforcement_capture=cap)
        assert v.satisfied, v.detail
        for dim in ("http", "https", "tcp", "udp"):
            assert dim in v.enforcement_backed_dimensions
            assert dim not in v.satisfied_dimensions
        assert v.enforcement_capture_digest == iso.canonical_enforcement_digest(cap)
        assert "loopback" in v.satisfied_dimensions

    def test_strict_contract_without_the_capture_still_fails(self):
        """Revert-then-pass guard: remove the enforcement capture argument and
        the very same attestation must fall back to UNPROVEN."""
        cap = capture()
        v = iso.verify_isolation_attestation(backed(cap))
        assert not v.satisfied
        assert set(v.unproven_dimensions) >= {"http", "https", "tcp", "udp"}
        assert not v.enforcement_backed_dimensions

    def test_observed_refusal_still_satisfies_without_any_capture(self):
        def refused(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            return (iso.OUTCOME_DENIED, "connection refused: sim", "")

        att = build_att(probe=refused, udp_probe=lambda h, p: (
            iso.OUTCOME_DENIED, "winerror=10013", ""), enforcement_scope="")
        v = iso.verify_isolation_attestation(att)
        assert v.satisfied, v.detail
        assert not v.enforcement_backed_dimensions

    def test_capture_never_erases_an_observed_reachable_path(self):
        def leaked(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            if port == 443:
                return (iso.OUTCOME_PERMITTED, "connected to the https target", "10.73.0.2")
            return ("timeout", "timed out", "")

        cap = capture()
        v = iso.verify_isolation_attestation(backed(cap, probe=leaked),
                                            enforcement_capture=cap)
        assert not v.satisfied
        assert "https" in v.failed_dimensions
        assert "https" not in v.enforcement_backed_dimensions

    def test_negative_control_can_never_be_satisfied_by_a_capture(self):
        """A broken probe harness must not be rescued by perfect enforcement."""
        cap = capture()
        att = backed(cap)
        broken = iso.IsolationAttestation(
            **{**att.__dict__, "negative_control": iso.OUTCOME_DENIED})
        v = iso.verify_isolation_attestation(broken, enforcement_capture=cap)
        assert not v.satisfied
        assert "negative_control" in v.unproven_dimensions

    def test_unavailable_is_not_a_drop_and_is_not_backed(self):
        def no_path(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            return (iso.OUTCOME_UNAVAILABLE, "no global IPv6 address", "")

        cap = capture()
        att = build_att(probe=no_path, ipv6_available=lambda: True,
                        enforcement_capture_sha256=iso.canonical_enforcement_digest(cap))
        v = iso.verify_isolation_attestation(att, enforcement_capture=cap)
        assert "ipv6" not in v.enforcement_backed_dimensions
        assert "ipv6" in v.unproven_dimensions

    def test_a_dns_name_target_is_never_backable(self):
        """Coverage is derived from a literal address; a name makes it
        un-derivable, which is the correct refusal."""
        def named(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            return ("timeout", "timed out", "")

        cap = capture()
        att = build_att(
            probe=named,
            external_targets=(("example.com", 80), ("example.com", 443),
                              ("example.com", 8080)),
            enforcement_capture_sha256=iso.canonical_enforcement_digest(cap),
        )
        v = iso.verify_isolation_attestation(att, enforcement_capture=cap)
        assert not v.satisfied
        assert not set(v.enforcement_backed_dimensions) & {"http", "https", "tcp"}

    def test_mixed_timeout_and_denied_is_satisfied_as_observed(self):
        def mixed(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            if port == 80:
                return (iso.OUTCOME_DENIED, "winerror=10013", "")
            return ("timeout", "timed out", "")

        cap = capture()
        v = iso.verify_isolation_attestation(backed(cap, probe=mixed),
                                            enforcement_capture=cap)
        assert v.satisfied, v.detail
        assert "http" in v.satisfied_dimensions
        assert "https" in v.enforcement_backed_dimensions

    def test_partial_coverage_leaves_the_dimension_unproven(self):
        """One covered target is not coverage for the whole dimension."""
        only_half = capture(rules=[
            {"direction": "outbound", "action": "deny", "remote_ip": "1.1.1.1",
             "protocol": "any", "weight": 900, "stateful": False},
        ])
        v = iso.verify_isolation_attestation(backed(only_half),
                                            enforcement_capture=only_half)
        assert not v.satisfied
        for dim in ("http", "https", "tcp", "udp"):
            assert dim not in v.enforcement_backed_dimensions


# --------------------------------------------------------- integrity failures
class TestCaptureIntegrity:
    def test_digest_mismatch_is_refused(self):
        cap = capture()
        other = capture(rules=S8_RULES + [
            {"direction": "outbound", "action": "allow", "remote_ip": "1.1.1.1",
             "protocol": "any", "weight": 999, "stateful": True}])
        att = backed(cap)
        v = iso.verify_isolation_attestation(att, enforcement_capture=other)
        assert not v.satisfied
        assert "enforcement_capture" in v.unproven_dimensions
        assert not v.enforcement_backed_dimensions

    def test_attestation_claims_nothing_and_capture_is_refused(self):
        cap = capture()
        v = iso.verify_isolation_attestation(build_att(), enforcement_capture=cap)
        assert not v.satisfied
        assert "enforcement_capture" in v.unproven_dimensions

    def test_unrecognised_scope_is_refused(self):
        cap = capture()
        att = build_att(enforcement_scope="windows_account",
                        enforcement_capture_sha256=iso.canonical_enforcement_digest(cap))
        v = iso.verify_isolation_attestation(att, enforcement_capture=cap)
        assert not v.satisfied
        assert "enforcement_capture" in v.unproven_dimensions

    def test_malformed_capture_is_refused_not_raised(self):
        """A bad capture must degrade to the strict contract, never crash the
        verifier and never satisfy anything."""
        att = backed(capture())
        v = iso.verify_isolation_attestation(att, enforcement_capture={"schema": "nope"})
        assert not v.satisfied
        assert "enforcement_capture" in v.unproven_dimensions

    def test_an_empty_rule_list_cannot_back(self):
        empty = capture(rules=[])
        att = build_att(enforcement_capture_sha256=iso.canonical_enforcement_digest(empty))
        v = iso.verify_isolation_attestation(att, enforcement_capture=empty)
        assert not v.satisfied

    def test_informative_extras_in_the_subject_do_not_break_the_claim(self):
        """Regression for a defect this change itself introduced: the verifier
        digested a NORMALISED capture, so a real read-back carrying extra
        subject fields (adapter name, switch) never matched its own claim and
        every legitimate capture was refused.  The digest must bind the capture
        EXACTLY as supplied."""
        enriched = capture()
        enriched["subject"] = {
            **enriched["subject"], "adapter": "Network Adapter", "switch": "F2-S8-NAT",
        }
        for rule in enriched["rules"]:
            rule.setdefault("local_ip", "ANY")
        att = backed(enriched)
        assert att.enforcement_capture_sha256 == \
            iso.canonical_enforcement_digest(enriched)
        v = iso.verify_isolation_attestation(att, enforcement_capture=enriched)
        assert v.satisfied, v.detail
        assert "https" in v.enforcement_backed_dimensions

    def test_bytes_capture_is_accepted_and_digested_identically(self):
        cap = capture()
        att = backed(cap)
        v = iso.verify_isolation_attestation(
            att, enforcement_capture=json.dumps(cap).encode("utf-8"))
        assert v.satisfied, v.detail
        assert v.enforcement_capture_digest == iso.canonical_enforcement_digest(cap)


# ------------------------------------------------------------- raw evidence
class TestRawOutcomeEvidence:
    def test_raw_outcome_distinguishes_the_four_observations(self):
        seen: dict[str, str] = {}

        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                seen["loopback"] = "connected"
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            seen[str(port)] = "timeout"
            return ("timeout", "timed out", "")

        att = build_att(probe=probe)
        raws = {p.dimension: p.raw_outcome for p in att.probes}
        assert raws["loopback"] == iso.OUTCOME_PERMITTED
        assert raws["https"] == "timeout"
        assert raws["http"] == "timeout"

    def test_raw_outcome_survives_render_and_parse(self):
        att = build_att()
        back = iso.parse_attestation(iso.render_attestation(att))
        assert [p.raw_outcome for p in back.probes] == \
            [p.raw_outcome for p in att.probes]
        assert back.enforcement_capture_sha256 == att.enforcement_capture_sha256

    def test_raw_outcome_does_not_change_the_strict_verdict(self):
        """Preserving the raw class must not, by itself, alter classification."""
        att = build_att()
        v = iso.verify_isolation_attestation(att)
        assert not v.satisfied
        assert "http" in v.unproven_dimensions

    def test_dns_failure_is_visible_as_its_own_raw_class(self):
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            return ("dns_unresolved", "name resolution failed", "")

        att = build_att(probe=probe)
        raws = {p.dimension: p.raw_outcome for p in att.probes}
        assert raws["https"] == "dns_unresolved"

    def test_unreachable_is_visible_and_not_backable(self):
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            return ("unreachable", "winerror=10051: no route", "")

        cap = capture()
        att = build_att(
            probe=probe,
            udp_probe=lambda h, p: ("unreachable", "winerror=10051: no route", ""),
            enforcement_capture_sha256=iso.canonical_enforcement_digest(cap),
        )
        v = iso.verify_isolation_attestation(att, enforcement_capture=cap)
        assert not v.enforcement_backed_dimensions
        assert "https" in v.unproven_dimensions
        assert "udp" in v.unproven_dimensions
        raws = {p.dimension: p.raw_outcome for p in att.probes}
        assert raws["https"] == "unreachable"


# ------------------------------------------------------------ projections
class TestProjectionAndSerialisation:
    def test_enforcement_backed_projects_to_denied_but_stays_named(self):
        cap = capture()
        v = iso.verify_isolation_attestation(backed(cap), enforcement_capture=cap)
        proj = v.as_readiness_mapping()
        for dim in ("http", "https", "tcp", "udp"):
            assert proj[dim] == iso.OUTCOME_DENIED
            assert dim in v.enforcement_backed_dimensions
        assert "http" not in v.satisfied_dimensions

    def test_verdict_round_trips_through_json(self):
        cap = capture()
        v = iso.verify_isolation_attestation(backed(cap), enforcement_capture=cap)
        again = iso.VerificationVerdict(**json.loads(json.dumps(v.to_dict())))
        assert tuple(again.enforcement_backed_dimensions) == v.enforcement_backed_dimensions
        assert again.satisfied is True

    def test_attestation_json_carries_the_claim(self):
        cap = capture()
        payload = json.loads(iso.render_attestation(backed(cap)).decode("utf-8"))
        assert payload["enforcement_capture_sha256"] == \
            iso.canonical_enforcement_digest(cap)
        assert all("raw_outcome" in p for p in payload["probes"])
