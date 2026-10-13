"""Adversarial tests for the Option C host-drop attribution gate.

The gate is OFF by default (baseline), must only activate on an explicit
``acceptance=OPTION_C_ACCEPTANCE_VERSION``, and must fail closed against every
kind of bad evidence: generic drops, wrong event/component/switch, flow and
direction mismatches, bad digests, stale/out-of-window captures, run-binding
mismatches, ambiguity, malformed records and permitted observations.
"""

from __future__ import annotations

import pytest

from qwen_train import f2_host_attribution as ha
from qwen_train import f2_isolation as iso

_EXTERNAL = (
    ("93.184.216.34", 80),
    ("93.184.216.34", 443),
    ("93.184.216.34", 8080),
)


def _probe(protocol, host, port):
    if str(host) == "127.0.0.1":
        return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
    if str(host) == "2606:4700:4700::1111":
        return (iso.OUTCOME_DENIED, "refused", "")
    return ("timeout", "silently dropped", "")


def _att(**over):
    kwargs = dict(
        probe=_probe,
        external_targets=_EXTERNAL,
        required_services=(("backend", "127.0.0.1", 8000),),
        udp_probe=lambda h, p: (iso.OUTCOME_DENIED, "winerror=10013", ""),
        dns_probe=lambda: (iso.OUTCOME_DENIED, "resolver refused"),
        ipv6_available=lambda: True,
        interface_inventory=("Loopback Pseudo-Interface 1", "Wi-Fi"),
        policy_identity="vmswitch-extended-acl",
        policy_sha256="ab" * 32,
        policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
        arm_id="arm-1",
        rollout_id="rollout-1",
        workspace="C:/arms/1",
        enforcement_scope="vm",
        executing_user="F2VM\\operator",
        interpreter_path=r"C:\f2\python.exe",
        interpreter_sha256="cd" * 32,
    )
    kwargs.update(over)
    return iso.run_egress_probes(**kwargs)


def _window(att, *, before=30.0, after=1.0):
    ats = [p.at for p in att.probes]
    return min(ats) - before, max(ats) + after


def _now(att):
    return max(p.at for p in att.probes) + 1.0


def _record(att, dim, **over):
    probe = next(p for p in att.probes if p.dimension == dim)
    host, port = probe.target.rsplit(":", 1)
    ws, we = _window(att)
    rec = dict(
        source=ha.VMSWITCH_PROVIDER,
        event_id=ha.VMSWITCH_ACL_DROP_EVENT_ID,
        channel=ha.VMSWITCH_ACL_DROP_CHANNEL,
        vm_name="F2-Isolation-VM",
        switch_name=ha.DEFAULT_F2_SWITCH_NAME,
        component="vmswitch.sys",
        direction="egress",
        protocol=probe.protocol,
        ip_src="10.72.0.2",
        ip_dst=host,
        port_dst=int(port),
        acl_specificity=ha.ACL_SPECIFICITY_EXTENDED,
        acl_entry="42",
        acl_weight="7",
        drop_reason="Failed Security Policy",
        pkt_group_id="1234",
        capture_artifact_sha256="ef" * 32,
        capture_window_start=ws,
        capture_window_stop=we,
        probe_at=probe.at,
        run_arm_id=att.arm_id,
        run_rollout_id=att.rollout_id,
    )
    rec.update(over)
    return rec


def _option_c(att, records):
    return ha.apply_host_attribution(
        att, records, acceptance=ha.OPTION_C_ACCEPTANCE_VERSION, now=_now(att)
    )


def test_baseline_is_default_and_ignores_records():
    att = _att()
    baseline = iso.verify_isolation_attestation(att)
    assert baseline.satisfied is False

    v = ha.apply_host_attribution(
        att, [_record(att, d) for d in ("http", "https", "tcp")], now=_now(att)
    )
    assert v.acceptance_version == ha.OPTION_C_BASELINE
    assert v.attributed_dimensions == ()
    assert v.satisfied is False
    assert v.as_readiness_mapping() == baseline.as_readiness_mapping()


