"""F2 confirmatory analysis: paired ledger -> final result (F2-IMPL-AUTH-019).

This is the final link of the F2 chain:

    ... -> paired outcome -> receipt -> statistical ledger -> final result

It is PURE and OFFLINE: no experiment is executed, no service is contacted, and
no trust is placed in any producer. Given the admitted paired observations (one
``(T endpoint, X endpoint)`` pair **per task** — F2-CLARIFICATION-006; one pair =
one task, F2-IMPL-AUTH-024 s3), it produces the FROZEN
confirmatory result:

* the exact two-sided McNemar test; and
* the authoritative two-sided 95% Clopper-Pearson exact conditional interval on
  the discordant direction, transformed to the paired risk difference
  (``F2-CLARIFICATION-004`` / ``F2-IMPL-AUTH-016``).

``finalize_f2`` is the PRODUCER. ``independent_reconstruction`` is the auditor:
it re-derives the same numbers from the raw booleans WITHOUT calling any
``f2_statistics`` function, so the reported result can be cross-checked
(``>>>`` the two must agree). It is deliberately a second implementation, not a
wrapper, so a defect in one is caught by the other.

Missingness (Q5/Q6, `F2-IMPL-AUTH-018`): a pair with either arm missing is NOT a
success, NOT a failure, and is NOT silently dropped. It is excluded from the
primary paired analysis and reported in the ledger accounting. Missing is
missing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import comb
from typing import Any, Iterable, Mapping

# The authorized, pre-declared infrastructure-failure taxonomy lives with the
# calibration layer that introduced it. Importing it here (rather than
# re-declaring it) guarantees the Q6 gate and the rerun policy can never drift
# into disagreeing about what counts as an infrastructure fault.
from qwen_train.f2_calibration import INFRASTRUCTURE_RERUN_CAUSES

__all__ = [
    "F2_ALPHA",
    "F2_CONFIDENCE",
    "F2_INFRA_MAX_FRACTION",
    "PairedObservation",
    "F2ConfirmatoryResult",
    "InfrastructureGate",
    "classify_missing_reason",
    "evaluate_infrastructure_gate",
    "finalize_f2",
    "independent_reconstruction",
]

#: Frozen F2 design constants (AUTH-013). Not parameters of this module.
F2_ALPHA = 0.05
F2_CONFIDENCE = 0.95

#: Q6 (F2-IMPL-AUTH-018): the authorized ceiling on the fraction of the frozen
#: population lost to PRE-DECLARED infrastructure failure. Exceeding it is a
#: STOP-AND-DIAGNOSE condition, not a scientific result. The ledger already
#: recorded missingness; what was missing was the gate itself.
F2_INFRA_MAX_FRACTION = 0.30

#: Marker prefix the authorized calibration/admission layers use when they record
#: an infrastructure cause (``invalid_reason=f"infrastructure:{cause}"``).
_INFRA_PREFIX = "infrastructure:"

#: Reasons that are missingness but NOT infrastructure failure. These are
#: scientific/administrative exclusions (pre-declared Q5 reasons) and are
#: reported separately so an infrastructure outage can never be laundered into a
#: scientific exclusion, or vice versa.
_SCIENTIFIC_MISSING_REASONS = frozenset(
    {
        "missing_required_provenance",
        "failed_base_gold_verification",
        "contamination_classification",
        "malformed_task",
        "reproducibility_failure",
        "missing_required_artifact",
        "endpoint_unmeasurable",
        "censored",
        "unspecified",
        "",
    }
)


def classify_missing_reason(reason: str) -> str:
    """Classify a ledger ``missing_reason`` as infrastructure / scientific / unknown.

    The classification is a closed, pre-declared taxonomy. An unrecognised
    non-empty reason is ``"unknown"`` rather than being optimistically filed as
    scientific: an unknown reason must never be used to shrink the measured
    infrastructure-failure rate.
    """
    text = str(reason or "").strip()
    if not text:
        return "scientific"
    if text.startswith(_INFRA_PREFIX):
        cause = text[len(_INFRA_PREFIX):]
        if cause in INFRASTRUCTURE_RERUN_CAUSES:
            return "infrastructure"
        return "unknown"
    if text in _SCIENTIFIC_MISSING_REASONS:
        return "scientific"
    return "unknown"


@dataclass(frozen=True)
class InfrastructureGate:
    """Outcome of the Q6 infrastructure-failure gate.

    ``satisfied`` is ``True`` only when the measured infrastructure-failure
    fraction is at or below :data:`F2_INFRA_MAX_FRACTION`. It never alters the
    frozen statistics: it is an additional, separately reported verdict.
    """

    n_total: int
    n_missing: int
    n_infrastructure: int
    n_scientific_missing: int
    n_unknown_missing: int
    infrastructure_fraction: float
    max_fraction: float
    satisfied: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_total": self.n_total,
            "n_missing": self.n_missing,
            "n_infrastructure": self.n_infrastructure,
            "n_scientific_missing": self.n_scientific_missing,
            "n_unknown_missing": self.n_unknown_missing,
            "infrastructure_fraction": self.infrastructure_fraction,
            "max_fraction": self.max_fraction,
            "satisfied": self.satisfied,
            "detail": self.detail,
        }


def evaluate_infrastructure_gate(
    n_total: int,
    missing_by_reason: Mapping[str, int],
    *,
    max_fraction: float = F2_INFRA_MAX_FRACTION,
) -> InfrastructureGate:
    """Apply the Q6 stop-and-diagnose gate to a recorded missingness ledger.

    The denominator is the whole frozen observation window (``n_total``), not the
    complete-pair count, so a large infrastructure outage cannot hide by also
    shrinking N. ``unknown`` reasons are surfaced separately and never counted as
    scientific exclusions.
    """
    total = max(0, int(n_total))
    n_infra = n_sci = n_unknown = 0
    for reason, count in (missing_by_reason or {}).items():
        n = max(0, int(count))
        if n == 0:
            continue
        kind = classify_missing_reason(reason)
        if kind == "infrastructure":
            n_infra += n
        elif kind == "scientific":
            n_sci += n
        else:
            n_unknown += n

    n_missing = n_infra + n_sci + n_unknown
    fraction = (n_infra / total) if total else 0.0
    satisfied = fraction <= max_fraction
    if satisfied:
        detail = (
            f"infrastructure failures {n_infra}/{total} = {fraction:.4f} "
            f"<= {max_fraction:.2f} threshold"
        )
    else:
        detail = (
            f"STOP-AND-DIAGNOSE: infrastructure failures {n_infra}/{total} = "
            f"{fraction:.4f} EXCEEDS the {max_fraction:.2f} Q6 threshold"
        )
    return InfrastructureGate(
        n_total=total,
        n_missing=n_missing,
        n_infrastructure=n_infra,
        n_scientific_missing=n_sci,
        n_unknown_missing=n_unknown,
        infrastructure_fraction=fraction,
        max_fraction=max_fraction,
        satisfied=satisfied,
        detail=detail,
    )


@dataclass(frozen=True)
class PairedObservation:
    """One T/X pair for ONE task.

    The experimental unit is the task: **one pair = one task**, run once under T
    and once under X (F2-IMPL-AUTH-024 s3, which cites readiness plan s2.4 line
    146: ``m`` = complete task pairs, each pair = one T rollout + one X
    rollout). ``seed`` records which rollout produced the pair; it is NOT a
    second unit. A repeated run of the same task is a replicate inside this
    clustered unit and never increases ``m`` — ``_partition`` fails closed if a
    second complete pair for one task reaches the ledger.

    ``t_endpoint`` / ``x_endpoint`` are ``True`` (a qualifying edit was observed
    inside the horizon), ``False`` (a censored observation: post-delivery steps
    existed but no qualifying edit), or ``None`` (missing: the observation could
    not be established). ``None`` in EITHER arm makes the pair incomplete.
    """

    task_id: str
    seed: int
    t_endpoint: bool | None
    x_endpoint: bool | None
    missing_reason: str = ""

    @property
    def complete(self) -> bool:
        return self.t_endpoint is not None and self.x_endpoint is not None

    @classmethod
    def coerce(cls, raw: Any) -> "PairedObservation":
        """Accept a PairedObservation, a (t, x) 2-tuple, or a mapping."""
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, Mapping):
            return cls(
                task_id=str(raw.get("task_id") or ""),
                seed=int(raw.get("seed") or 0),
                t_endpoint=raw.get("t_endpoint"),
                x_endpoint=raw.get("x_endpoint"),
                missing_reason=str(raw.get("missing_reason") or ""),
            )
        if isinstance(raw, (tuple, list)) and len(raw) == 2:
            return cls(task_id="", seed=0, t_endpoint=raw[0], x_endpoint=raw[1])
        raise TypeError(f"cannot interpret {raw!r} as a PairedObservation")


@dataclass(frozen=True)
class F2ConfirmatoryResult:
    """The frozen F2 confirmatory result (produced by ``finalize_f2``).

    Contingency convention (rows = T, cols = X, 1 = endpoint observed):

        n11 = both observed      n10 = T observed, X not  (= b)
        n01 = T not, X observed  n00 = neither observed
    """

    observation_window: dict[str, Any]
    n_total: int
    n_complete: int
    n_missing: int
    n00: int
    n01: int
    n10: int
    n11: int
    b: int
    c: int
    d: int  # discordant = b + c
    risk_difference: float
    p_value: float
    ci_low: float
    ci_high: float
    direction: str
    alpha: float = F2_ALPHA
    confidence: float = F2_CONFIDENCE
    sid: str = "two-sided"
    missing_by_reason: dict[str, int] = field(default_factory=dict)
    infrastructure_gate: dict[str, Any] = field(default_factory=dict)

    @property
    def infrastructure_gate_satisfied(self) -> bool:
        """Q6 verdict. ``False`` means STOP-AND-DIAGNOSE, not a valid run."""
        return bool(self.infrastructure_gate.get("satisfied", False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_window": dict(self.observation_window),
            "N": self.n_complete,
            "n_total": self.n_total,
            "n_missing": self.n_missing,
            "n00": self.n00,
            "n01": self.n01,
            "n10": self.n10,
            "n11": self.n11,
            "b": self.b,
            "c": self.c,
            "d": self.d,
            "risk_difference": self.risk_difference,
            "p_value": self.p_value,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "direction": self.direction,
            "alpha": self.alpha,
            "confidence": self.confidence,
            "sided": self.sid,
            "missing_by_reason": dict(self.missing_by_reason),
            "infrastructure_gate": dict(self.infrastructure_gate),
        }


def _partition(observations: Iterable[Any]) -> tuple[list[PairedObservation], list[PairedObservation]]:
    obs = [PairedObservation.coerce(o) for o in observations]
    complete = [o for o in obs if o.complete]
    missing = [o for o in obs if not o.complete]

    # F2-CLARIFICATION-006: ONE PAIR = ONE TASK (F2-IMPL-AUTH-024 s3, s6.3).
    # Repeated runs of one task are replicates inside a single clustered unit and
    # "MUST NOT be counted as additional independent pairs"; pseudo-replication
    # would inflate m and break the independence the exact McNemar null assumes.
    # Incomplete rows are exempt: a ledger legitimately carries several attempts
    # for one task (Q6 reruns retain every attempt) as long as at most one of
    # them reaches a complete pair. Empty task_id is the documented tuple/map
    # coercion shortcut, which carries no identity to collide.
    # Fail closed rather than silently drop: choosing which duplicate survives is
    # an exclusion decision, and Q5 requires exclusions to be pre-specified and
    # outcome-independent -- which a post-hoc pick would not be.
    seen: set[str] = set()
    for o in complete:
        if not o.task_id:
            continue
        if o.task_id in seen:
            raise ValueError(
                f"two complete T/X pairs carry task_id {o.task_id!r}: one "
                "pair = one task (F2-IMPL-AUTH-024 s3), so a second complete "
                "pair for the same task is pseudo-replication and must not "
                "increase m. Record the extra attempt as missing under a "
                "pre-specified Q5 reason, or fix the ledger."
            )
        seen.add(o.task_id)

    return complete, missing


def finalize_f2(
    observations: Iterable[Any],
    *,
    observation_window: Mapping[str, Any] | None = None,
    alpha: float = F2_ALPHA,
    confidence: float = F2_CONFIDENCE,
) -> F2ConfirmatoryResult:
    """Produce the frozen confirmatory result from the raw paired ledger.

    The point estimate, exact two-sided McNemar p-value and the authoritative
    Clopper-Pearson-transformed paired-risk-difference interval are delegated to
    the shared ``f2_statistics`` implementation, so this function cannot drift
    from the frozen method.
    """
    from qwen_train.f2_statistics import mcnemar_exact

    complete, missing = _partition(observations)
    t_values = [bool(o.t_endpoint) for o in complete]
    x_values = [bool(o.x_endpoint) for o in complete]

    res = mcnemar_exact(t_values, x_values, sided="two-sided")

    missing_by_reason: dict[str, int] = {}
    for o in missing:
        key = o.missing_reason or "unspecified"
        missing_by_reason[key] = missing_by_reason.get(key, 0) + 1

    n_total = len(complete) + len(missing)
    gate = evaluate_infrastructure_gate(n_total, missing_by_reason)

    return F2ConfirmatoryResult(
        observation_window=dict(observation_window or {}),
        n_total=n_total,
        n_complete=len(complete),
        n_missing=len(missing),
        n00=res.n_concordant_x,
        n01=res.c,
        n10=res.b,
        n11=res.n_concordant_t,
        b=res.b,
        c=res.c,
        d=res.b + res.c,
        risk_difference=res.risk_difference,
        p_value=res.p_value,
        ci_low=res.ci_low,
        ci_high=res.ci_high,
        direction=res.direction,
        alpha=alpha,
        confidence=confidence,
        sid=res.sided,
        missing_by_reason=missing_by_reason,
        infrastructure_gate=gate.to_dict(),
    )


# ---------------------------------------------------------------------------
# Independent reconstruction (F2 section 21). Deliberately does NOT import or
# call qwen_train.f2_statistics: a second implementation catches a defect in
# the first. Formulas are the frozen design's, re-derived from the raw booleans.
# ---------------------------------------------------------------------------
def _binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), from scratch."""
    if n <= 0 or k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k, n + 1))


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), from scratch."""
    if n <= 0 or k >= n:
        return 1.0
    if k < 0:
        return 0.0
    return sum(comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(0, k + 1))


def _cp_bounds(k: int, n: int, alpha: float) -> tuple[float, float]:
    """Two-sided Clopper-Pearson interval for k successes in n trials."""
    if n <= 0:
        return 0.0, 1.0
    if k <= 0:
        lo = 0.0
    else:
        # Solve P(X >= k | p) = alpha/2. The upper tail INCREASES in p, so if
        # the tail at the midpoint is below target the root is to the RIGHT.
        a, b = 0.0, 1.0
        for _ in range(200):
            m = (a + b) / 2.0
            if _binom_sf(k, n, m) < alpha / 2.0:
                a = m
            else:
                b = m
        lo = (a + b) / 2.0
    if k >= n:
        hi = 1.0
    else:
        # Solve P(X <= k | p) = alpha/2. The lower tail DECREASES in p.
        a, b = 0.0, 1.0
        for _ in range(200):
            m = (a + b) / 2.0
            if _binom_cdf(k, n, m) > alpha / 2.0:
                a = m
            else:
                b = m
        hi = (a + b) / 2.0
    return lo, hi


def independent_reconstruction(
    observations: Iterable[Any],
    *,
    alpha: float = F2_ALPHA,
    confidence: float = F2_CONFIDENCE,
) -> dict[str, Any]:
    """Recompute the confirmatory statistics from raw booleans, independently.

    Returns the same fields ``finalize_f2().to_dict()`` reports (minus the
    free-form observation window). The two must agree; a mismatch is a hard
    blocker (F2 section 21).
    """
    complete, missing = _partition(observations)
    n_total = len(complete) + len(missing)
    n = len(complete)

    b = sum(1 for o in complete if bool(o.t_endpoint) and not bool(o.x_endpoint))
    c = sum(1 for o in complete if (not bool(o.t_endpoint)) and bool(o.x_endpoint))
    both = sum(1 for o in complete if o.t_endpoint and o.x_endpoint)
    neither = sum(1 for o in complete if not o.t_endpoint and not o.x_endpoint)
    d = b + c

    rd = (b - c) / n if n else 0.0

    if d == 0:
        p_value = 1.0
        half = 1.959963985 * math.sqrt(1.0 / (4.0 * n)) if n else 1.0
        ci_low, ci_high = max(-1.0, -half), min(1.0, half)
    else:
        k = max(b, c)
        p_value = min(1.0, 2.0 * _binom_sf(k, d, 0.5))
        p_lo, p_hi = _cp_bounds(b, d, alpha)
        scale = d / n
        ci_low = max(-1.0, (2.0 * p_lo - 1.0) * scale)
        ci_high = min(1.0, (2.0 * p_hi - 1.0) * scale)

    if rd > 0:
        direction = "favours_treatment"
    elif rd < 0:
        direction = "favours_control"
    else:
        direction = "null"

    missing_by_reason: dict[str, int] = {}
    for o in missing:
        key = o.missing_reason or "unspecified"
        missing_by_reason[key] = missing_by_reason.get(key, 0) + 1
    gate = evaluate_infrastructure_gate(n_total, missing_by_reason)

    return {
        "N": n,
        "n_total": n_total,
        "n_missing": len(missing),
        "n00": neither,
        "n01": c,
        "n10": b,
        "n11": both,
        "b": b,
        "c": c,
        "d": d,
        "risk_difference": rd,
        "p_value": p_value,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "direction": direction,
        "alpha": alpha,
        "confidence": confidence,
        "missing_by_reason": missing_by_reason,
        "infrastructure_gate": gate.to_dict(),
    }
