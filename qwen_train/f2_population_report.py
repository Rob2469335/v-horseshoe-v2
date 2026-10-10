"""Reproducible F2 population funnel counts, derived from the build artifacts.

Why this module exists
----------------------
The F2 population funnel (source candidates -> admitted -> analyzable pairs) was
previously only reportable by re-running the whole screen or by parsing the
multi-gigabyte census manifest.  This module recomputes every count **from the
artifacts the build already writes** -- the per-source ``PROVENANCE.json`` files
and the union report -- so a number in a report can always be re-derived instead
of being believed.

The derivation is exact, not sampled:

* ``manifest.summary()["admitted"]`` is ``total_accepted_after_union - len(screen_rejections)``
  because :meth:`qwen_train.f2_population_build.build_union` emits one
  ``screen_rejections`` row for **every** non-admitted entry and none for an
  admitted one, and the manifest enforces one entry per identity.
* An admitted entry has no failing rule, so the per-rule failure counts,
  ``metadata_eligible`` and ``evidence_verified`` are all recoverable from the
  same two numbers.  A completeness assertion fails loudly if the report is
  inconsistent with the accepted total rather than silently undercounting.

Stages 7-12 (paired-observation units) have **no ledger artifact** in this
repository yet, so they are reported as zero with the basis recorded; nothing is
inferred from their absence, and ``NOT ESTABLISHED`` is what a reader must take
from them.

Usage
-----
    python -m qwen_train.f2_population_report
    python -m qwen_train.f2_population_report --json
    python -m qwen_train.f2_population_report --against <older_report.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from qwen_train.f2_readiness import FROZEN_MIN_PAIRS

__all__ = [
    "REPORT_SCHEMA",
    "RULE_S8",
    "NO_LEDGER_BASIS",
    "DEFAULT_UNION_DIR",
    "DEFAULT_PROVENANCE",
    "ReportError",
    "load_union_report",
    "funnel_counts",
    "diff_reports",
    "main",
]

REPORT_SCHEMA = "f2_population_report_v1"
RULE_S8 = "S8_evidence_provenance"
NO_LEDGER_BASIS = "no paired-observation ledger artifact exists (NOT ESTABLISHED)"

DEFAULT_UNION_DIR = Path("data") / "f2_population" / "union"
DEFAULT_PROVENANCE = (
    Path("data") / "f2_population" / "upstream_swe_bench_live_b51a8642" / "PROVENANCE.json",
    Path("data") / "f2_population" / "upstream_swe_rebench_v2_10483de0" / "PROVENANCE.json",
)


class ReportError(ValueError):
    """The artifacts are absent, unreadable, or mutually inconsistent."""


def load_union_report(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ReportError(f"union report not found: {p}")
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"union report is unreadable: {p}: {exc}") from exc
    if not isinstance(record, dict):
        raise ReportError(f"union report is not a JSON object: {p}")
    for key in ("total_raw_rows", "total_accepted_after_union", "screen_rejections"):
        if key not in record:
            raise ReportError(f"union report is missing {key!r}: {p}")
    return record


def _load_provenance(paths: Sequence[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise ReportError(f"provenance not found: {path}")
        try:
            prov = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportError(f"provenance unreadable: {path}: {exc}") from exc
        if not isinstance(prov, dict):
            raise ReportError(f"provenance is not a JSON object: {path}")
        out.append(
            {
                "source_key": prov.get("source_key"),
                "repo_id": prov.get("repo_id"),
                "revision": prov.get("revision"),
                "row_count": int(prov.get("row_count") or 0),
                "distinct_instance_ids": int(prov.get("distinct_instance_ids") or 0),
                "duplicate_instance_ids": list(prov.get("duplicate_instance_ids") or []),
                "normalised_sha256": prov.get("normalised_sha256"),
            }
        )
    return out


def funnel_counts(
    record: Mapping[str, Any],
    *,
    provenance: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Recompute the 12-stage funnel from a union report.

    Raises :class:`ReportError` if the artifacts do not account for every row --
    an inconsistent artifact must never be reported as a number.
    """
    rejected = list(record.get("screen_rejections") or [])
    accepted = int(record.get("total_accepted_after_union") or 0)
    raw = int(record.get("total_raw_rows") or 0)
    exclusions = int(record.get("exclusions") or len(record.get("pre_screen_exclusions") or ()))

    if len(rejected) > accepted:
        raise ReportError(
            f"report lists {len(rejected)} rejected rows for {accepted} accepted rows"
        )
    if raw - exclusions != accepted:
        raise ReportError(
            f"raw {raw} - exclusions {exclusions} != accepted {accepted}"
        )
    upstream = sum(int(p["row_count"]) for p in provenance)
    if provenance and upstream != raw:
        raise ReportError(
            f"PROVENANCE row counts sum to {upstream} but the report says {raw}"
        )
    admitted = accepted - len(rejected)

    failing_rules: dict[str, int] = {}
    failing_rules_by_source: dict[str, dict[str, int]] = {}
    only_s8 = 0
    no_s8 = 0
    by_source: dict[str, int] = {}
    contamination: dict[str, int] = {}
    for row in rejected:
        rules = [str(r) for r in (row.get("failing_rules") or [])]
        src = str(row.get("source") or "")
        for rule in rules:
            failing_rules[rule] = failing_rules.get(rule, 0) + 1
            per_source = failing_rules_by_source.setdefault(src, {})
            per_source[rule] = per_source.get(rule, 0) + 1
        rule_set = set(rules)
        if rule_set == {RULE_S8}:
            only_s8 += 1
        if RULE_S8 not in rule_set:
            no_s8 += 1
        by_source[src] = by_source.get(src, 0) + 1
        cls = str(row.get("contamination_class") or "")
        contamination[cls] = contamination.get(cls, 0) + 1
    failing_rules_by_source = {
        src: dict(sorted(rules.items()))
        for src, rules in sorted(failing_rules_by_source.items())
    }

    # Admitted entries carry no failing rule and are absent from screen_rejections.
    evidence_verified = admitted + no_s8
    metadata_eligible = admitted + only_s8
    analyzable = 0

    # Every accepted row is accounted for: rejected rows carry their rules,
    # admitted rows carry none.  Fail closed if a rejected row claims no rule.
    unclassified = sum(1 for row in rejected if not (row.get("failing_rules") or []))
    if unclassified:
        raise ReportError(
            f"{unclassified} accepted row(s) are neither admitted nor carry a failing rule"
        )

    stages = [
        {"stage": 1, "name": "source_candidates", "count": upstream or raw, "basis": "PROVENANCE row_count (upstream rows)"},
        {"stage": 2, "name": "derived_population", "count": raw - exclusions, "basis": "raw rows minus pre-screen exclusions"},
        {"stage": 3, "name": "unique_identities", "count": accepted, "basis": "accepted rows; manifest enforces one entry per identity"},
        {"stage": 4, "name": "eligible_tasks", "count": metadata_eligible, "basis": "every screen except S8 passes"},
        {"stage": 5, "name": "tasks_with_complete_evidence", "count": evidence_verified, "basis": "S8 evidence verified"},
        {"stage": 6, "name": "admitted_tasks", "count": admitted, "basis": "accepted minus rows listed in screen_rejections"},
        {"stage": 7, "name": "authorized_paired_units", "count": 0, "basis": NO_LEDGER_BASIS},
        {"stage": 8, "name": "started_units", "count": 0, "basis": NO_LEDGER_BASIS},
        {"stage": 9, "name": "completed_units", "count": 0, "basis": NO_LEDGER_BASIS},
        {"stage": 10, "name": "complete_valid_pairs", "count": 0, "basis": NO_LEDGER_BASIS},
        {"stage": 11, "name": "analyzable_pairs", "count": analyzable, "basis": NO_LEDGER_BASIS},
        {"stage": 12, "name": "remaining_shortfall", "count": max(0, FROZEN_MIN_PAIRS - analyzable), "basis": f"FROZEN_MIN_PAIRS = {FROZEN_MIN_PAIRS} minus analyzable pairs"},
    ]

    return {
        "schema": REPORT_SCHEMA,
        "sources": [dict(p) for p in provenance],
        "stages": stages,
        "failing_rules": dict(sorted(failing_rules.items())),
        "failing_rules_by_source": failing_rules_by_source,
        "metadata_eligible_by_source": _eligible_by_source(rejected),
        "rejected_by_source": dict(sorted(by_source.items())),
        "contamination_classes_rejected": dict(sorted(contamination.items())),
        "pre_screen_exclusions": exclusions,
        "frozen_min_pairs": FROZEN_MIN_PAIRS,
        "checks": {
            "raw_equals_accepted_plus_exclusions": True,
            "provenance_row_count_matches_report": not provenance or upstream == raw,
        },
    }


