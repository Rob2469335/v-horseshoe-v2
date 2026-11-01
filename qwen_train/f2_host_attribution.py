"""Option C host-drop attribution gate -- PROPOSED, VERSIONED, OFF BY DEFAULT.

This gate consumes only sealed :class:`~qwen_train.f2_evidence_ingestion.AcceptedAttribution`
tokens produced by the trusted ingestion pipeline. It deliberately does NOT accept
raw records, JSON mappings, or self-computed digests, so a caller cannot manufacture
accepted evidence by constructing an object -- the previous bypass is removed.

The frozen baseline contract remains the default: ``acceptance=OPTION_C_BASELINE``
(or anything other than the exact :data:`OPTION_C_ACCEPTANCE_VERSION`) ignores the
tokens entirely and returns :func:`qwen_train.f2_isolation.verify_isolation_attestation`
verbatim. Activating the rule for an experiment requires owner approval and is not
the default. Nothing here is wired into readiness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from qwen_train import f2_isolation as iso
from qwen_train.f2_evidence_ingestion import AcceptedAttribution

__all__ = [
    "VMSWITCH_PROVIDER",
    "VMSWITCH_ACL_DROP_EVENT_ID",
    "VMSWITCH_ACL_DROP_CHANNEL",
    "VMSWITCH_COMPONENT",
    "DEFAULT_F2_SWITCH_NAME",
    "OPTION_C_BASELINE",
    "OPTION_C_ACCEPTANCE_VERSION",
    "DEFAULT_MAX_CAPTURE_AGE_SECONDS",
    "HostAttributionVerdict",
    "apply_host_attribution",
]

VMSWITCH_PROVIDER = "Microsoft-Windows-Hyper-V-VmSwitch"
VMSWITCH_ACL_DROP_EVENT_ID = 131
VMSWITCH_ACL_DROP_CHANNEL = "Microsoft-Windows-Hyper-V-VmSwitch-Diagnostic-Traffic"
VMSWITCH_COMPONENT = "vmswitch.sys"
DEFAULT_F2_SWITCH_NAME = "F2-Internal-Switch"

OPTION_C_BASELINE = "baseline"
OPTION_C_ACCEPTANCE_VERSION = "option-c/1"

DEFAULT_MAX_CAPTURE_AGE_SECONDS = 3600.0


@dataclass(frozen=True)
class HostAttributionVerdict:
    """The Option C verdict over an attestation plus accepted host attributions.

    ``baseline`` is the unchanged frozen verdict. ``attributed_dimensions`` are the
    denied-required dimensions lifted from ``unknown`` by accepted evidence.
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


def _binding_reason(acc: AcceptedAttribution, att: iso.IsolationAttestation, probes: Sequence[iso.ProbeResult]) -> str:
    """Why an accepted token does NOT bind to this attestation/dimension ("" = binds)."""
    if acc.run_arm_id != att.arm_id or acc.run_rollout_id != att.rollout_id:
        return "attribution is not bound to this arm/rollout"
    if not iso.is_sha256_hex(acc.artifact_sha256):
        return "attribution carries no artifact digest"
    unknown_probes = [p for p in probes if p.outcome == iso.OUTCOME_UNKNOWN]
    if not unknown_probes:
        return "no unknown observation to attribute"
    if not any(acc.window_start <= p.at <= acc.window_stop for p in unknown_probes):
        return "attribution window does not contain the probe observation"
    return ""


def apply_host_attribution(
    att: iso.IsolationAttestation | Mapping[str, Any],
    accepted: Sequence[AcceptedAttribution] = (),
    *,
    acceptance: str = OPTION_C_BASELINE,
    required_services: Sequence[str] = (),
) -> HostAttributionVerdict:
    """Recompute the verdict with Option C attribution, GATED and off by default.

    ``accepted`` must contain only sealed ``AcceptedAttribution`` tokens from the
    trusted pipeline. With ``acceptance`` left at the default (baseline), the tokens
    are ignored entirely.
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
            detail="baseline contract in force; host attributions ignored",
        )

    by_dim: dict[str, list[iso.ProbeResult]] = {}
    for p in att.probes:
        by_dim.setdefault(p.dimension, []).append(p)

    attributed: list[str] = []
    rejected: list[tuple[str, str]] = []
    still_unproven: list[str] = []

    for dim in baseline.unproven_dimensions:
        if dim not in iso.DENIED_REQUIRED:
            still_unproven.append(dim)
            continue
        probes = by_dim.get(dim, [])
        qualifying: list[AcceptedAttribution] = []
        for acc in accepted:
            if acc.dimension != dim:
                continue
            reason = _binding_reason(acc, att, probes)
            if reason == "":
                qualifying.append(acc)
            else:
                rejected.append((dim, reason))
        distinct = {(a.artifact_sha256, a.flow, a.weight) for a in qualifying}
        if not qualifying:
            still_unproven.append(dim)
        elif len(distinct) == 1:
            attributed.append(dim)
        else:
            rejected.append((dim, f"ambiguous: {len(distinct)} distinct accepted attributions"))
            still_unproven.append(dim)

    satisfied = not still_unproven and not baseline.failed_dimensions
    detail = (
        f"Option C {OPTION_C_ACCEPTANCE_VERSION}: attributed={attributed or 'none'}; "
        f"unproven={still_unproven or 'none'}; failed={list(baseline.failed_dimensions) or 'none'}"
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
