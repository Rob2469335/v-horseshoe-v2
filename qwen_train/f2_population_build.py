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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from qwen_train.f2_population import (
    contamination_policy_record,
    deduplicate_rows,
    screen_pool_rows,
)
from qwen_train.f2_relevant_files import relevant_file_set_from_patch

#: Preference order when the SAME canonical identity is supplied by more than one
#: source with an identical payload. Lower index wins. Deterministic, not ad hoc.
SOURCE_PRIORITY: tuple[str, ...] = ("swe-bench-live", "swe-rebench-v2")


def _source_rank(source_key: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source_key)
    except ValueError:
        return len(SOURCE_PRIORITY)

#: The contamination proxy declared under F2-IMPL-AUTH-029.  A temporal cutoff is a
#: PROXY: the served model's training cutoff is NOT ESTABLISHED, so this can only
#: bound *freshness*, never prove absence from pre-training.
DEFAULT_CUTOFF = "2024-01-01"
CUTOFF_BASIS = (
    "SWE-bench-Live declared freshness-window start (dataset anchor). "
    "The served model's training cutoff is NOT ESTABLISHED; this is a PROXY."
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file without ``str.splitlines()``.

    ``str.splitlines()`` splits on Unicode line boundaries (U+2028, U+2029, NEL,
    form feed, ...), which occur inside JSON strings and tear a record in half --
    observed as ``JSONDecodeError: Unterminated string`` on the real
    SWE-rebench-V2 acquisition. JSONL records are separated by ``\\n`` ONLY.
    """
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def _as_test_cmd(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " && ".join(str(v) for v in value if str(v).strip())
    return str(value)


def to_screen_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map an acquired SWE-bench-Live row to the screening contract's row shape."""
    return source_to_screen_row("swe-bench-live", row)


def _coerce_created_at(value: Any) -> str:
    """Normalise a source's ``created_at`` to extended ISO-8601 (UTC).

    Sources differ: SWE-bench-Live publishes an ISO timestamp; SWE-rebench-V2
    publishes ``"YYYY-MM-DD HH:MM:SS"`` (UTC). An epoch-milliseconds integer/string
    is also accepted (the schema has been observed both ways). The value is
    preserved faithfully; nothing is inferred.
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # bool is an int subclass; never a timestamp
        return str(value)
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().isdigit()
    ):
        try:
            ms = int(value)
        except (TypeError, ValueError):
            return str(value).strip()
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    return _normalise_space_datetime(str(value).strip())


def _normalise_space_datetime(text: str) -> str:
    """Turn ``"YYYY-MM-DD HH:MM:SS"`` into the extended ISO ``"YYYY-MM-DDTHH:MM:SS"``."""
    if len(text) > 10 and text[10] == " ":
        return text[:10] + "T" + text[11:]
    return text


def _test_cmd_for(source_key: str, row: Mapping[str, Any]) -> str:
    """The declared test command, read from the source's actual field."""
    if source_key == "swe-rebench-v2":
        ic = row.get("install_config")
        if isinstance(ic, Mapping):
            return _as_test_cmd(ic.get("test_cmd"))
        return ""
    return _as_test_cmd(row.get("test_cmds") or row.get("test_cmd"))


def source_to_screen_row(source_key: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Faithfully map an acquired row from ``source_key`` to the canonical schema.

    Required fields keep their original meaning. ``FAIL_TO_PASS``/``PASS_TO_PASS``
    are copied verbatim: for SWE-bench-Live these are pytest node ids, for
    SWE-rebench-V2 they are the source's log-parser outcome identifiers. The
    identifier kind is preserved (not reinterpreted) and the source key is carried
    so the difference is auditable.
    """
    return {
        "instance_id": str(row.get("instance_id") or ""),
        "repo": str(row.get("repo") or ""),
        "base_commit": str(row.get("base_commit") or ""),
        "test_cmd": _test_cmd_for(source_key, row),
        "fail_to_pass": list(row.get("FAIL_TO_PASS") or row.get("fail_to_pass") or []),
        "pass_to_pass": list(row.get("PASS_TO_PASS") or row.get("pass_to_pass") or []),
        "created_at": _coerce_created_at(row.get("created_at")),
        "language": str(row.get("language") or ""),
        # The upstream split is the probe that discovered the task.
        "usable": True,
        "_patch": str(row.get("patch") or ""),
        "_source": source_key,
    }


def _payload_key(row: Mapping[str, Any]) -> str:
    """Canonical payload identity used to detect CONFLICTING duplicates.

    Excludes annotations and the reference patch (a different patch for the same
    identity is a conflict, but the patch's hash is compared separately).
    """
    payload = {
        "repo": row.get("repo"),
        "base_commit": row.get("base_commit"),
        "test_cmd": row.get("test_cmd"),
        "fail_to_pass": list(row.get("fail_to_pass") or []),
        "pass_to_pass": list(row.get("pass_to_pass") or []),
        "created_at": row.get("created_at"),
        "patch": row.get("_patch"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def build(
    acquired_path: Path,
    *,
    cutoff: str = DEFAULT_CUTOFF,
) -> tuple[Any, dict[str, Any]]:
    """Return ``(manifest, rejections_record)``. Pure except for reading ``acquired_path``."""
    raw_rows = _read_jsonl(acquired_path)
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


def build_union(
    source_paths: Sequence[tuple[str, Path]],
    *,
    cutoff: str = DEFAULT_CUTOFF,
) -> tuple[Any, dict[str, Any]]:
    """Union several acquired sources into one screened manifest (F2-IMPL-AUTH-031).

    Cross-source policy:

    * the canonical identity is ``instance_id`` (exact match);
    * an IDENTICAL payload supplied by more than one source -> keep the
      highest-priority source (:data:`SOURCE_PRIORITY`) and record the exclusion;
    * a CONFLICTING payload for one identity (within or across sources) -> exclude
      EVERY copy and record it; equivalence cannot be established safely, so the
      task is not treated as an independent experimental unit.

    Records the per-source identity of every accepted candidate.
    """
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    per_source_raw: dict[str, int] = {}
    for source_key, path in source_paths:
        raw_rows = _read_jsonl(path)
        per_source_raw[source_key] = len(raw_rows)
        for raw in raw_rows:
            mapped = source_to_screen_row(source_key, raw)
            key = mapped["instance_id"] or f"<missing:{source_key}>"
            groups.setdefault(key, []).append((source_key, mapped))

    accepted: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for iid, items in groups.items():
        if len(items) == 1:
            accepted.append(items[0][1])
            continue
        sources_present = {source_key for source_key, _ in items}
        if len({_payload_key(sr) for _, sr in items}) == 1:
            ordered = sorted(items, key=lambda t: (_source_rank(t[0]), t[0]))
            keeper_source, keeper = ordered[0]
            accepted.append(keeper)
            for source_key, _ in ordered[1:]:
                exclusions.append(
                    {
                        "instance_id": iid,
                        "reason": "duplicate_identical_cross_source",
                        "kept_source": keeper_source,
                        "excluded_source": source_key,
                        "detail": "identical payload; highest-priority source kept",
                    }
                )
        elif len(sources_present) == 1:
            # Same source, same ID, DIFFERENT payload: an internal data defect.
            # Equivalence cannot be established, so no copy is trusted.
            for source_key, _ in items:
                exclusions.append(
                    {
                        "instance_id": iid,
                        "reason": "conflicting_duplicate_removed",
                        "excluded_source": source_key,
                        "detail": (
                            "same source, same canonical identity, differing payload; "
                            "all copies excluded (internal data defect)"
                        ),
                    }
                )
        else:
            # Cross-source conflict: the SAME underlying issue (repo + PR) is
            # described differently by two pipelines. Keep the highest-priority
            # source's record -- recorded, deterministic -- and never treat both
            # as independent units (fields are not merged).
            ordered = sorted(items, key=lambda t: (_source_rank(t[0]), t[0]))
            keeper_source, keeper = ordered[0]
            accepted.append(keeper)
            for source_key, _ in ordered[1:]:
                exclusions.append(
                    {
                        "instance_id": iid,
                        "reason": "conflicting_cross_source_priority_selected",
                        "kept_source": keeper_source,
                        "excluded_source": source_key,
                        "detail": (
                            "same underlying issue, differing derived payload; "
                            "highest-priority source kept, the other recorded"
                        ),
                    }
                )

    relevant: dict[str, list[str]] = {}
    sources_by_id: dict[str, str] = {}
    r8_missing: list[dict[str, Any]] = []
    for row in accepted:
        iid = row["instance_id"]
        sources_by_id[iid] = row.pop("_source", "")
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

    manifest = screen_pool_rows(
        accepted,
        relevant_file_sets=relevant,
        model_cutoff=cutoff,
        rejections=exclusions,
        contamination_policy=contamination_policy_record(cutoff, basis=CUTOFF_BASIS),
    )
    manifest.verify()

    accepted_by_source: dict[str, int] = {}
    for source_key in sources_by_id.values():
        accepted_by_source[source_key] = accepted_by_source.get(source_key, 0) + 1
    language_counts: dict[str, int] = {}
    for entry in manifest.entries:
        lang = entry.language or "<unknown>"
        language_counts[lang] = language_counts.get(lang, 0) + 1
    screen_rejections = [
        {
            "instance_id": e.instance_id,
            "source": sources_by_id.get(e.instance_id, ""),
            "failing_rules": [s.rule for s in e.screens if not s.passed],
            "contamination_class": e.contamination_class,
        }
        for e in manifest.entries
        if not e.admitted
    ]
    record = {
        "schema": "f2_population_union_v1",
        "cutoff": cutoff,
        "sources": [
            {
                "source_key": source_key,
                "path": str(path),
                "raw_rows": per_source_raw.get(source_key, 0),
                "accepted_after_union": accepted_by_source.get(source_key, 0),
            }
            for source_key, path in source_paths
        ],
        "total_raw_rows": sum(per_source_raw.values()),
        "total_accepted_after_union": len(accepted),
        "exclusions": len(exclusions),
        "conflicting_ids": sorted(
            {
                e["instance_id"]
                for e in exclusions
                if e["reason"]
                in (
                    "conflicting_duplicate_removed",
                    "conflicting_cross_source_priority_selected",
                )
            }
        ),
        "conflicting_ids_all_copies_removed": sorted(
            {
                e["instance_id"]
                for e in exclusions
                if e["reason"] == "conflicting_duplicate_removed"
            }
        ),
        "pre_screen_exclusions": exclusions,
        "r8_no_source_file": r8_missing,
        "language_counts": dict(sorted(language_counts.items())),
        "screen_rejections": screen_rejections,
    }
    return manifest, record


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--acquired", type=Path, default=None)
    ap.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="KEY=PATH",
        help="a source_key=acquired.jsonl pair; repeatable (union mode)",
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rejections-out", type=Path, default=None)
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    args = ap.parse_args(argv)

    if args.source:
        pairs = []
        for item in args.source:
            key, _, path = item.partition("=")
            pairs.append((key.strip(), Path(path.strip())))
        manifest, rejections = build_union(pairs, cutoff=args.cutoff)
    elif args.acquired:
        manifest, rejections = build(args.acquired, cutoff=args.cutoff)
    else:
        ap.error("supply --acquired PATH or at least one --source KEY=PATH")

    args.out.write_text(manifest.to_json(indent=2) + "\n", encoding="utf-8")
    if args.rejections_out:
        args.rejections_out.write_text(
            json.dumps(rejections, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    summary = {
        k: v for k, v in manifest.summary().items() if k not in ("entries", "rejections")
    }
    if args.source:
        summary = {
            "sources": rejections["sources"],
            "total_raw_rows": rejections["total_raw_rows"],
            "total_accepted_after_union": rejections["total_accepted_after_union"],
            "exclusions": rejections["exclusions"],
            "conflicting_ids": rejections["conflicting_ids"],
            "language_counts": rejections["language_counts"],
            **summary,
        }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