def _eligible_by_source(rejected: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rejected:
        if set(row.get("failing_rules") or ()) == {RULE_S8}:
            src = str(row.get("source") or "")
            out[src] = out.get(src, 0) + 1
    return dict(sorted(out.items()))


def diff_reports(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    """Before/after comparison of two union reports (task-level, not just totals).

    An identity present in one report and absent from the other is reported
    separately (``only_in_before`` / ``only_in_after``) rather than being read as
    a rule change: an absent row may be admitted, excluded upstream, or a
    different population altogether.
    """

    def _index(record: Mapping[str, Any]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for row in record.get("screen_rejections") or []:
            out[str(row.get("instance_id"))] = set(row.get("failing_rules") or ())
        return out

    def _admitted(record: Mapping[str, Any]) -> int:
        accepted = int(record.get("total_accepted_after_union") or 0)
        rejected = list(record.get("screen_rejections") or [])
        return accepted - len(rejected)

    def _rule_counts(record: Mapping[str, Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in record.get("screen_rejections") or []:
            for rule in row.get("failing_rules") or ():
                out[str(rule)] = out.get(str(rule), 0) + 1
        return dict(sorted(out.items()))

    b, a = _index(before), _index(after)
    shared = set(b) & set(a)
    newly_eligible = sorted(i for i in shared if a[i] == {RULE_S8} and b[i] != {RULE_S8})
    newly_rejected = sorted(i for i in shared if a[i] != {RULE_S8} and b[i] == {RULE_S8})
    only_b = sorted(set(b) - set(a))
    only_a = sorted(set(a) - set(b))

    def _eligible(record: Mapping[str, Any], index: Mapping[str, set[str]]) -> int:
        return _admitted(record) + sum(1 for v in index.values() if v == {RULE_S8})

    return {
        "failing_rules_before": _rule_counts(before),
        "failing_rules_after": _rule_counts(after),
        "admitted_before": _admitted(before),
        "admitted_after": _admitted(after),
        "metadata_eligible_before": _eligible(before, b),
        "metadata_eligible_after": _eligible(after, a),
        "newly_eligible": newly_eligible,
        "newly_eligible_count": len(newly_eligible),
        "newly_rejected": newly_rejected,
        "newly_rejected_count": len(newly_rejected),
        "only_in_before": only_b,
        "only_in_before_count": len(only_b),
        "only_in_after": only_a,
        "only_in_after_count": len(only_a),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--union-dir",
        type=Path,
        default=DEFAULT_UNION_DIR,
        help="directory holding union_report.json",
    )
    ap.add_argument("--report", type=Path, default=None, help="explicit union report path")
    ap.add_argument(
        "--provenance",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="per-source PROVENANCE.json (repeatable); defaults to the two acquisitions",
    )
    ap.add_argument("--against", type=Path, default=None, help="an older union report to diff against")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    report_path = args.report or (args.union_dir / "union_report.json")
    try:
        record = load_union_report(report_path)
        prov_paths = args.provenance or list(DEFAULT_PROVENANCE)
        provenance = _load_provenance(prov_paths)
        out = funnel_counts(record, provenance=provenance)
        out["report_path"] = str(report_path)
        out["provenance_paths"] = [str(p) for p in prov_paths]
        if args.against is not None:
            out["diff"] = diff_reports(load_union_report(args.against), record)
    except ReportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    print(f"F2 population funnel  (report: {report_path})")
    for stage in out["stages"]:
        print(f"  {stage['stage']:>2}. {stage['name']:<32} {stage['count']:>8}   {stage['basis']}")
    print("\nfailing rules:")
    for rule, count in out["failing_rules"].items():
        print(f"    {count:>8}  {rule}")
    if out["failing_rules_by_source"]:
        print("\nfailing rules by source:")
        for src, rules in out["failing_rules_by_source"].items():
            for rule, count in rules.items():
                print(f"    {count:>8}  {src}: {rule}")
    print(f"\nmetadata-eligible by source: {out['metadata_eligible_by_source']}")
    diff = out.get("diff")
    if diff:
        print(
            f"\nvs {args.against}: metadata-eligible "
            f"{diff['metadata_eligible_before']} -> {diff['metadata_eligible_after']} "
            f"(+{diff['newly_eligible_count']}, -{diff['newly_rejected_count']})"
        )
        if diff["newly_rejected"]:
            print(f"  newly rejected: {diff['newly_rejected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
