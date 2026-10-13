"""Trusted, fail-closed ingestion and two-source correlation of host Q9 evidence.

Offline only. This module defines the trust boundary between raw artifact bytes and
an accepted host-drop attribution, so that a caller **cannot** manufacture accepted
evidence by constructing a dataclass or a JSON mapping:

    OriginalArtifact  (raw bytes; digest COMPUTED from the bytes)
        -> ParsedEvent        (only produced by parse_artifact)
        -> Observation        (validated, bound to the artifact digest)
        -> CorrelationCandidate (only produced by correlate)
        -> AcceptedAttribution (only produced by accept_attribution)

Sealed types raise ``PermissionError`` if constructed with anything but the
module-private ``_SEAL``. A digest proves bytes match a digest; it does **not**
prove who produced the bytes. Producer authenticity is a separate, owner-controlled
trust anchor (see ``TrustPolicy``); with no anchor configured the pipeline returns
``UNTRUSTED`` and accepts nothing.

Nothing here activates Option C or changes the frozen acceptance rule. Acceptance
additionally requires a ``CorrelationRule`` with ``authorized=True`` and (by
default) an authenticated producer; until an owner provisions both, the gate stays
inert and fails closed.

Documented limitation (Event 131): its manifest template carries only
PortName/PortFName, SwitchName/SwitchFName, Direction and Weight -- no addresses,
ports, protocol, packet id, or ACL-entry field. It therefore can never identify the
destination flow; only the flow-carrying pktmon source can, and it reports a generic
security-policy reason. Correlation is not causation.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

__all__ = [
    "SCHEMA_VMSWITCH_ACL_DROP",
    "SCHEMA_PKTMON_DROP",
    "SUPPORTED_SCHEMAS",
    "STATUS_UNTRUSTED",
    "STATUS_MALFORMED",
    "STATUS_UNSUPPORTED",
    "STATUS_NO_MATCH",
    "STATUS_AMBIGUOUS",
    "STATUS_CORRELATED_CANDIDATE",
    "STATUS_ACCEPTED_ATTRIBUTION",
    "SourceMeta",
    "OriginalArtifact",
    "ProducerVerdict",
    "TrustPolicy",
    "NullTrustPolicy",
    "ParsedEvent",
    "ParseOutcome",
    "parse_artifact",
    "Observation",
    "observations_from",
    "CorrelationRule",
    "CorrelationCandidate",
    "CorrelationOutcome",
    "correlate",
    "AcceptOutcome",
    "AcceptedAttribution",
    "accept_attribution",
]

SCHEMA_VMSWITCH_ACL_DROP = "vmswitch-acl-drop/1"
SCHEMA_PKTMON_DROP = "pktmon-drop/1"
SUPPORTED_SCHEMAS = frozenset({SCHEMA_VMSWITCH_ACL_DROP, SCHEMA_PKTMON_DROP})

STATUS_UNTRUSTED = "UNTRUSTED"
STATUS_MALFORMED = "MALFORMED"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_NO_MATCH = "NO_MATCH"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_CORRELATED_CANDIDATE = "CORRELATED_CANDIDATE"
STATUS_ACCEPTED_ATTRIBUTION = "ACCEPTED_ATTRIBUTION"

#: The only object that may seal a trusted-pipeline type. Not exported.
_SEAL = object()

_KIND_VMSWITCH = "vmswitch_acl_drop"
_KIND_PKTMON = "pktmon_drop"

_REQUIRED_BY_KIND = {
    _KIND_VMSWITCH: (
        "provider", "event_id", "channel", "vm_name", "port_name", "port_fname",
        "switch_name", "switch_fname", "direction", "weight", "timestamp",
    ),
    _KIND_PKTMON: (
        "component", "vm_name", "switch_name", "direction", "protocol", "ip_src",
        "ip_dst", "port_dst", "drop_reason", "pkt_group_id", "timestamp",
    ),
}


@dataclass(frozen=True)
class SourceMeta:
    """Where the bytes came from. Self-reported; NOT authentication."""

    path: str = ""
    producer_hint: str = ""
    size: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class OriginalArtifact:
    """Raw evidence bytes; the digest is COMPUTED from the bytes, never asserted."""

    data: bytes
    source: SourceMeta

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @classmethod
    def from_bytes(cls, data: bytes, *, path: str = "", producer_hint: str = "") -> "OriginalArtifact":
        meta = SourceMeta(path=path, producer_hint=producer_hint, size=len(data))
        return cls(data=bytes(data), source=meta)

    @classmethod
    def from_path(cls, path: str, *, producer_hint: str = "") -> "OriginalArtifact":
        with open(path, "rb") as handle:
            data = handle.read()
        return cls.from_bytes(data, path=str(path), producer_hint=producer_hint)


@dataclass(frozen=True)
class ProducerVerdict:
    """Result of the producer trust check. ``authenticated`` is the trust statement."""

    authenticated: bool
    producer: str = ""
    detail: str = ""


class TrustPolicy(Protocol):
    """Owner-controlled producer authentication. Implementations must not trust
    caller-supplied keys or self-signed artifacts as a trusted producer."""

    def verify(self, artifact: OriginalArtifact) -> ProducerVerdict: ...


class NullTrustPolicy:
    """The default: no trust anchor is configured, so nothing is authenticated."""

    def verify(self, artifact: OriginalArtifact) -> ProducerVerdict:
        return ProducerVerdict(False, "", "no producer trust anchor configured")


@dataclass(frozen=True)
class ParsedEvent:
    """A schema-validated event. Sealed: only ``parse_artifact`` may produce one."""

    schema: str
    kind: str
    artifact_sha256: str
    artifact_path: str
    fields: Mapping[str, Any]
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise PermissionError("ParsedEvent may only be produced by parse_artifact()")


@dataclass(frozen=True)
class ParseOutcome:
    status: str
    events: tuple[ParsedEvent, ...]
    detail: str


def parse_artifact(artifact: OriginalArtifact) -> ParseOutcome:
    """Deterministically parse original bytes into schema-validated events.

    Fails closed: any malformed/truncated/unsupported/inconsistent input yields
    ``MALFORMED`` or ``UNSUPPORTED`` with no events.
    """
    try:
        doc = json.loads(artifact.data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return ParseOutcome(STATUS_MALFORMED, (), f"artifact is not valid UTF-8 JSON: {exc}")
    if not isinstance(doc, Mapping):
        return ParseOutcome(STATUS_MALFORMED, (), "artifact root must be a JSON object")
    schema = doc.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        return ParseOutcome(STATUS_UNSUPPORTED, (), f"unsupported schema {schema!r}")
    raw_events = doc.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        return ParseOutcome(STATUS_MALFORMED, (), "artifact must contain a non-empty events list")

    events: list[ParsedEvent] = []
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, Mapping):
            return ParseOutcome(STATUS_MALFORMED, (), f"event {index} is not an object")
        kind = raw.get("kind")
        if kind not in _REQUIRED_BY_KIND:
            return ParseOutcome(STATUS_MALFORMED, (), f"event {index} has unknown kind {kind!r}")
        if schema == SCHEMA_VMSWITCH_ACL_DROP and kind != _KIND_VMSWITCH:
            return ParseOutcome(STATUS_MALFORMED, (), f"event {index} kind {kind!r} not allowed in {schema}")
        if schema == SCHEMA_PKTMON_DROP and kind != _KIND_PKTMON:
            return ParseOutcome(STATUS_MALFORMED, (), f"event {index} kind {kind!r} not allowed in {schema}")
        for name in _REQUIRED_BY_KIND[kind]:
            value = raw.get(name)
            if value is None or (isinstance(value, str) and value.strip() == ""):
                return ParseOutcome(STATUS_MALFORMED, (), f"event {index} missing field {name!r}")
        events.append(
            ParsedEvent(
                schema=schema,
                kind=kind,
                artifact_sha256=artifact.sha256,
                artifact_path=artifact.source.path,
                fields=dict(raw),
                _seal=_SEAL,
            )
        )
    return ParseOutcome("OK", tuple(events), f"parsed {len(events)} event(s)")


@dataclass(frozen=True)
class Observation:
    """A validated event bound to the artifact digest. Sealed."""

    kind: str
    artifact_sha256: str
    fields: Mapping[str, Any]
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise PermissionError("Observation may only be produced by observations_from()")


def observations_from(parse: ParseOutcome) -> tuple[Observation, ...]:
    if parse.status != "OK":
        return ()
    return tuple(
        Observation(kind=e.kind, artifact_sha256=e.artifact_sha256, fields=dict(e.fields), _seal=_SEAL)
        for e in parse.events
    )


@dataclass(frozen=True)
class CorrelationRule:
    """Owner authorization + matching policy for two-source correlation."""

    version: str = "option-c-correlation/1"
    authorized: bool = False
    f2_switch_name: str = "F2-Internal-Switch"
    vm_name: str = "F2-Isolation-VM"
    vm_port_fname: str = ""
    expected_acl_weight: int = 1
    window_tolerance_seconds: float = 5.0
    require_authenticated_producer: bool = True


@dataclass(frozen=True)
class CorrelationCandidate:
    """A plausible two-source correlation. Sealed; NOT an accepted attribution."""

    dimension: str
    artifact_sha256: str
    flow_artifact_sha256: str
    switch_name: str
    port_name: str
    weight: int
    flow: tuple[str, str, int]
    window_start: float
    window_stop: float
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise PermissionError("CorrelationCandidate may only be produced by correlate()")


@dataclass(frozen=True)
class CorrelationOutcome:
    status: str
    candidate: CorrelationCandidate | None
    detail: str


def _in_window(ts: float, probe_at: float, tol: float) -> bool:
    return abs(float(ts) - float(probe_at)) <= tol


def _acl_matches(o: Observation, rule: CorrelationRule, probe_at: float) -> bool:
    f = o.fields
    return (
        str(f.get("provider")) == "Microsoft-Windows-Hyper-V-VmSwitch"
        and int(f.get("event_id", 0)) == 131
        and str(f.get("switch_name")) == rule.f2_switch_name
        and str(f.get("vm_name")) == rule.vm_name
        and str(f.get("direction")).lower() in ("egress", "out")
        and int(f.get("weight", -1)) == rule.expected_acl_weight
        and (rule.vm_port_fname == "" or str(f.get("port_fname")) == rule.vm_port_fname)
        and _in_window(f.get("timestamp", 0.0), probe_at, rule.window_tolerance_seconds)
    )


def _flow_matches(o: Observation, rule: CorrelationRule, probe_at: float, probe_protocol: str, probe_target: str) -> bool:
    f = o.fields
    component = str(f.get("component", "")).lower()
    target = f"{f.get('ip_dst')}:{int(f.get('port_dst', 0))}"
    return (
        component.endswith("vmswitch.sys")
        and str(f.get("switch_name")) == rule.f2_switch_name
        and str(f.get("direction")).lower() in ("egress", "out")
        and str(f.get("protocol")).lower() == probe_protocol.lower()
        and target == probe_target
        and _in_window(f.get("timestamp", 0.0), probe_at, rule.window_tolerance_seconds)
    )


def correlate(
    *,
    dimension: str,
    probe_protocol: str,
    probe_target: str,
    probe_at: float,
    acl_observations: Sequence[Observation] = (),
    flow_observations: Sequence[Observation] = (),
    rule: CorrelationRule = CorrelationRule(),
) -> CorrelationOutcome:
    """Correlate an Event-131 ACL drop with a flow-carrying packet-drop record.

    Never returns ``ACCEPTED_ATTRIBUTION``. ``CORRELATED_CANDIDATE`` means both
    sources agree on boundary, direction and time -- it does NOT prove the same
    physical packet caused both, nor that the intended ACL did (Event 131 carries
    no ACL identity beyond weight; the flow source's reason is generic).
    """
    acl = [o for o in acl_observations if o.kind == _KIND_VMSWITCH and _acl_matches(o, rule, probe_at)]
    flow = [
        o for o in flow_observations
        if o.kind == _KIND_PKTMON and _flow_matches(o, rule, probe_at, probe_protocol, probe_target)
    ]
    if not acl or not flow:
        return CorrelationOutcome(STATUS_NO_MATCH, None, "no ACL-drop and flow-drop agree on the boundary and window")
    if len(acl) > 1 or len(flow) > 1:
        return CorrelationOutcome(
            STATUS_AMBIGUOUS, None,
            f"{len(acl)} ACL-drop and {len(flow)} flow-drop records match; refusing an arbitrary pick",
        )
    a_fields = acl[0].fields
    return CorrelationOutcome(
        STATUS_CORRELATED_CANDIDATE,
        CorrelationCandidate(
            dimension=dimension,
            artifact_sha256=acl[0].artifact_sha256,
            flow_artifact_sha256=flow[0].artifact_sha256,
            switch_name=str(a_fields.get("switch_name")),
            port_name=str(a_fields.get("port_name")),
            weight=int(a_fields.get("weight", 0)),
            flow=(str(flow[0].fields.get("ip_dst")), str(flow[0].fields.get("protocol")).lower(), int(flow[0].fields.get("port_dst", 0))),
            window_start=probe_at - rule.window_tolerance_seconds,
            window_stop=probe_at + rule.window_tolerance_seconds,
            _seal=_SEAL,
        ),
        "ACL drop at the F2 port and a matching flow drop agree in-window (correlation, not causation)",
    )


@dataclass(frozen=True)
class AcceptedAttribution:
    """An attribution the pipeline has accepted. Sealed; only accept_attribution
    may produce one. Carries the artifact it was derived from and the rule version."""

    dimension: str
    artifact_sha256: str
    flow_artifact_sha256: str
    switch_name: str
    port_name: str
    weight: int
    flow: tuple[str, str, int]
    window_start: float
    window_stop: float
    run_arm_id: str
    run_rollout_id: str
    rule_version: str
    producer: str
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise PermissionError("AcceptedAttribution may only be produced by accept_attribution()")


@dataclass(frozen=True)
class AcceptOutcome:
    status: str
    attribution: AcceptedAttribution | None
    detail: str


def accept_attribution(
    *,
    dimension: str,
    probe_protocol: str,
    probe_target: str,
    probe_at: float,
    acl_observations: Sequence[Observation] = (),
    flow_observations: Sequence[Observation] = (),
    artifacts: Sequence[OriginalArtifact] = (),
    rule: CorrelationRule = CorrelationRule(),
    trust_policy: TrustPolicy | None = None,
    run_arm_id: str = "",
    run_rollout_id: str = "",
) -> AcceptOutcome:
    """The ONLY transition to ACCEPTED_ATTRIBUTION. Fails closed unless the rule is
    authorized and (by default) every contributing artifact is authenticated by the
    supplied ``trust_policy``. The default policy authenticates nothing, so without
    an owner trust anchor this always returns ``UNTRUSTED``.

    Authenticity is decided from the actual artifact bytes via the policy -- it is
    never taken from a caller-supplied verdict -- and both the ACL and flow
    artifacts must authenticate, so a caller cannot assert trust by constructing an
    object.
    """
    if not rule.authorized:
        return AcceptOutcome(STATUS_UNTRUSTED, None, f"correlation rule {rule.version!r} is not authorized")

    authenticated: set[str] = set()
    producer = ""
    if rule.require_authenticated_producer:
        policy = trust_policy if trust_policy is not None else NullTrustPolicy()
        if not artifacts:
            return AcceptOutcome(STATUS_UNTRUSTED, None, "no artifact supplied for producer authentication")
        for art in artifacts:
            verdict = policy.verify(art)
            if not verdict.authenticated:
                return AcceptOutcome(STATUS_UNTRUSTED, None, f"artifact not authenticated: {verdict.detail}")
            authenticated.add(art.sha256)
            producer = verdict.producer or producer

    outcome = correlate(
        dimension=dimension,
        probe_protocol=probe_protocol,
        probe_target=probe_target,
        probe_at=probe_at,
        acl_observations=acl_observations,
        flow_observations=flow_observations,
        rule=rule,
    )
    if outcome.status != STATUS_CORRELATED_CANDIDATE or outcome.candidate is None:
        return AcceptOutcome(outcome.status, None, outcome.detail)
    candidate = outcome.candidate
    if rule.require_authenticated_producer:
        if candidate.artifact_sha256 not in authenticated or candidate.flow_artifact_sha256 not in authenticated:
            return AcceptOutcome(STATUS_UNTRUSTED, None, "contributing artifact was not authenticated")
    return AcceptOutcome(
        STATUS_ACCEPTED_ATTRIBUTION,
        AcceptedAttribution(
            dimension=candidate.dimension,
            artifact_sha256=candidate.artifact_sha256,
            flow_artifact_sha256=candidate.flow_artifact_sha256,
            switch_name=candidate.switch_name,
            port_name=candidate.port_name,
            weight=candidate.weight,
            flow=candidate.flow,
            window_start=candidate.window_start,
            window_stop=candidate.window_stop,
            run_arm_id=run_arm_id,
            run_rollout_id=run_rollout_id,
            rule_version=rule.version,
            producer=producer,
            _seal=_SEAL,
        ),
        "accepted by an authorized rule and authenticated producer",
    )


def fresh_now() -> float:
    return time.time()
