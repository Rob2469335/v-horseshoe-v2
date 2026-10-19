"""Materialise F2-S8 task inputs from the pinned LOCAL population snapshots.

Why this exists
---------------
The campaign stalled at ~30 prepared input records while **3 828** tasks are
registered. That looked like a missing-dataset problem; it is not. Measured:
every one of the 3 828 registered ids is present in the population snapshots
already on disk (2 065 ``swe-rebench-v2`` + 1 763 ``swe-bench-live``). The gap
was that only the swe-bench-live parquet was ever read.

Two schemas, one canonical command
----------------------------------
The two upstream sources declare their test command differently:

======================  ====================================================
``swe-bench-live``      ``test_cmds`` -- a LIST of commands
``swe-rebench-v2``      ``install_config.test_cmd`` -- a single string
======================  ====================================================

Neither is read directly here. Both go through
:func:`qwen_train.f2_population_build.source_to_screen_row`, the SAME adapter the
live runner uses, so a materialised record can never disagree with the command
the runner will derive from it.

Eligibility is the repository's own predicate (:func:`f2_registration.eligible`),
never a local re-implementation, so materialisation cannot admit something
registration would reject.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from qwen_train.f2_population_build import source_to_screen_row
from qwen_train.f2_registration import eligible

__all__ = [
    "POPULATION_SOURCES",
    "MATERIALISE_SCHEMA",
    "load_population_rows",
    "materialise_batch",
]

#: source key -> directory name under ``data/f2_population``.
POPULATION_SOURCES: dict[str, str] = {
    "swe-bench-live": "upstream_swe_bench_live_b51a8642",
    "swe-rebench-v2": "upstream_swe_rebench_v2_10483de0",
}

MATERIALISE_SCHEMA = "f2_s8_materialise_v1"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_population_rows(population_root: Path | str) -> dict[str, dict[str, Any]]:
    """``instance_id -> {"source", "row"}`` for every locally available task.

    Reads the ``_snapshot`` copies when present (they are the acquisition's own
    preserved bytes) and falls back to ``raw``.  A task appearing in both
    sources keeps the FIRST source in ``POPULATION_SOURCES`` order, so the
    mapping is deterministic.
    """
    import pyarrow.parquet as pq

    root = Path(population_root)
    found: dict[str, dict[str, Any]] = {}
    for source, dirname in POPULATION_SOURCES.items():
        base = root / dirname
        shards = sorted((base / "_snapshot").rglob("*.parquet")) or sorted(
            base.rglob("*.parquet"))
        for shard in shards:
            for row in pq.read_table(shard).to_pylist():
                iid = row.get("instance_id")
                if not iid or iid in found:
                    continue
                found[str(iid)] = {"source": source, "row": row}
    return found


def _patches_for(source: str, row: Mapping[str, Any]) -> tuple[str, str]:
    gold = str(row.get("patch") or "")
    test = str(row.get("test_patch") or "")
    if not gold.strip() or not test.strip():
        raise ValueError(f"{source}: gold/test patch missing")
    return gold, test


def materialise_batch(
    *,
    registration_path: Path | str,
    population_root: Path | str,
    out_dir: Path | str,
    attempted: Iterable[str] = (),
    excluded: Iterable[str] = (),
    parked: Iterable[str] = (),
    limit: int = 10,
) -> dict[str, Any]:
    """Write input records + patches for the next eligible tasks, in order.

    Selection is REGISTRATION ORDER, never predicted difficulty.  A task is
    skipped only for a recorded, outcome-independent reason.  Nothing is
    overwritten outside ``out_dir``.
    """
    order = json.loads(Path(registration_path).read_text(encoding="utf-8"))
    rows = load_population_rows(population_root)
    attempted_set = set(attempted)
    excluded_set = set(excluded)
    parked_set = set(parked)

    inputs_dir = Path(out_dir) / "inputs"
    patches_dir = Path(out_dir) / "patches"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    patches_dir.mkdir(parents=True, exist_ok=True)

    reasons: dict[str, int] = {}
    selected: list[dict[str, Any]] = []

    for position, entry in enumerate(order):
        iid = str(entry.get("instance_id") or "")
        if not iid:
            continue
        if iid in attempted_set:
            reasons["already_attempted"] = reasons.get("already_attempted", 0) + 1
            continue
        if iid in excluded_set:
            reasons["policy_excluded"] = reasons.get("policy_excluded", 0) + 1
            continue
        if iid in parked_set:
            reasons["policy_parked"] = reasons.get("policy_parked", 0) + 1
            continue
        found = rows.get(iid)
        if found is None:
            reasons["absent_from_local_snapshot"] = (
                reasons.get("absent_from_local_snapshot", 0) + 1)
            continue
        source, row = found["source"], found["row"]
        try:
            canonical = source_to_screen_row(source, row)
        except Exception:  # noqa: BLE001 - an unmappable row is skipped, not guessed
            reasons["source_adapter_failed"] = reasons.get("source_adapter_failed", 0) + 1
            continue
        test_cmd = str(canonical.get("test_cmd") or "")
        f2p = list(canonical.get("fail_to_pass") or [])
        p2p = list(canonical.get("pass_to_pass") or [])
        if not eligible(test_cmd, f2p, p2p):
            reasons["not_evaluator_compatible"] = (
                reasons.get("not_evaluator_compatible", 0) + 1)
            continue
        try:
            gold, test_patch = _patches_for(source, row)
        except ValueError:
            reasons["missing_patch"] = reasons.get("missing_patch", 0) + 1
            continue

        base_commit = str(row.get("base_commit") or "")
        repo = str(row.get("repo") or "")
        record = {
            "FAIL_TO_PASS": f2p,
            "PASS_TO_PASS": p2p,
            "all_hints_text": row.get("all_hints_text") or row.get("hints_text") or "",
            "base_commit": base_commit,
            "commit_url": f"https://github.com/{repo}/tree/{base_commit}",
            "commit_urls": [],
            "created_at": str(row.get("created_at") or ""),
            "difficulty": row.get("difficulty") or {},
            "hints_text": row.get("hints_text") or "",
            "instance_id": iid,
            "issue_numbers": list(row.get("issue_numbers") or []),
            "log_parser": "pytest",
            "patch": gold,
            "problem_statement": row.get("problem_statement") or "",
            "pull_number": str(row.get("pull_number") or ""),
            "repo": repo,
            # The RAW list, exactly as the source declared it. The runner derives
            # the canonical command from this through the same adapter used here.
            "test_cmds": list(row.get("test_cmds") or ([row["install_config"]["test_cmd"]]
                             if isinstance(row.get("install_config"), Mapping)
                             and row["install_config"].get("test_cmd") else [test_cmd])),
            "test_patch": test_patch,
            "source": source,
        }
        text = json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        (inputs_dir / f"{iid}.json").write_text(text, encoding="utf-8", newline="\n")
        (patches_dir / f"{iid}.gold_patch").write_text(gold, encoding="utf-8", newline="\n")
        (patches_dir / f"{iid}.test_patch").write_text(test_patch, encoding="utf-8", newline="\n")
        selected.append({
            "task_id": iid,
            "registration_position": position,
            "source": source,
            "repo": repo,
            "base_commit": base_commit,
            "test_cmd": test_cmd,
            "f2p_count": len(f2p),
            "p2p_count": len(p2p),
            "gold_patch_sha256": _sha256_text(gold),
            "test_patch_sha256": _sha256_text(test_patch),
            "file_sha256": _sha256_text(text),
        })
        if len(selected) >= int(limit):
            break

    manifest = {
        "schema": MATERIALISE_SCHEMA,
        "registration_path": str(Path(registration_path)),
        "registration_sha256": hashlib.sha256(
            Path(registration_path).read_bytes()).hexdigest(),
        "population_root": str(Path(population_root)),
        "selection_rule": ("registration order; never-attempted; policy exclusions "
                           "removed; evaluator-compatible; non-empty patches"),
        "limit": int(limit),
        "selected": len(selected),
        "skip_reasons": reasons,
        "tasks": selected,
    }
    (Path(out_dir) / "inputs_manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