def test_option_c_qualifying_records_satisfy():
    att = _att()
    records = [_record(att, d) for d in ("http", "https", "tcp")]
    v = _option_c(att, records)
    assert v.satisfied is True
    assert set(v.attributed_dimensions) == {"http", "https", "tcp"}
    assert v.unproven_dimensions == ()
    mapping = v.as_readiness_mapping()
    assert mapping["http"] == iso.OUTCOME_DENIED
    assert mapping["udp"] == iso.OUTCOME_DENIED


def test_option_c_without_records_fails_closed():
    att = _att()
    v = _option_c(att, [])
    assert v.satisfied is False
    assert v.attributed_dimensions == ()


@pytest.mark.parametrize(
    "over, needle",
    [
        ({"acl_specificity": ha.ACL_SPECIFICITY_GENERIC}, "acl-specific"),
        ({"event_id": 36}, "event"),
        ({"acl_entry": ""}, "acl entry"),
        ({"acl_weight": ""}, "acl weight"),
        ({"switch_name": "Default Switch"}, "switch"),
        ({"component": "tcpip.sys"}, "component"),
        ({"direction": "ingress"}, "direction"),
        ({"port_dst": 9999}, "flow mismatch"),
        ({"protocol": "udp"}, "protocol mismatch"),
        ({"capture_artifact_sha256": "not-a-digest"}, "sha-256"),
        ({"vm_name": ""}, "missing field"),
    ],
)
def test_bad_record_is_rejected_and_not_attributed(over, needle):
    att = _att()
    bad = _record(att, "http", **over)
    v = _option_c(att, [bad, _record(att, "https"), _record(att, "tcp")])
    assert "http" in v.unproven_dimensions
    assert "http" not in v.attributed_dimensions
    assert v.satisfied is False
    reasons = " ".join(r for _d, r in v.rejected).lower()
    assert needle.lower() in reasons


def test_stale_capture_is_rejected():
    att = _att()
    v = ha.apply_host_attribution(
        att,
        [_record(att, d) for d in ("http", "https", "tcp")],
        acceptance=ha.OPTION_C_ACCEPTANCE_VERSION,
        now=_now(att) + 10**6,
    )
    assert v.satisfied is False
    assert any("stale" in r for _d, r in v.rejected)


def test_probe_outside_capture_window_is_rejected():
    att = _att()
    probe = next(p for p in att.probes if p.dimension == "http")
    bad = _record(
        att,
        "http",
        capture_window_start=probe.at + 100,
        capture_window_stop=probe.at + 200,
        probe_at=probe.at + 150,
    )
    v = _option_c(att, [bad, _record(att, "https"), _record(att, "tcp")])
    assert "http" in v.unproven_dimensions
    assert any("outside the capture window" in r for _d, r in v.rejected)


def test_run_binding_mismatch_is_rejected():
    att = _att()
    bad = _record(att, "http", run_rollout_id="someone-elses-rollout")
    v = _option_c(att, [bad, _record(att, "https"), _record(att, "tcp")])
    assert "http" in v.unproven_dimensions
    assert any("not bound to this arm" in r for _d, r in v.rejected)


def test_ambiguous_conflicting_records_are_rejected():
    att = _att()
    r1 = _record(att, "http", acl_entry="42")
    r2 = _record(att, "http", acl_entry="43")  # distinct, both otherwise qualify
    v = _option_c(att, [r1, r2, _record(att, "https"), _record(att, "tcp")])
    assert "http" in v.unproven_dimensions
    assert any("ambiguous" in r for _d, r in v.rejected)


def test_identical_duplicate_record_is_not_ambiguous():
    att = _att()
    records = [
        _record(att, "http"),
        _record(att, "http"),
        _record(att, "https"),
        _record(att, "tcp"),
    ]
    v = _option_c(att, records)
    assert set(v.attributed_dimensions) == {"http", "https", "tcp"}


