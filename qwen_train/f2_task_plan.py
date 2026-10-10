"""Deterministic, auditable pre-registration ordering for the F2 population.

What this is
------------
A **planning** module.  It turns the pinned population artifacts into a stable,
reproducible task order and a plan-only view of position N.  It reads task
metadata as data and performs no task execution of any kind: no clone, no
checkout, no dependency install, no shell command from task metadata, no VM, no
model, no treatment/control evidence, and no admission decision.

Ordering rule (``created_at_desc_instance_id_asc_v1``)
------------------------------------------------------
1. ``created_at`` descending (most recent first);
2. ``instance_id`` ascending as the deterministic tie-breaker.

Records whose ``created_at`` is missing or unparseable are **kept out of the
ordered list** and reported separately with a reason code; no date is invented
for them.  The rule is applied only after inspection confirmed that no
governing document already prescribes a population ordering — none does.

Reproducibility
---------------
Everything except ``generated_at`` is content-addressed: the ordered id list,
the excluded list, the rule id and the pinned input digests are hashed together
into ``content_sha256``.  Two runs over identical inputs produce identical
content digests; changing any substantive input changes it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from qwen_train.f2_population import parse_timestamp, TimestampError

__all__ = [
    "ORDERING_RULE_ID",
    "SCHEMA_VERSION",
    "PlanError",
    "static_os_plausibility",
    "order_tasks",
    "registration_artifacts",
    "plan_for_position",
    "load_eligible_created_at",
    "main",
]

SCHEMA_VERSION = "f2_task_order_v1"
ORDERING_RULE_ID = "created_at_desc_instance_id_asc_v1"

#: Markers are static text signals in ``test_cmd``.  They classify
#: *plausibility only*; they never prove a task runs on an OS, and no command
#: is ever invoked.
_WINDOWS_MARKERS = (
    r"\bpowershell\b", r"\bpwsh\b", r"\bcmd\s*/c\b", r"\bfindstr\b",
    r"\.ps1\b", r"\bwhere\s+[\w.-]+\b", r"\bpy\s+-3\b", r"\.bat\b",
    r"\.cmd\b", r"\bchoco\b", r"\\\\",
)
_POSIX_MARKERS = (
    r"\bbash\b", r"\bsh\s+-c\b", r"^#!", r"\bapt(-get)?\b", r"\byum\b",
    r"\bdnf\b", r"\bchmod\b", r"\./[\w-]+\.(sh|bash|zsh|py|pl|rb)",
    r"\bset\s+-e\b", r"^\s*export\s", r"\bsource\s", r"\bmake\b",
    r"\bcmake\b", r"\btox\b", r"\bnox\b", r"\blinux\b",
)
_WIN_RE = tuple(re.compile(p, re.IGNORECASE) for p in _WINDOWS_MARKERS)
_POS_RE = tuple(re.compile(p, re.IGNORECASE) for p in _POSIX_MARKERS)

OS_LABELS = (
    "windows_plausible",
    "linux_plausible",
    "both_plausible",
    "ambiguous",
    "not_classifiable",
)


class PlanError(ValueError):
    """The inputs are absent, malformed, or violate a planning invariant."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def static_os_plausibility(test_cmd: Any) -> str:
    """Static plausibility label for a task's declared test command.

    Pure string inspection.  The command is **never** executed, expanded, or
    passed to a shell; a label is a hypothesis about syntax, not a result.
    """
    text = str(test_cmd or "").strip()
    if not text:
        return "not_classifiable"
    win = any(r.search(text) for r in _WIN_RE)
    pos = any(r.search(text) for r in _POS_RE)
    if win and pos:
        return "ambiguous"
    if win:
        return "windows_plausible"
    if pos:
        return "linux_plausible"
    return "both_plausible"


def _created_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return parse_timestamp(text)
    except TimestampError:
        return None


