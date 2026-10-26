"""Deterministic feasibility sampling and Wilson score intervals for S8 tasks.

Provides:
* Deterministic, reproducible task sampling from the UNKNOWN population using a
  preregistered seed.
* Wilson score confidence interval calculation for binomial proportions (e.g.
  feasibility / compatibility rates).
* Canonical manifest generation and SHA-256 binding for sample reproducibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import sys
from typing import Any, Iterable, Mapping, Sequence

SAMPLING_SCHEMA = "f2-s8-feasibility-sample/v1"


def wilson_score_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Calculate the Wilson score confidence interval for a binomial proportion.

    Returns ``(lower_bound, point_estimate, upper_bound)`` where all values
    are in ``[0.0, 1.0]``.

    Formula:
        center = (p_hat + z^2 / (2n)) / (1 + z^2 / n)
        half_width = (z / (1 + z^2 / n)) * sqrt(p_hat * (1 - p_hat) / n + z^2 / (4n^2))
        lower = max(0.0, center - half_width)
        upper = min(1.0, center + half_width)
    """
    if not isinstance(total, int) or total <= 0:
        raise ValueError(f"total must be a positive integer, got {total!r}")
    if not isinstance(successes, int) or successes < 0 or successes > total:
        raise ValueError(
            f"successes must be an integer between 0 and total ({total}), got {successes!r}"
        )
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence must be between 0.0 and 1.0, got {confidence!r}")

    # Standard normal critical value z for common confidence levels
    # Supports exact values for common thresholds or accurate approximation
    if abs(confidence - 0.95) < 1e-4:
        z = 1.959963984540054
    elif abs(confidence - 0.90) < 1e-4:
        z = 1.6448536269514722
    elif abs(confidence - 0.99) < 1e-4:
        z = 2.5758293035489004
    else:
        # Approximate normal quantile via inverse error function approximation
        # p = (1 + confidence) / 2
        # Use standard rational approximation for normal quantile
        p_val = (1.0 + confidence) / 2.0
        t = math.sqrt(-2.0 * math.log(1.0 - p_val))
        c0, c1, c2 = 2.515517, 0.802853, 0.010328
        d1, d2, d3 = 1.432788, 0.189269, 0.001308
        z = t - ((c2 * t + c1) * t + c0) / (((d3 * t + d2) * t + d1) * t + 1.0)

    n = float(total)
    k = float(successes)
    p_hat = k / n

    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    radicand = (p_hat * (1.0 - p_hat) / n) + (z2 / (4.0 * n * n))
    half_width = (z * math.sqrt(max(0.0, radicand))) / denom

    if successes == 0:
        lower = 0.0
    else:
        lower = max(0.0, center - half_width)

    if successes == total:
        upper = 1.0
    else:
        upper = min(1.0, center + half_width)

    point = p_hat

    return (lower, point, upper)


def deterministic_sample_tasks(
    task_ids: Sequence[str],
    *,
    sample_size: int = 50,
    seed: str | int = 0,
) -> list[str]:
    """Select a deterministic, uniform pseudo-random sample of task IDs.

    Uses HMAC/SHA-256 keyed hashing of each task ID with the preregistered
    seed. The sample is guaranteed to be:
    * Reproducible across all operating systems and Python versions.
    * Invariant to the order of items in ``task_ids``.
    * Without replacement.

    Returns the sampled task IDs sorted alphabetically.
    """
    if sample_size <= 0:
        raise ValueError(f"sample_size must be positive, got {sample_size!r}")

    unique_sorted = sorted(set(task_ids))
    if len(unique_sorted) <= sample_size:
        return unique_sorted

    seed_str = str(seed).strip()
    prefix = f"f2-s8-feasibility-sample:{seed_str}:"

    # Compute deterministic priority key for each task id
    scored: list[tuple[bytes, str]] = []
    for tid in unique_sorted:
        score = hashlib.sha256(f"{prefix}{tid}".encode("utf-8")).digest()
        scored.append((score, tid))

    scored.sort(key=lambda item: item[0])
    selected = [tid for _, tid in scored[:sample_size]]
    return sorted(selected)


def sample_manifest(
    sampled_task_ids: Sequence[str],
    *,
    seed: str | int,
    population_sha256: str,
    sample_size: int,
) -> dict[str, Any]:
    """Construct a canonical sample manifest."""
    sorted_ids = sorted(set(sampled_task_ids))
    return {
        "schema": SAMPLING_SCHEMA,
        "seed": str(seed),
        "population_sha256": population_sha256,
        "sample_size": sample_size,
        "task_count": len(sorted_ids),
        "task_ids": sorted_ids,
    }