def test_permitted_observation_is_never_overridden_by_a_drop_record():
    def probe(protocol, host, port):
        if str(host) == "127.0.0.1":
            return (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1")
        if str(host) == "2606:4700:4700::1111":
            return (iso.OUTCOME_DENIED, "refused", "")
        if int(port) == 80:
            return (iso.OUTCOME_PERMITTED, "reachable", "")
        return ("timeout", "dropped", "")

    att = _att(probe=probe)
    assert "http" in iso.verify_isolation_attestation(att).failed_dimensions
    v = _option_c(att, [_record(att, "http"), _record(att, "https"), _record(att, "tcp")])
    assert v.satisfied is False
    assert "http" not in v.attributed_dimensions


def test_malformed_record_mapping_does_not_crash():
    att = _att()
    v = _option_c(att, [{"event_id": "not-an-int"}, _record(att, "https"), _record(att, "tcp")])
    assert v.satisfied is False
    assert any("malformed" in r for _d, r in v.rejected)


def test_serialization_round_trip_and_digest_sensitivity():
    att = _att()
    rec = ha.HostDropRecord(**_record(att, "http"))
    payload = rec.to_dict()
    assert ha.HostDropRecord(**payload).digest() == rec.digest()
    assert rec.digest() == rec.digest()
    changed = dict(payload)
    changed["acl_entry"] = "99"
    assert ha.HostDropRecord(**changed).digest() != rec.digest()

    verdict = _option_c(att, [rec, _record(att, "https"), _record(att, "tcp")])
    assert isinstance(verdict.to_dict()["baseline"], dict)
    assert verdict.to_dict()["acceptance_version"] == ha.OPTION_C_ACCEPTANCE_VERSION


@pytest.mark.parametrize("acceptance", ["", "option-c/2", "OPTION-C-1", "bogus", None])
def test_unknown_acceptance_version_fails_closed(acceptance):
    """Anything other than the explicit option-c/1 string stays on the baseline:
    the records are ignored and no dimension is attributed."""
    att = _att()
    v = ha.apply_host_attribution(
        att,
        [_record(att, d) for d in ("http", "https", "tcp")],
        acceptance=acceptance,
        now=_now(att),
    )
    assert v.acceptance_version == ha.OPTION_C_BASELINE
    assert v.attributed_dimensions == ()
    assert v.satisfied is False


def test_serialized_payload_cannot_activate_option_c():
    """Activation is an explicit function argument, never a field. A serialized
    attestation that smuggles an `acceptance` key or a `host_drop_attribution`
    list is parsed with those keys ignored, and the default stays baseline."""
    att = _att()
    blob = att.to_dict()
    blob["acceptance"] = ha.OPTION_C_ACCEPTANCE_VERSION
    blob["host_drop_attribution"] = [_record(att, "http")]
    v = ha.apply_host_attribution(
        blob,
        [_record(att, d) for d in ("http", "https", "tcp")],
        now=_now(att),
    )
    assert v.acceptance_version == ha.OPTION_C_BASELINE
    assert v.attributed_dimensions == ()
    assert v.satisfied is False


def test_provenance_is_shape_only_documented_trust_gap():
    """KNOWN TRUST GAP (documented, not fixed here): the gate validates a record's
    SHAPE, not its ORIGIN. No artifact bytes are read or parsed, so a caller can
    fabricate every field -- including the artifact digest and the ACL entry --
    and be accepted. This pins that behaviour so a future provenance fix is a
    deliberate, separately-authorized change."""
    att = _att()
    fabricated = [
        _record(
            att,
            d,
            capture_artifact_sha256="de" * 32,
            acl_entry="FABRICATED",
            acl_weight="FABRICATED",
            pkt_group_id="FAB",
        )
        for d in ("http", "https", "tcp")
    ]
    v = _option_c(att, fabricated)
    assert v.satisfied is True
    assert set(v.attributed_dimensions) == {"http", "https", "tcp"}