def order_tasks(
    rows: Iterable[Mapping[str, Any]], *, rule: str = ORDERING_RULE_ID
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(ordered, excluded)`` under the registered ordering rule.

    ``ordered`` is sorted by ``created_at`` DESC then ``instance_id`` ASC.
    ``excluded`` carries every row that cannot be ordered, with a reason code
    (``missing_created_at`` / ``invalid_created_at`` / ``missing_instance_id``)
    so nothing silently disappears.
    """
    if rule != ORDERING_RULE_ID:
        raise PlanError(f"unknown ordering rule {rule!r}")

    ordered: list[tuple[datetime, str, dict[str, Any]]] = []
    excluded: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        iid = str(row.get("instance_id") or "").strip()
        if not iid:
            excluded.append(
                {"instance_id": "", "reason": "missing_instance_id", "created_at": row.get("created_at")}
            )
            continue
        raw_created = row.get("created_at")
        if raw_created is None or not str(raw_created).strip():
            excluded.append(
                {
                    "instance_id": iid,
                    "reason": "missing_created_at",
                    "created_at": raw_created,
                }
            )
            continue
        dt = _created_dt(raw_created)
        if dt is None:
            excluded.append(
                {
                    "instance_id": iid,
                    "reason": "invalid_created_at",
                    "created_at": str(raw_created),
                }
            )
            continue
        kept = {
            "instance_id": iid,
            "created_at": dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": str(row.get("source") or ""),
            "language": str(row.get("language") or ""),
            "repo": str(row.get("repo") or ""),
            "test_cmd_sha256": _sha256_text(str(row.get("test_cmd") or "")),
            "test_cmd_present": bool(str(row.get("test_cmd") or "").strip()),
            "os_plausibility": static_os_plausibility(row.get("test_cmd")),
        }
        ordered.append((dt, iid, kept))

    ordered.sort(key=lambda t: (-t[0].timestamp(), t[1]))
    excluded.sort(key=lambda e: (e["reason"], e["instance_id"]))
    return [item[2] for item in ordered], excluded


def registration_artifacts(
    ordered: Sequence[Mapping[str, Any]],
    excluded: Sequence[Mapping[str, Any]],
    *,
    inputs: Mapping[str, str],
    top_n: int = 30,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the pre-registration artifact set with content digests.

    ``inputs`` maps a pinned input's name to its SHA-256.  ``generated_at`` is
    the only non-deterministic field and is excluded from ``content_sha256``.
    """
    if top_n < 1:
        raise PlanError("top_n must be >= 1")
    ids = [str(r["instance_id"]) for r in ordered]
    if len(ids) != len(set(ids)):
        raise PlanError("ordered list contains duplicate instance_ids")

    def _digest(obj: Any) -> str:
        return _sha256_text(
            json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )

    ordered_rows = [dict(r) for r in ordered]
    excluded_rows = [dict(e) for e in excluded]
    # The content digest covers the ordered METADATA, not just the ids, so a
    # changed row (language, repo, created_at, test-command digest) changes it.
    content = {
        "schema_version": SCHEMA_VERSION,
        "ordering_rule": ORDERING_RULE_ID,
        "top_n": top_n,
        "inputs": dict(inputs),
        "ordered_instance_ids": ids,
        "ordered_rows_sha256": _digest(ordered_rows),
        "excluded": excluded_rows,
    }
    content_sha = _sha256_text(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )

    first30 = [dict(r) for r in ordered_rows[:top_n]]
    rule_manifest = {
        "schema_version": SCHEMA_VERSION,
        "ordering_rule": ORDERING_RULE_ID,
        "rule": [
            "created_at descending",
            "instance_id ascending (tie-break)",
        ],
        "missing_or_invalid_created_at": "excluded from the ordered list and reported separately; no date is invented",
        "denominator": len(ordered),
        "excluded_count": len(excluded),
        "top_n": top_n,
        "content_sha256": content_sha,
    }
    source_manifest = {
        "schema_version": SCHEMA_VERSION,
        "pinned_inputs": dict(inputs),
        "content_sha256": content_sha,
    }
    return {
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "content_sha256": content_sha,
        "source_manifest": source_manifest,
        "source_manifest_sha256": _digest(source_manifest),
        "ordering_rule_manifest": rule_manifest,
        "ordering_rule_manifest_sha256": _digest(rule_manifest),
        "first_30": first30,
        "first_30_sha256": _digest(first30),
        "full_ordered_metadata": [dict(r) for r in ordered],
        "full_ordered_metadata_sha256": _digest([dict(r) for r in ordered]),
        "excluded": [dict(e) for e in excluded],
        "excluded_sha256": _digest([dict(e) for e in excluded]),
        "counts": {
            "ordered": len(ordered),
            "excluded": len(excluded),
            "first_30": len(first30),
        },
    }


def plan_for_position(
    ordered: Sequence[Mapping[str, Any]], n: int, *, inputs: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Plan-only view of position ``n`` (1-based).

    Returns static metadata and hashes.  Deliberately **cannot**: clone a
    repository, download task code, install dependencies, run any command from
    task metadata, launch a VM, run tests, produce T/X evidence, or mark a task
    admitted or analyzable — none of those operations exist in this module.
    """
    if n < 1 or n > len(ordered):
        raise PlanError(f"position {n} is outside 1..{len(ordered)}")
    row = dict(ordered[n - 1])
    body = {
        "schema_version": SCHEMA_VERSION,
        "ordering_rule": ORDERING_RULE_ID,
        "position": n,
        "task_id": row.get("instance_id"),
        "source": row.get("source"),
        "repo": row.get("repo"),
        "created_at": row.get("created_at"),
        "language": row.get("language"),
        "test_cmd_sha256": row.get("test_cmd_sha256"),
        "test_cmd_present": row.get("test_cmd_present"),
        "os_plausibility": row.get("os_plausibility"),
        "pinned_inputs": dict(inputs or {}),
        "operations_performed": [
            "read pinned metadata",
            "sort by the registered ordering rule",
            "hash the row",
        ],
        "operations_not_performed": [
            "clone_repository",
            "download_task_code",
            "install_dependencies",
            "run_task_command",
            "launch_vm",
            "run_tests",
            "produce_treatment_or_control_evidence",
            "mark_admitted",
            "mark_analyzable",
        ],
        "admission_state": "not_established",
        "execution_state": "not_established",
    }
    body["plan_sha256"] = _sha256_text(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    return body


def load_eligible_created_at(
    *,
    union_report: Path,
    acquired_paths: Sequence[Path],
    eligible_ids: Sequence[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Read ``created_at``/``test_cmd``/``language`` for eligible identities.

    Streams the pinned acquisitions as data.  No task command is executed;
    ``test_cmd`` is captured only to be hashed and statically classified.
    """
    from qwen_train.f2_population_build import source_to_screen_row  # local import

    wanted = set(eligible_ids) if eligible_ids is not None else None
    out: dict[str, dict[str, str]] = {}
    for path in acquired_paths:
        path = Path(path)
        source_key = "swe-bench-live" if "live" in path.parent.name else "swe-rebench-v2"
        prov = path.parent / "PROVENANCE.json"
        if prov.is_file():
            source_key = str(json.loads(prov.read_text(encoding="utf-8")).get("source_key") or source_key)
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                iid = str(row.get("instance_id") or "")
                if not iid or (wanted is not None and iid not in wanted) or iid in out:
                    continue
                mapped = source_to_screen_row(source_key, row)
                out[iid] = {
                    "created_at": mapped.get("created_at") or "",
                    "test_cmd": mapped.get("test_cmd") or "",
                    "language": mapped.get("language") or "",
                    "repo": mapped.get("repo") or "",
                    "source": source_key,
                }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--union-report", type=Path, required=True)
    ap.add_argument("--acquired", type=Path, action="append", default=[], required=True)
    ap.add_argument("--out", type=Path, required=True, help="directory for the artifact set")
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--plan", type=int, default=None, help="print the plan-only view for position N")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        report = json.loads(Path(args.union_report).read_text(encoding="utf-8"))
        s8 = "S8_evidence_provenance"
        eligible = [
            r["instance_id"]
            for r in report.get("screen_rejections") or []
            if set(r.get("failing_rules") or ()) == {s8}
        ]
        if not eligible:
            raise PlanError("no metadata-eligible identities in the union report")
        meta = load_eligible_created_at(
            union_report=Path(args.union_report),
            acquired_paths=[Path(p) for p in args.acquired],
            eligible_ids=eligible,
        )
        rows = [
            {"instance_id": iid, **meta.get(iid, {"created_at": "", "test_cmd": "", "language": "", "repo": "", "source": ""})}
            for iid in eligible
        ]
        ordered, excluded = order_tasks(rows)
        inputs = {"union_report_sha256": _sha256_file(Path(args.union_report))}
        for p in args.acquired:
            inputs[f"{Path(p).parent.name}_acquired_sha256"] = _sha256_file(Path(p))
        arts = registration_artifacts(ordered, excluded, inputs=inputs, top_n=args.top_n)
    except (PlanError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.plan is not None:
        view = plan_for_position(ordered, args.plan, inputs=inputs)
        print(json.dumps(view, indent=2, sort_keys=True))
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, key in (
        ("first_30.json", "first_30"),
        ("full_ordered_metadata.json", "full_ordered_metadata"),
        ("source_manifest.json", "source_manifest"),
        ("ordering_rule_manifest.json", "ordering_rule_manifest"),
        ("excluded.json", "excluded"),
    ):
        target = out_dir / name
        body = json.dumps(arts[key], indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        target.write_text(body, encoding="utf-8", newline="\n")
        written[name] = {"sha256": _sha256_text(body), "path": str(target)}

    summary = {
        "schema_version": SCHEMA_VERSION,
        "ordering_rule": ORDERING_RULE_ID,
        "generated_at": arts["generated_at"],
        "content_sha256": arts["content_sha256"],
        "counts": arts["counts"],
        "pinned_inputs": inputs,
        "artifacts": written,
        "label": "pre-registration ordering of metadata-eligible tasks; not an admission, "
        "not a scientific-cleanliness claim, and not authorization to execute anything",
    }
    (out_dir / "registration_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True) if args.json else json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
