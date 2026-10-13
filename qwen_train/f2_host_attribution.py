"""Option C host-side extended-port-ACL drop attribution -- PROPOSED, VERSIONED, OFF BY DEFAULT.

This module implements the *proposed* Q9 acceptance rule that the experiment owner
authorised on 2026-10-12 as a repository-side, clearly separated and versioned
change. It is deliberately NOT wired into the default verifier
(:func:`qwen_train.f2_isolation.verify_isolation_attestation`) or the readiness
gate, and it MUST NOT be activated for an experiment without a separate explicit
owner authorisation. The frozen baseline contract remains the default.

The proposed rule (:data:`OPTION_C_ACCEPTANCE_VERSION`)
-------------------------------------------------------
An otherwise ``unknown`` denied-required dimension (the signature of a *silent*
Hyper-V virtual-switch drop) may be accepted ONLY when an **ACL-specific**
``Microsoft-Windows-Hyper-V-VmSwitch`` drop event is reliably bound to that
dimension's probe, the F2 virtual-switch boundary, the run and a fresh capture
window. The qualifying event is the manifest event

    id 131, channel ``Microsoft-Windows-Hyper-V-VmSwitch-Diagnostic-Traffic``,
    "A packet was dropped on port %2 ... on switch %6 ... because the packet is
    filtered by %9 extended port ACL entry with weight %10."

Everything else is REJECTED, fail closed: generic ``Failed Security Policy``
drops (the vSwitch applies many security policies -- extended Port ACL, Port ACL,
IP-spoofing, RouterGuard, DHCPGuard, isolation -- so the reason alone does not
identify the extended ACL), local ``WSAEACCES`` refusals without host attribution,
timeouts without qualifying host evidence, mismatched flows, wrong boundary
components, ambiguous matches, incomplete records, stale/replayed records and
untrusted/fabricated records.

Documented evidence limitation (NOT worked around)
--------------------------------------------------
The event exists in the provider manifest, but the exact payload field names and
whether the emitted event carries the full 5-tuple are NOT ESTABLISHED without a
capture (which is out of scope here). This module therefore REQUIRES those fields
to be present in a supplied record and fails closed when they are absent; it does
not synthesise, infer, or assume them.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from qwen_train import f2_isolation as iso

__all__ = [
    "VMSWITCH_PROVIDER",
    "VMSWITCH_ACL_DROP_EVENT_ID",
    "VMSWITCH_ACL_DROP_CHANNEL",
    "VMSWITCH_COMPONENT",
    "ACL_SPECIFICITY_EXTENDED",
    "ACL_SPECIFICITY_GENERIC",
    "DEFAULT_F2_SWITCH_NAME",
    "OPTION_C_ACCEPTANCE_VERSION",
    "OPTION_C_BASELINE",
    "DEFAULT_MAX_CAPTURE_AGE_SECONDS",
    "HostDropRecord",
    "HostAttributionVerdict",
    "record_rejection_reason",
    "apply_host_attribution",
]

#: The only ETW provider whose ACL-specific drop event is accepted.
VMSWITCH_PROVIDER = "Microsoft-Windows-Hyper-V-VmSwitch"

#: The only accepted drop event id (the extended-port-ACL filter event).
VMSWITCH_ACL_DROP_EVENT_ID = 131

#: The channel that event is published on.
VMSWITCH_ACL_DROP_CHANNEL = "Microsoft-Windows-Hyper-V-VmSwitch-Diagnostic-Traffic"

#: The driver image that must own the drop.
VMSWITCH_COMPONENT = "vmswitch.sys"

#: Only this specificity is accepted as attribution. The generic reason is
#: corroboration at best and never sufficient on its own.
ACL_SPECIFICITY_EXTENDED = "extended_port_acl_entry"
ACL_SPECIFICITY_GENERIC = "generic_security_policy"

#: The F2 virtual switch the drop must be observed on.
DEFAULT_F2_SWITCH_NAME = "F2-Internal-Switch"

#: Acceptance-rule versions. Passing anything other than
#: :data:`OPTION_C_ACCEPTANCE_VERSION` (which must be explicit) leaves the
#: baseline contract in force.
OPTION_C_BASELINE = "baseline"
OPTION_C_ACCEPTANCE_VERSION = "option-c/1"

#: A capture older than this is not accepted (staleness bound).
DEFAULT_MAX_CAPTURE_AGE_SECONDS = 3600.0

#: Clock skew tolerated when checking the capture window against ``now``.
_WINDOW_SKEW_SECONDS = 5.0

_REQUIRED_STRINGS = (
    "source", "channel", "vm_name", "switch_name", "component", "direction",
    "protocol", "ip_src", "ip_dst", "acl_specificity", "capture_artifact_sha256",
    "run_arm_id", "run_rollout_id",
)


@dataclass(frozen=True)
class HostDropRecord:
    """One host-side drop attribution record (operator/tooling supplied).

    Every field is a claim by the capture tooling. This module validates the
    claim strictly; it never produces one. All fields are digest-bound through
    :meth:`digest`.
    """

    source: str
    event_id: int
    channel: str
    vm_name: str
    switch_name: str
    component: str
    direction: str
    protocol: str
    ip_src: str
    ip_dst: str
    port_dst: int
    acl_specificity: str
    acl_entry: str
    acl_weight: str
    drop_reason: str
    pkt_group_id: str
    capture_artifact_sha256: str
    capture_window_start: float
    capture_window_stop: float
    probe_at: float
    run_arm_id: str
    run_rollout_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "event_id": int(self.event_id),
            "channel": self.channel,
            "vm_name": self.vm_name,
            "switch_name": self.switch_name,
            "component": self.component,
            "direction": self.direction,
            "protocol": self.protocol,
            "ip_src": self.ip_src,
            "ip_dst": self.ip_dst,
            "port_dst": int(self.port_dst),
            "acl_specificity": self.acl_specificity,
            "acl_entry": self.acl_entry,
            "acl_weight": self.acl_weight,
            "drop_reason": self.drop_reason,
            "pkt_group_id": self.pkt_group_id,
            "capture_artifact_sha256": self.capture_artifact_sha256,
            "capture_window_start": round(float(self.capture_window_start), 3),
            "capture_window_stop": round(float(self.capture_window_stop), 3),
            "probe_at": round(float(self.probe_at), 3),
            "run_arm_id": self.run_arm_id,
            "run_rollout_id": self.run_rollout_id,
        }

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def _coerce_record(record: HostDropRecord | Mapping[str, Any]) -> HostDropRecord:
    if isinstance(record, HostDropRecord):
        return record
    if not isinstance(record, Mapping):
        raise ValueError("host drop record must be a HostDropRecord or mapping")
    return HostDropRecord(
        source=str(record.get("source", "")),
        event_id=int(record.get("event_id", 0) or 0),
        channel=str(record.get("channel", "")),
        vm_name=str(record.get("vm_name", "")),
        switch_name=str(record.get("switch_name", "")),
        component=str(record.get("component", "")),
        direction=str(record.get("direction", "")),
        protocol=str(record.get("protocol", "")),
        ip_src=str(record.get("ip_src", "")),
        ip_dst=str(record.get("ip_dst", "")),
        port_dst=int(record.get("port_dst", 0) or 0),
        acl_specificity=str(record.get("acl_specificity", "")),
        acl_entry=str(record.get("acl_entry", "")),
        acl_weight=str(record.get("acl_weight", "")),
        drop_reason=str(record.get("drop_reason", "")),
        pkt_group_id=str(record.get("pkt_group_id", "")),
        capture_artifact_sha256=str(record.get("capture_artifact_sha256", "")),
        capture_window_start=float(record.get("capture_window_start", 0.0) or 0.0),
        capture_window_stop=float(record.get("capture_window_stop", 0.0) or 0.0),
        probe_at=float(record.get("probe_at", 0.0) or 0.0),
        run_arm_id=str(record.get("run_arm_id", "")),
        run_rollout_id=str(record.get("run_rollout_id", "")),
    )


def _target_of(host: str, port: int) -> str:
    return f"{host}:{port}"


def record_rejection_reason(
    record: HostDropRecord | Mapping[str, Any],
    probe: iso.ProbeResult,
    att: iso.IsolationAttestation,
    *,
    f2_switch_name: str = DEFAULT_F2_SWITCH_NAME,
    now: float | None = None,
    max_capture_age_seconds: float = DEFAULT_MAX_CAPTURE_AGE_SECONDS,
) -> str:
    """Return ``""`` when ``record`` is a fully qualifying attribution for
    ``probe``; otherwise return the reason it is REJECTED. Ordered, fail-closed.
    """
    now = time.time() if now is None else now
    rec = _coerce_record(record)

    for name in _REQUIRED_STRINGS:
        if not str(getattr(rec, name)).strip():
            return f"missing field: {name}"
    if rec.event_id != VMSWITCH_ACL_DROP_EVENT_ID:
        return f"not the ACL-specific event (event_id={rec.event_id!r})"
    if rec.acl_specificity != ACL_SPECIFICITY_EXTENDED:
        return f"not ACL-specific (acl_specificity={rec.acl_specificity!r})"
    if not rec.acl_entry.strip():
        return "missing extended port ACL entry"
    if not rec.acl_weight.strip():
        return "missing extended port ACL weight"
    if rec.source != VMSWITCH_PROVIDER:
        return f"wrong provider ({rec.source!r})"
    if rec.switch_name != f2_switch_name:
        return f"wrong switch ({rec.switch_name!r} != {f2_switch_name!r})"
    if not rec.component.lower().endswith(VMSWITCH_COMPONENT):
        return f"wrong component ({rec.component!r})"
    if rec.direction.strip().lower() not in ("egress", "out"):
        return f"wrong direction ({rec.direction!r})"
    if not iso.is_sha256_hex(rec.capture_artifact_sha256):
        return "capture artifact digest is not a 64-hex SHA-256"
    if rec.protocol.strip().lower() != probe.protocol.strip().lower():
        return f"protocol mismatch ({rec.protocol!r} != {probe.protocol!r})"
    if _target_of(rec.ip_dst.strip(), int(rec.port_dst)) != probe.target:
        return (
            f"flow mismatch ({_target_of(rec.ip_dst, rec.port_dst)!r} "
            f"!= probe target {probe.target!r})"
        )
    if not (rec.capture_window_start > 0 and rec.capture_window_stop >= rec.capture_window_start):
        return "malformed capture window"
    if not (rec.capture_window_start <= rec.probe_at <= rec.capture_window_stop):
        return "probe timestamp is outside the capture window"
    if not (rec.capture_window_start <= probe.at <= rec.capture_window_stop):
        return "probe observation is outside the capture window"
    if rec.capture_window_stop > now + _WINDOW_SKEW_SECONDS:
        return "capture window is in the future"
    if (now - rec.capture_window_stop) > max_capture_age_seconds:
        return "capture is stale"
    if rec.run_arm_id != att.arm_id or rec.run_rollout_id != att.rollout_id:
        return "record is not bound to this arm/rollout"
    return ""


@dataclass(frozen=True)
class HostAttributionVerdict:
    """The Option C verdict over an attestation plus host drop records.

    ``baseline`` is the unchanged frozen verdict. ``attributed_dimensions`` are
    the denied-required dimensions that the qualifying host evidence lifted from
    ``unknown`` to attributed. ``satisfied`` is the combined result.
    """

    baseline: iso.VerificationVerdict
    acceptance_version: str
    attributed_dimensions: tuple[str, ...]
    unproven_dimensions: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    satisfied: bool
    detail: str

    def as_readiness_mapping(self) -> dict[str, str]:
        mapping = dict(self.baseline.as_readiness_mapping())
        for dim in self.attributed_dimensions:
            mapping[dim] = iso.OUTCOME_DENIED
        return mapping

    def to_dict(self) -> dict[str, Any]:
        return {
            "acceptance_version": self.acceptance_version,
            "satisfied": self.satisfied,
            "attributed_dimensions": list(self.attributed_dimensions),
            "unproven_dimensions": list(self.unproven_dimensions),
            "rejected": [{"dimension": d, "reason": r} for d, r in self.rejected],
            "detail": self.detail,
            "baseline": self.baseline.to_dict(),
        }


def apply_host_attribution(
    att: iso.IsolationAttestation | Mapping[str, Any],
    records: Sequence[HostDropRecord | Mapping[str, Any]] = (),
    *,
    acceptance: str = OPTION_C_BASELINE,
    f2_switch_name: str = DEFAULT_F2_SWITCH_NAME,
    required_services: Sequence[str] = (),
    now: float | None = None,
    max_capture_age_seconds: float = DEFAULT_MAX_CAPTURE_AGE_SECONDS,
) -> HostAttributionVerdict:
    """Recompute the verdict with Option C host attribution, GATED and off by default.

    With ``acceptance`` left at :data:`OPTION_C_BASELINE` (the default), the
    records are ignored entirely and the result is exactly the frozen
    :func:`qwen_train.f2_isolation.verify_isolation_attestation` verdict. Only an
    explicit ``acceptance=OPTION_C_ACCEPTANCE_VERSION`` activates the proposed
    rule -- which requires owner approval and is not the default.
    """
    if isinstance(att, Mapping):
        att = iso.parse_attestation(att)

    baseline = iso.verify_isolation_attestation(att, required_services=required_services)

    if acceptance != OPTION_C_ACCEPTANCE_VERSION:
        return HostAttributionVerdict(
            baseline=baseline,
            acceptance_version=OPTION_C_BASELINE,
            attributed_dimensions=(),
            unproven_dimensions=baseline.unproven_dimensions,
            rejected=(),
            satisfied=baseline.satisfied,
            detail="baseline contract in force; host drop records ignored",
        )

    by_dim: dict[str, list[iso.ProbeResult]] = {}
    for p in att.probes:
        by_dim.setdefault(p.dimension, []).append(p)

    attributed: list[str] = []
    rejected: list[tuple[str, str]] = []
    still_unproven: list[str] = []

    for dim in baseline.unproven_dimensions:
        if dim not in iso.DENIED_REQUIRED:
            # binding / negative_control / dns_behaviour / coverage are never
            # attributable by a host drop record.
            still_unproven.append(dim)
            continue

        probes = by_dim.get(dim, [])
        # Only a silent/unknown observation may be lifted; a permitted
        # observation would already be in failed_dimensions, not unproven.
        candidates: list[tuple[iso.ProbeResult, HostDropRecord]] = []
        for probe in probes:
            if probe.outcome != iso.OUTCOME_UNKNOWN:
                continue
            for raw in records:
                try:
                    reason = record_rejection_reason(
                        raw, probe, att, f2_switch_name=f2_switch_name,
                        now=now, max_capture_age_seconds=max_capture_age_seconds,
                    )
                except (ValueError, TypeError) as exc:  # malformed record
                    rejected.append((dim, f"malformed record: {exc}"))
                    continue
                if reason == "":
                    candidates.append((probe, _coerce_record(raw)))
                else:
                    rejected.append((dim, reason))

        distinct = {rec.digest() for _p, rec in candidates}
        if not candidates:
            still_unproven.append(dim)
        elif len(distinct) == 1:
            attributed.append(dim)
        else:
            rejected.append((dim, f"ambiguous: {len(distinct)} distinct qualifying records"))
            still_unproven.append(dim)

    satisfied = not still_unproven and not baseline.failed_dimensions
    if satisfied:
        detail = (
            f"Option C {OPTION_C_ACCEPTANCE_VERSION}: "
            f"{len(attributed)} dimension(s) attributed to the extended port ACL "
            f"by host drop evidence; all required dimensions satisfied"
        )
    else:
        detail = (
            f"Option C {OPTION_C_ACCEPTANCE_VERSION}: "
            f"attributed={attributed or 'none'}; "
            f"unproven={still_unproven or 'none'}; "
            f"failed={list(baseline.failed_dimensions) or 'none'}"
        )

    return HostAttributionVerdict(
        baseline=baseline,
        acceptance_version=OPTION_C_ACCEPTANCE_VERSION,
        attributed_dimensions=tuple(attributed),
        unproven_dimensions=tuple(still_unproven),
        rejected=tuple(rejected),
        satisfied=satisfied,
        detail=detail,
    )
