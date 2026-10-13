"""Option C gate tests driven through the trusted ingestion pipeline.

The gate now accepts only sealed ``AcceptedAttribution`` tokens (no raw records), so
these tests build SYNTHETIC artifacts, run them through the real parser/trust/
correlation pipeline, and assert the gate stays inert by default and fails closed.
"""

from __future__ import annotations

import json

import pytest

from qwen_train import f2_evidence_ingestion as ing
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


def _artifact(schema, event, path):
    doc = {"schema": schema, "source": {"producer": "SYNTHETIC"}, "events": [event]}
    return ing.OriginalArtifact.from_bytes(json.dumps(doc).encode(), path=path, producer_hint="SYNTHETIC")


def _acl_artifact(ts, **over):
    ev = {
        "kind": "vmswitch_acl_drop",
        "provider": "Microsoft-Windows-Hyper-V-VmSwitch",
        "event_id": 131,
        "channel": "Microsoft-Windows-Hyper-V-VmSwitch-Diagnostic-Traffic",
        "vm_name": "F2-Isolation-VM",
        "port_name": "SyntheticPort",
        "port_fname": "Network Adapter",
        "switch_name": "F2-Internal-Switch",
        "switch_fname": "F2-Internal-Switch",
        "direction": "egress",
        "weight": 1,
        "timestamp": ts,
    }
    ev.update(over)
    return _artifact(ing.SCHEMA_VMSWITCH_ACL_DROP, ev, "synthetic://acl")


def _flow_artifact(ts, target, protocol="tcp", **over):
    host, port = target.rsplit(":", 1)
    ev = {
        "kind": "pktmon_drop",
        "component": "vmswitch.sys",
        "vm_name": "F2-Isolation-VM",
        "switch_name": "F2-Internal-Switch",
        "direction": "egress",
        "protocol": protocol,
        "ip_src": "10.72.0.2",
        "ip_dst": host,
        "port_dst": int(port),
        "drop_reason": "Failed security policy",
        "pkt_group_id": "1234",
        "timestamp": ts,
    }
    ev.update(over)
    return _artifact(ing.SCHEMA_PKTMON_DROP, ev, "synthetic://flow")


class _Trust:
    def __init__(self, authenticated=True, producer="synthetic-owner-key"):
        self._a, self._p = authenticated, producer

    def verify(self, artifact):
        return ing.ProducerVerdict(self._a, self._p if self._a else "", "synthetic")


def _rule(authorized=True):
    return ing.CorrelationRule(authorized=authorized, expected_acl_weight=1, window_tolerance_seconds=5.0)


def _obs(*artifacts):
    out = []
    for a in artifacts:
        out.extend(ing.observations_from(ing.parse_artifact(a)))
    return tuple(out)


def _accepted(att, dim, *, policy=None, rule=None, target=None, acl_over=None, flow_over=None, run_arm=None, run_rollout=None):
    probe = next(p for p in att.probes if p.dimension == dim)
    tgt = target or probe.target
    acl = _acl_artifact(probe.at, **(acl_over or {}))
    flow = _flow_artifact(probe.at, tgt, **(flow_over or {}))
    return ing.accept_attribution(
        dimension=dim,
        probe_protocol=probe.protocol,
        probe_target=tgt,
        probe_at=probe.at,
        acl_observations=_obs(acl),
        flow_observations=_obs(flow),
        artifacts=[acl, flow],
        rule=rule if rule is not None else _rule(),
        trust_policy=policy if policy is not None else _Trust(),
        run_arm_id=run_arm if run_arm is not None else att.arm_id,
        run_rollout_id=run_rollout if run_rollout is not None else att.rollout_id,
    )


def _token(att, dim, **kw):
    out = _accepted(att, dim, **kw)
    assert out.status == ing.STATUS_ACCEPTED_ATTRIBUTION, out.detail
    return out.attribution


