"""Adversarial tests for the trusted evidence-ingestion pipeline.

All fixtures are SYNTHETIC (built from JSON bytes in-process). They are not live
experimental evidence. The tests prove the pipeline parses real bytes, binds records
to the computed artifact digest, seals its trusted types, correlates two sources, and
refuses to accept anything without an authorized rule and an authenticated producer.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from qwen_train import f2_evidence_ingestion as ing


def _vmswitch_artifact(**over):
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
        "timestamp": 1000.0,
    }
    ev.update(over)
    doc = {"schema": ing.SCHEMA_VMSWITCH_ACL_DROP, "source": {"producer": "SYNTHETIC"}, "events": [ev]}
    return ing.OriginalArtifact.from_bytes(
        json.dumps(doc).encode(), path="synthetic://vmswitch", producer_hint="SYNTHETIC"
    )


def _pktmon_artifact(**over):
    ev = {
        "kind": "pktmon_drop",
        "component": "vmswitch.sys",
        "vm_name": "F2-Isolation-VM",
        "switch_name": "F2-Internal-Switch",
        "direction": "egress",
        "protocol": "tcp",
        "ip_src": "10.72.0.2",
        "ip_dst": "93.184.216.34",
        "port_dst": 80,
        "drop_reason": "Failed security policy",
        "pkt_group_id": "1234",
        "timestamp": 1000.0,
    }
    ev.update(over)
    doc = {"schema": ing.SCHEMA_PKTMON_DROP, "source": {"producer": "SYNTHETIC"}, "events": [ev]}
    return ing.OriginalArtifact.from_bytes(
        json.dumps(doc).encode(), path="synthetic://pktmon", producer_hint="SYNTHETIC"
    )


class _FakeTrust:
    def __init__(self, authenticated=True, producer="synthetic-test"):
        self._authenticated = authenticated
        self._producer = producer

    def verify(self, artifact):
        return ing.ProducerVerdict(self._authenticated, self._producer if self._authenticated else "", "synthetic")


def _rule(authorized=True, **over):
    kw = dict(authorized=authorized, expected_acl_weight=1, window_tolerance_seconds=5.0)
    kw.update(over)
    return ing.CorrelationRule(**kw)


# ---------------------------------------------------------------- parsing
def test_parser_ok_and_digest_is_computed_from_bytes():
    art = _vmswitch_artifact()
    outcome = ing.parse_artifact(art)
    assert outcome.status == "OK"
    assert len(outcome.events) == 1
    assert outcome.events[0].artifact_sha256 == hashlib.sha256(art.data).hexdigest()
    assert art.sha256 == hashlib.sha256(art.data).hexdigest()


def test_parser_rejects_malformed_json():
    art = ing.OriginalArtifact.from_bytes(b"{not json")
    assert ing.parse_artifact(art).status == ing.STATUS_MALFORMED


def test_parser_rejects_truncated_bytes():
    art = ing.OriginalArtifact.from_bytes(b'{"schema":"vmswitch-acl-drop/1","events":[{"kind"')
    assert ing.parse_artifact(art).status == ing.STATUS_MALFORMED


def test_parser_rejects_unsupported_schema():
    art = ing.OriginalArtifact.from_bytes(b'{"schema":"nope/9","events":[{}]}')
    assert ing.parse_artifact(art).status == ing.STATUS_UNSUPPORTED


def test_parser_rejects_missing_required_field():
    doc = {"schema": ing.SCHEMA_VMSWITCH_ACL_DROP, "events": [{"kind": "vmswitch_acl_drop", "provider": "x"}]}
    art = ing.OriginalArtifact.from_bytes(json.dumps(doc).encode())
    assert ing.parse_artifact(art).status == ing.STATUS_MALFORMED


def test_parser_rejects_wrong_kind_for_schema():
    doc = {"schema": ing.SCHEMA_PKTMON_DROP, "events": [{"kind": "vmswitch_acl_drop"}]}
    art = ing.OriginalArtifact.from_bytes(json.dumps(doc).encode())
    assert ing.parse_artifact(art).status == ing.STATUS_MALFORMED


def test_parser_rejects_empty_events():
    doc = {"schema": ing.SCHEMA_VMSWITCH_ACL_DROP, "events": []}
    art = ing.OriginalArtifact.from_bytes(json.dumps(doc).encode())
    assert ing.parse_artifact(art).status == ing.STATUS_MALFORMED


# ---------------------------------------------------------------- sealing
def test_sealed_types_reject_direct_construction():
    with pytest.raises(PermissionError):
        ing.ParsedEvent(schema="s", kind="k", artifact_sha256="a", artifact_path="", fields={})
    with pytest.raises(PermissionError):
        ing.Observation(kind="k", artifact_sha256="a", fields={})
    with pytest.raises(PermissionError):
        ing.CorrelationCandidate(
            dimension="http", artifact_sha256="a", flow_artifact_sha256="b", switch_name="s",
            port_name="p", weight=1, flow=("1.1.1.1", "tcp", 80), window_start=0.0, window_stop=1.0,
        )
    with pytest.raises(PermissionError):
        ing.AcceptedAttribution(
            dimension="http", artifact_sha256="a", flow_artifact_sha256="b", switch_name="s",
            port_name="p", weight=1, flow=("1.1.1.1", "tcp", 80), window_start=0.0, window_stop=1.0,
            run_arm_id="a", run_rollout_id="r", rule_version="v", producer="p",
        )


# ---------------------------------------------------------------- correlation
def _obs(*artifacts):
    out = []
    for a in artifacts:
        out.extend(ing.observations_from(ing.parse_artifact(a)))
    return tuple(out)


def test_correlate_unique_match_is_a_candidate_not_accepted():
    acl = _obs(_vmswitch_artifact(timestamp=1000.0))
    flow = _obs(_pktmon_artifact(timestamp=1001.0))
    out = ing.correlate(
        dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
        probe_at=1000.5, acl_observations=acl, flow_observations=flow, rule=_rule(),
    )
    assert out.status == ing.STATUS_CORRELATED_CANDIDATE
    assert out.candidate is not None


def test_correlate_no_flow_is_no_match():
    acl = _obs(_vmswitch_artifact())
    out = ing.correlate(
        dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
        probe_at=1000.0, acl_observations=acl, rule=_rule(),
    )
    assert out.status == ing.STATUS_NO_MATCH


def test_correlate_two_flow_records_is_ambiguous():
    acl = _obs(_vmswitch_artifact())
    flow = _obs(_pktmon_artifact(timestamp=1000.0), _pktmon_artifact(timestamp=1002.0))
    out = ing.correlate(
        dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
        probe_at=1000.5, acl_observations=acl, flow_observations=flow, rule=_rule(),
    )
    assert out.status == ing.STATUS_AMBIGUOUS


@pytest.mark.parametrize(
    "flow_over, acl_over",
    [
        ({"ip_dst": "1.2.3.4"}, {}),               # wrong tuple
        ({"port_dst": 443}, {}),                   # wrong port
        ({"protocol": "udp"}, {}),                 # wrong protocol
        ({"component": "tcpip.sys"}, {}),          # wrong component
        ({"switch_name": "Other"}, {}),            # wrong switch
        ({"direction": "ingress"}, {}),            # wrong direction
        ({"timestamp": 99999.0}, {}),              # outside window
        ({}, {"weight": 100}),                     # wrong ACL weight
        ({}, {"switch_name": "Other"}),            # wrong ACL switch
        ({}, {"direction": "in"}),                 # wrong ACL direction
        ({}, {"timestamp": 99999.0}),              # ACL outside window
    ],
)
def test_correlate_rejects_mismatches(flow_over, acl_over):
    acl = _obs(_vmswitch_artifact(**acl_over))
    flow = _obs(_pktmon_artifact(**flow_over))
    out = ing.correlate(
        dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
        probe_at=1000.5, acl_observations=acl, flow_observations=flow, rule=_rule(),
    )
    assert out.status == ing.STATUS_NO_MATCH


# ---------------------------------------------------------------- acceptance
def _accept(policy, rule, *, supply_artifacts=True):
    acl = _vmswitch_artifact(timestamp=1000.0)
    flow = _pktmon_artifact(timestamp=1001.0)
    return ing.accept_attribution(
        dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
        probe_at=1000.5,
        acl_observations=_obs(acl),
        flow_observations=_obs(flow),
        artifacts=[acl, flow] if supply_artifacts else [],
        rule=rule, trust_policy=policy, run_arm_id="arm-1", run_rollout_id="roll-1",
    )


def test_accept_requires_authorized_rule():
    out = _accept(_FakeTrust(True), _rule(authorized=False))
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None


def test_accept_requires_authenticated_producer():
    out = _accept(_FakeTrust(False), _rule(authorized=True))
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None


def test_accept_requires_artifacts_to_authenticate():
    out = _accept(_FakeTrust(True), _rule(authorized=True), supply_artifacts=False)
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None


def test_null_trust_policy_never_authenticates():
    art = _vmswitch_artifact()
    assert ing.NullTrustPolicy().verify(art).authenticated is False


def test_accept_succeeds_only_with_authorized_rule_and_authenticated_producer():
    out = _accept(_FakeTrust(True, "owner-key-1"), _rule(authorized=True))
    assert out.status == ing.STATUS_ACCEPTED_ATTRIBUTION
    acc = out.attribution
    assert acc is not None
    assert acc.artifact_sha256 == hashlib.sha256(_vmswitch_artifact(timestamp=1000.0).data).hexdigest()
    assert acc.flow_artifact_sha256 == hashlib.sha256(_pktmon_artifact(timestamp=1001.0).data).hexdigest()
    assert acc.dimension == "http"
    assert acc.run_arm_id == "arm-1" and acc.producer == "owner-key-1"


def test_accept_ambiguous_does_not_accept():
    acl = _vmswitch_artifact()
    out = ing.accept_attribution(
        dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
        probe_at=1000.5,
        acl_observations=_obs(acl),
        flow_observations=_obs(_pktmon_artifact(timestamp=1000.0), _pktmon_artifact(timestamp=1002.0)),
        artifacts=[acl],
        rule=_rule(authorized=True), trust_policy=_FakeTrust(True),
        run_arm_id="arm-1", run_rollout_id="roll-1",
    )
    assert out.status == ing.STATUS_AMBIGUOUS
    assert out.attribution is None


# ---------------------------------------------------------------- security gate
def _attempt(*, acl_observations, flow_observations, artifacts, policy=None, rule=None):
    return ing.accept_attribution(
        dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
        probe_at=1000.5,
        acl_observations=acl_observations,
        flow_observations=flow_observations,
        artifacts=artifacts,
        rule=rule if rule is not None else _rule(authorized=True),
        trust_policy=policy,
        run_arm_id="arm-1", run_rollout_id="roll-1",
    )


class _SelectiveTrust:
    def __init__(self, allow):
        self.allow = allow

    def verify(self, art):
        ok = art.sha256 in self.allow
        return ing.ProducerVerdict(ok, "p1" if ok else "", "selective")


class _PathProducerTrust:
    def verify(self, art):
        return ing.ProducerVerdict(True, art.source.path, "per-path")


def test_fabricated_verdict_cannot_authorize():
    acl, flow = _vmswitch_artifact(), _pktmon_artifact()
    with pytest.raises(TypeError):
        ing.accept_attribution(
            dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
            probe_at=1000.5, acl_observations=_obs(acl), flow_observations=_obs(flow),
            artifacts=[acl, flow], rule=_rule(), trust=ing.ProducerVerdict(True, "forged"),
        )
    # A bare verdict is not a policy (it has no .verify), so it cannot authenticate.
    with pytest.raises(AttributeError):
        ing.accept_attribution(
            dimension="http", probe_protocol="tcp", probe_target="93.184.216.34:80",
            probe_at=1000.5, acl_observations=_obs(acl), flow_observations=_obs(flow),
            artifacts=[acl, flow], rule=_rule(), trust_policy=ing.ProducerVerdict(True, "forged"),
        )


def test_policy_authenticating_only_one_artifact_is_rejected():
    acl, flow = _vmswitch_artifact(), _pktmon_artifact()
    out = _attempt(
        acl_observations=_obs(acl), flow_observations=_obs(flow),
        artifacts=[acl, flow], policy=_SelectiveTrust({acl.sha256}),
    )
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None


def test_different_producers_are_rejected():
    acl, flow = _vmswitch_artifact(), _pktmon_artifact()
    out = _attempt(
        acl_observations=_obs(acl), flow_observations=_obs(flow),
        artifacts=[acl, flow], policy=_PathProducerTrust(),
    )
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None


def test_observation_from_other_artifact_is_rejected():
    acl = _vmswitch_artifact()
    other = _vmswitch_artifact(port_name="OtherPort")
    flow = _pktmon_artifact()
    out = _attempt(
        acl_observations=_obs(acl), flow_observations=_obs(flow),
        artifacts=[other, flow], policy=_FakeTrust(True),
    )
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None


def test_tampered_bytes_are_rejected():
    acl, flow = _vmswitch_artifact(), _pktmon_artifact()
    tampered = ing.OriginalArtifact.from_bytes(acl.data + b" ", path=acl.source.path)
    out = _attempt(
        acl_observations=_obs(acl), flow_observations=_obs(flow),
        artifacts=[tampered, flow], policy=_FakeTrust(True),
    )
    assert out.status == ing.STATUS_UNTRUSTED
    assert out.attribution is None


def test_ambiguous_event131_never_establishes_attribution():
    acl1 = _vmswitch_artifact()
    acl2 = _vmswitch_artifact(port_name="OtherPort")
    flow = _pktmon_artifact()
    out = _attempt(
        acl_observations=_obs(acl1, acl2), flow_observations=_obs(flow),
        artifacts=[acl1, acl2, flow], policy=_FakeTrust(True),
    )
    assert out.status == ing.STATUS_AMBIGUOUS
    assert out.attribution is None


def test_default_policy_and_default_rule_fail_closed():
    acl, flow = _vmswitch_artifact(), _pktmon_artifact()
    # default trust policy (NullTrustPolicy) -> UNTRUSTED
    out = _attempt(acl_observations=_obs(acl), flow_observations=_obs(flow), artifacts=[acl, flow])
    assert out.status == ing.STATUS_UNTRUSTED and out.attribution is None
    # default (unauthorized) rule -> UNTRUSTED even with an authenticated producer
    out2 = _attempt(
        acl_observations=_obs(acl), flow_observations=_obs(flow),
        artifacts=[acl, flow], policy=_FakeTrust(True), rule=ing.CorrelationRule(),
    )
    assert out2.status == ing.STATUS_UNTRUSTED and out2.attribution is None
