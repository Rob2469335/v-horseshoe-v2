"""Build a reproducible F2 population manifest + census from an acquired snapshot.

F2-IMPL-AUTH-029.  Deterministic, offline (no network, no execution): it reads an
``acquired.jsonl`` produced by :mod:`qwen_train.f2_population_acquire`, maps the
upstream fields to the screening contract, derives R8 from the reference fix
(:mod:`qwen_train.f2_relevant_files`), de-duplicates, screens, and writes a census
plus a rejection record.

Usage
-----
    python -m qwen_train.f2_population_build \\
        --acquired data/f2_population/upstream_swe_bench_live_b51a8642/acquired.jsonl \\
        --out      data/f2_population/upstream_swe_bench_live_b51a8642/census.json \\
        --cutoff   2024-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from qwen_train.f2_population import (
    contamination_policy_record,
    deduplicate_rows,
    screen_pool_rows,
)
from qwen_train.f2_relevant_files import relevant_file_set_from_patch

#: The contamination proxy declared under F2-IMPL-AUTH-029.  A temporal cutoff is a
#: PROXY: the served model's training cutoff is NOT ESTABLISHED, so this can only
#: bound *freshness*, never prove absence from pre-training.
DEFAULT_CUTOFF = "2024-01-01"
CUTOFF_BASIS = (
    "SWE-bench-Live declared freshness-window start (dataset anchor). "
    "The served model's training cutoff is NOT ESTABLISHED; this is a PROXY."
)


def _as_test_cmd(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " && ".join(str(v) for v in value if str(v).strip())
    return str(value)


def to_screen_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map an acquired upstream row to the screening contract's row shape."""
    return {
        "instance_id": str(row.get("instance_id") or ""),
        "repo": str(row.get("repo") or ""),
        "base_commit": str(row.get("base_commit") or ""),
        "test_cmd": _as_test_cmd(row.get("test_cmds") or row.get("test_cmd")),
        "fail_to_pass": list(row.get("FAIL_TO_PASS") or row.get("fail_to_pass") or []),
        "pass_to_pass": list(row.get("PASS_TO_PASS") or row.get("pass_to_pass") or []),
        "created_at": str(row.get("created_at") or ""),
        # The upstream split is the probe that discovered the task.
        "usable": True,
        "_patch": str(row.get("patch") or ""),
    }


def build(
    acquired_path: Path,
    *,
    cutoff: str = DEFAULT_CUTOFF,
) -> tuple[Any, dict[str, Any]]:
    """Return ``(manifest, rejections_record)``. Pure except for reading ``acquired_path``."""
    raw_rows = [
        json.loads(line)
        for line in Path(acquired_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    screen_rows = [to_screen_row(r) for r in raw_rows]

    # D5: deterministic de-duplication BEFORE screening.
    kept_rows, dedupe_rejections = deduplicate_rows(screen_rows)

    # R8: derive the relevant set from each row's reference fix.
    relevant: dict[str, list[str]] = {}
    r8_missing: list[dict[str, Any]] = []
    for row in kept_rows:
        iid = row["instance_id"]
        if not iid:
            continue
        rfs = list(relevant_file_set_from_patch(row.pop("_patch", "")))
        relevant[iid] = rfs
        if not rfs:
            r8_missing.append(
                {
                    "instance_id": iid,
                    "reason": "no_non_test_source_file_in_reference_fix",
                    "detail": "S6 will reject: the reference fix touches no source file",
                }
            )
    for row in kept_rows:
        row.pop("_patch", None)

    manifest = screen_pool_rows(
        kept_rows,
        relevant_file_sets=relevant,
        model_cutoff=cutoff,
        rejections=dedupe_rejections,
        contamination_policy=contamination_policy_record(cutoff, basis=CUTOFF_BASIS),
    )
    manifest.verify()

    screen_rejections = []
    for e in manifest.entries:
        if e.admitted:
            continue
        screen_rejections.append(
            {
                "instance_id": e.instance_id,
                "failing_rules": [s.rule for s in e.screens if not s.passed],
                "contamination_class": e.contamination_class,
            }
        )
    rejections_record = {
        "pre_screen_exclusions": dedupe_rejections,
        "r8_no_source_file": r8_missing,
        "screen_rejections": screen_rejections,
    }
    return manifest, rejections_record


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--acquired", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rejections-out", type=Path, default=None)
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    args = ap.parse_args(argv)

    manifest, rejections = build(args.acquired, cutoff=args.cutoff)
    args.out.write_text(manifest.to_json(indent=2) + "\n", encoding="utf-8")
    if args.rejections_out:
        args.rejections_out.write_text(
            json.dumps(rejections, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    summary = {
        k: v
        for k, v in manifest.summary().items()
        if k not in ("entries", "rejections")
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
