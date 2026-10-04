"""No-lesson X/C0 calibration for Experiment J F2 (R13).

Why calibration must precede confirmatory collection
----------------------------------------------------
Two quantities decide whether the confirmatory design is even feasible, and NEITHER
can be obtained from the literature or from F1:

1. **The X-arm base rate at k = 12.** F1's own pilot reached the qualifying
   endpoint in 10 of 10 VALID no-lesson observations, all at ATIF step 4. If the
   X arm is similarly saturated, the binary primary endpoint has ZERO headroom: a
   positive risk difference is impossible at any N, and the confirmatory test is
   structurally dead regardless of power. This is a hypothesis to be measured, not
   assumed.

2. **The empirical discordance (pi_d).** Required N for an exact paired test
   depends directly on pi_d and swings by roughly 3x across its plausible range
   (see ``f2_statistics.required_pairs``). It is measurable only from paired T/X
   runs, and the closest available proxy is a paired no-lesson replicate
   measurement.

Calibration is therefore the cheapest high-value experiment available, and it
requires no lesson, no ACTIVE transition, no promotion and no receipt key.

Scientific constraints this module enforces
------------------------------------------
* **No lesson contamination.** A record whose arm is anything other than X or C0
  is refused. A calibration observation that had a lesson delivered is not
  calibration.
* **No reruns on outcome.** Reruns are permitted ONLY for a named, predefined
  infrastructure cause, never because a result was undesirable (D-11).
* **Immutability.** Records are frozen and content-hashed; a record cannot be
  edited after the fact.
* **Same measurement membrane.** Every record must declare the same endpoint
  specification hash and horizon, so calibration and confirmatory data are
  measured identically.

This module is pure analysis over records. It never spawns an arm, contacts a
model, or mutates any store.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

__all__ = [
    "CalibrationError",
    "INFRASTRUCTURE_RERUN_CAUSES",
    "CALIBRATION_ARMS",
    "CalibrationRecord",
    "CalibrationSummary",
    "summarize_calibration",
]

#: The only arms a calibration observation may carry. X = frozen artifact with L
#: removed; C0 = no artifact at all. Both are lesson-free by construction.
CALIBRATION_ARMS = ("X", "C0")

#: The ONLY reasons a calibration run may be repeated. Deliberately a closed set:
#: an open-ended "flaky" category is how outcome-dependent reruns enter by the
#: back door. Each maps to an infrastructure fault, never to an undesired score.
INFRASTRUCTURE_RERUN_CAUSES = frozenset(
    {
        "backend_unreachable",
        "model_endpoint_unreachable",
        "workspace_preparation_failed",
        "trajectory_malformed",
        "process_crash",
        "port_occupied_by_foreign_process",
        "qdrant_unavailable",
        "infrastructure_timeout",
    }
)


class CalibrationError(ValueError):
    """Raised when a calibration record or summary violates its contract."""


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class CalibrationRecord:
    """One frozen no-lesson observation."""

    instance_id: str
    arm: str
    rollout_id: str
    endpoint_hash: str
    horizon_steps: int
    endpoint_observed: bool
    first_edit_step: int | None
    valid: bool
    invalid_reason: str = ""
    rerun_of: str = ""
    rerun_cause: str = ""
    task_id: str = ""

    def __post_init__(self) -> None:
        if self.arm not in CALIBRATION_ARMS:
            raise CalibrationError(
                f"calibration arm must be one of {CALIBRATION_ARMS}, got {self.arm!r}. "
                "A lesson-bearing arm is not calibration: it would contaminate the "
                "no-lesson baseline this measurement exists to establish."
            )
        if not str(self.instance_id or "").strip():
            raise CalibrationError("calibration record requires an instance_id")
        if not str(self.rollout_id or "").strip():
            raise CalibrationError("calibration record requires a rollout_id")
        if not str(self.endpoint_hash or "").strip():
            raise CalibrationError(
                "calibration record requires the endpoint specification hash so it "
                "is measured by the same detector as confirmatory data"
            )
        if not self.valid and not str(self.invalid_reason or "").strip():
            raise CalibrationError(
                "an invalid calibration record must state invalid_reason; a silent "
                "invald would become missing data"
            )
        if self.rerun_of and self.rerun_cause not in INFRASTRUCTURE_RERUN_CAUSES:
            raise CalibrationError(
                f"rerun cause {self.rerun_cause!r} is not a predefined "
                "infrastructure cause. Outcome-dependent reruns are forbidden."
            )
        if self.endpoint_observed and self.first_edit_step is None:
            raise CalibrationError(
                "endpoint_observed=True requires first_edit_step"
            )
        if not self.endpoint_observed and self.first_edit_step is not None:
            raise CalibrationError(
                "endpoint_observed=False must not carry a first_edit_step"
            )

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["record_hash"] = hashlib.sha256(_canon(d).encode("utf-8")).hexdigest()
        return d


@dataclass(frozen=True)
class CalibrationSummary:
    """Every quantity the confirmatory design depends on, computed not guessed."""

    n_records: int
    n_valid: int
    n_invalid: int
    endpoint_rate: float | None
    censoring_rate: float | None
    invalid_rate: float
    no_edit_rate: float | None
    first_edit_steps: tuple[int, ...]
    task_heterogeneity: dict[str, Any]
    per_task_rates: dict[str, float | None]
    invalid_causes: dict[str, int]
    rerun_causes: dict[str, int]
    saturation_flag: bool | None
    saturation_detail: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def summarize_calibration(
    records: Iterable[CalibrationRecord],
    *,
    endpoint_hash: str = "",
    saturation_threshold: float = 0.95,
) -> CalibrationSummary:
    """Summarize calibration records into the confirmatory design inputs.

    ``saturation_flag`` is the decision-relevant output: when the endpoint rate
    among valid observations is at or above ``saturation_threshold`` the binary
    primary endpoint has essentially no headroom and the confirmatory design must
    reconsider its endpoint (F0 section 5 is protected, so that is a scientific
    review, not an implementation change).
    """
    recs = list(records)
    if not recs:
        raise CalibrationError("no calibration records supplied")

    hashes = {r.endpoint_hash for r in recs}
    if len(hashes) > 1:
        raise CalibrationError(
            "calibration records were measured by DIFFERENT endpoint "
            f"specifications {sorted(hashes)}; they are not comparable"
        )
    if endpoint_hash and hashes and endpoint_hash not in hashes:
        raise CalibrationError(
            f"calibration endpoint hash {endpoint_hash!r} does not match the "
            f"records' {sorted(hashes)[0]!r}"
        )

    seen_rollouts = [r.rollout_id for r in recs]
    dupes = {x for x in seen_rollouts if seen_rollouts.count(x) > 1}
    if dupes:
        raise CalibrationError(f"duplicate rollout_id in calibration: {sorted(dupes)}")

    valid = [r for r in recs if r.valid]
    invalid = [r for r in recs if not r.valid]
    n = len(recs)

    endpoint_obs = [r for r in valid if r.endpoint_observed]
    endpoint_rate = (len(endpoint_obs) / len(valid)) if valid else None
    censoring_rate = (
        (len(valid) - len(endpoint_obs)) / len(valid) if valid else None
    )
    invalid_rate = len(invalid) / n

    steps = tuple(
        sorted(r.first_edit_step for r in endpoint_obs if r.first_edit_step is not None)
    )

    # Task heterogeneity: per-task endpoint rate, plus the spread. A single
    # aggregate rate can hide that some tasks are trivially saturated and others
    # never reach the endpoint, which is exactly the situation that makes a fixed
    # horizon uninformative.
    per_task: dict[str, list[bool]] = {}
    for r in valid:
        per_task.setdefault(r.instance_id, []).append(r.endpoint_observed)
    per_task_rates = {
        t: (sum(v) / len(v) if v else None) for t, v in sorted(per_task.items())
    }
    observed_rates = [v for v in per_task_rates.values() if v is not None]
    if observed_rates:
        mean_rate = sum(observed_rates) / len(observed_rates)
        var = (
            sum((x - mean_rate) ** 2 for x in observed_rates) / len(observed_rates)
        )
        heterogeneity = {
            "n_tasks_observed": len(observed_rates),
            "mean_task_rate": mean_rate,
            "sd_task_rate": var**0.5,
            "min_task_rate": min(observed_rates),
            "max_task_rate": max(observed_rates),
            "tasks_fully_saturated": sum(1 for x in observed_rates if x >= 1.0),
            "tasks_never_saturated": sum(1 for x in observed_rates if x <= 0.0),
        }
    else:
        heterogeneity = {"n_tasks_observed": 0}

    invalid_causes: dict[str, int] = {}
    for r in invalid:
        key = r.invalid_reason or "unspecified"
        invalid_causes[key] = invalid_causes.get(key, 0) + 1
    rerun_causes: dict[str, int] = {}
    for r in recs:
        if r.rerun_cause:
            rerun_causes[r.rerun_cause] = rerun_causes.get(r.rerun_cause, 0) + 1

    # No-edit rate: valid observations that produced NO filesystem action of any
    # kind within the horizon (distinct from "edited, but not a relevant file").
    no_edit = [r for r in valid if not r.endpoint_observed and r.first_edit_step is None]
    no_edit_rate = (len(no_edit) / len(valid)) if valid else None

    flag: bool | None = None
    detail = "insufficient valid observations to assess saturation"
    if endpoint_rate is not None and len(valid) >= 3:
        flag = endpoint_rate >= saturation_threshold
        if flag:
            detail = (
                f"endpoint rate {endpoint_rate:.3f} >= {saturation_threshold}: the "
                "binary primary endpoint has essentially no headroom at this "
                "horizon. A confirmatory positive risk difference is not "
                "attainable. Reconsider the endpoint under a scientific review "
                "(F0 section 5 is protected)."
            )
        else:
            detail = (
                f"endpoint rate {endpoint_rate:.3f} < {saturation_threshold}: "
                "headroom exists."
            )

    return CalibrationSummary(
        n_records=n,
        n_valid=len(valid),
        n_invalid=len(invalid),
        endpoint_rate=endpoint_rate,
        censoring_rate=censoring_rate,
        invalid_rate=invalid_rate,
        no_edit_rate=no_edit_rate,
        first_edit_steps=steps,
        task_heterogeneity=heterogeneity,
        per_task_rates=per_task_rates,
        invalid_causes=dict(sorted(invalid_causes.items())),
        rerun_causes=dict(sorted(rerun_causes.items())),
        saturation_flag=flag,
        saturation_detail=detail,
    )