# ---------------------------------------------------------------- gate
def test_default_baseline_ignores_tokens():
    att = _att()
    tokens = [_token(att, d) for d in ("http", "https", "tcp")]
    baseline = iso.verify_isolation_attestation(att)
    v = ha.apply_host_attribution(att, tokens)
    assert v.acceptance_version == ha.OPTION_C_BASELINE
    assert v.attributed_dimensions == ()
    assert v.satisfied is False
    assert v.as_readiness_mapping() == baseline.as_readiness_mapping()


def test_option_c_with_accepted_tokens_satisfies():
    att = _att()
    tokens = [_token(att, d) for d in ("http", "https", "tcp")]
    v = ha.apply_host_attribution(att, tokens, acceptance=ha.OPTION_C_ACCEPTANCE_VERSION)
    assert v.satisfied is True
    assert set(v.attributed_dimensions) == {"http", "https", "tcp"}
    assert v.unproven_dimensions == ()


def test_no_trust_anchor_means_no_attribution():
    att = _att()
    out = _accepted(att, "http", policy=ing.NullTrustPolicy())
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None
    v = ha.apply_host_attribution(att, [], acceptance=ha.OPTION_C_ACCEPTANCE_VERSION)
    assert v.satisfied is False


def test_token_bound_to_another_run_is_rejected():
    att = _att()
    bad = _token(att, "http", run_rollout="someone-else")
    good = [_token(att, "https"), _token(att, "tcp")]
    v = ha.apply_host_attribution(att, [bad, *good], acceptance=ha.OPTION_C_ACCEPTANCE_VERSION)
    assert "http" in v.unproven_dimensions
    assert v.satisfied is False
    assert any("not bound to this arm" in r for _d, r in v.rejected)


def test_ambiguous_tokens_for_one_dimension_are_rejected():
    att = _att()
    a = _token(att, "http")
    c = _token(att, "http", acl_over={"port_name": "OtherPort"})  # distinct ACL artifact digest
    v = ha.apply_host_attribution(att, [a, c], acceptance=ha.OPTION_C_ACCEPTANCE_VERSION)
    assert "http" in v.unproven_dimensions
    assert any("ambiguous" in r for _d, r in v.rejected)


def test_permitted_observation_is_never_overridden():
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
    tokens = [_token(att, d) for d in ("http", "https", "tcp")]
    v = ha.apply_host_attribution(att, tokens, acceptance=ha.OPTION_C_ACCEPTANCE_VERSION)
    assert v.satisfied is False
    assert "http" not in v.attributed_dimensions


@pytest.mark.parametrize("acceptance", ["", "option-c/2", "OPTION-C-1", "bogus", None])
def test_unknown_acceptance_version_fails_closed(acceptance):
    att = _att()
    tokens = [_token(att, d) for d in ("http", "https", "tcp")]
    v = ha.apply_host_attribution(att, tokens, acceptance=acceptance)
    assert v.acceptance_version == ha.OPTION_C_BASELINE
    assert v.attributed_dimensions == ()
    assert v.satisfied is False


def test_serialized_payload_cannot_activate_option_c():
    att = _att()
    blob = att.to_dict()
    blob["acceptance"] = ha.OPTION_C_ACCEPTANCE_VERSION
    blob["host_drop_attribution"] = [_token(att, "http").__dict__]
    v = ha.apply_host_attribution(blob, [_token(att, d) for d in ("http", "https", "tcp")])
    assert v.acceptance_version == ha.OPTION_C_BASELINE
    assert v.satisfied is False


def test_raw_record_cannot_be_smuggled_as_a_token():
    """Passing a plain mapping where an AcceptedAttribution is expected must not
    be accepted (and must not silently count as attribution)."""
    att = _att()
    fake = {"dimension": "http", "artifact_sha256": "de" * 32, "flow": ("93.184.216.34", "tcp", 80)}
    with pytest.raises(AttributeError):
        ha.apply_host_attribution(att, [fake], acceptance=ha.OPTION_C_ACCEPTANCE_VERSION)