def sample_manifest_digest(manifest: Mapping[str, Any]) -> str:
    """Calculate the canonical SHA-256 digest of a sample manifest."""
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def filter_unknown_population(
    registration_tasks: Sequence[str],
    compat_classes: Mapping[str, str],
    *,
    exclusion_ids: Iterable[str] = (),
    terminal_ids: Iterable[str] = (),
) -> list[str]:
    """Filter registration tasks down to the eligible UNKNOWN population.

    Excludes tasks that:
    1. Are in ``exclusion_ids`` (e.g. EXCLUDE-185).
    2. Are in ``terminal_ids`` (e.g. from historical ledgers).
    3. Have classification != "UNKNOWN" in ``compat_classes``.
    """
    forbidden = set(exclusion_ids) | set(terminal_ids)
    eligible: list[str] = []
    for tid in registration_tasks:
        if tid in forbidden:
            continue
        if compat_classes.get(tid) == "UNKNOWN":
            eligible.append(tid)
    return eligible


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for deterministic feasibility sampling."""
    ap = argparse.ArgumentParser(description="Deterministic S8 Feasibility Sampling Utility")
    ap.add_argument("--seed", default="0", help="Preregistered random seed (int or string)")
    ap.add_argument("--sample-size", type=int, default=50, help="Number of tasks to sample")
    ap.add_argument(
        "--registration-file",
        default=os.environ.get("F2_REGISTRATION_FILE", r"data\f2_population\registration\registered_tasks.json"),
        help="Path to registered tasks JSON",
    )
    ap.add_argument(
        "--compat-report",
        default=os.environ.get("F2_COMPAT_REPORT", r"data\f2_population\plugin_compat\plugin_compat_report.json"),
        help="Path to plugin compatibility report",
    )
    ap.add_argument(
        "--exclusion-policy",
        default=os.environ.get("F2_EXCLUSION_POLICY", ""),
        help="Path to EXCLUDE-185 policy JSON",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Output path for the sample manifest JSON (default: stdout)",
    )
    args = ap.parse_args(argv)

    reg_path = pathlib.Path(args.registration_file)
    if not reg_path.is_file():
        print(f"REGISTRATION_FILE_MISSING: {reg_path}", file=sys.stderr)
        return 1

    compat_path = pathlib.Path(args.compat_report)
    if not compat_path.is_file():
        print(f"COMPAT_REPORT_MISSING: {compat_path}", file=sys.stderr)
        return 1

    try:
        reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
        if isinstance(reg_data, list):
            if all(isinstance(x, str) for x in reg_data):
                reg_tasks = reg_data
            elif all(isinstance(x, dict) and x.get("instance_id") for x in reg_data):
                reg_tasks = [str(x["instance_id"]) for x in reg_data]
            else:
                print("REGISTRATION_UNREADABLE: invalid list format", file=sys.stderr)
                return 1
        else:
            print("REGISTRATION_UNREADABLE: expected list", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"REGISTRATION_ERROR: {exc}", file=sys.stderr)
        return 1

    from qwen_train.f2_s8_v2_gate import _compat_classes_from_report

    try:
        compat_raw = json.loads(compat_path.read_text(encoding="utf-8"))
        compat_classes = _compat_classes_from_report(compat_raw)
    except Exception as exc:
        print(f"COMPAT_REPORT_ERROR: {exc}", file=sys.stderr)
        return 1

    exclusion_ids: set[str] = set()
    if args.exclusion_policy:
        from qwen_train.f2_s8_candidate_pool import load_exclusion_policy

        try:
            _, policy_ids, _ = load_exclusion_policy(args.exclusion_policy)
            exclusion_ids = policy_ids
        except Exception as exc:
            print(f"EXCLUSION_POLICY_ERROR: {exc}", file=sys.stderr)
            return 1

    # Check conflicts declared in compat report as well
    raw_conflicts = compat_raw.get("resolved_by_source_priority") or []
    if isinstance(raw_conflicts, list):
        exclusion_ids |= {str(x) for x in raw_conflicts}

    eligible_unknown = filter_unknown_population(
        reg_tasks, compat_classes, exclusion_ids=exclusion_ids
    )

    pop_bytes = json.dumps(eligible_unknown, sort_keys=True).encode("utf-8")
    pop_sha = hashlib.sha256(pop_bytes).hexdigest()

    sampled = deterministic_sample_tasks(
        eligible_unknown, sample_size=args.sample_size, seed=args.seed
    )

    manifest = sample_manifest(
        sampled,
        seed=args.seed,
        population_sha256=pop_sha,
        sample_size=args.sample_size,
    )
    manifest_digest = sample_manifest_digest(manifest)
    manifest["manifest_sha256"] = manifest_digest

    output_text = json.dumps(manifest, indent=2, ensure_ascii=False)
    if args.output:
        out_path = pathlib.Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
        print(f"Wrote {len(sampled)} sampled tasks to {out_path} (digest: {manifest_digest})")
    else:
        print(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
