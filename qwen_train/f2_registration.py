"""Operative S8 task registration: evaluator-compatible AND Python-confirmed.

Why this module exists
----------------------
The evaluator (`qwen_train/f2_evaluator.py`) is pytest-only: it appends
``--junitxml`` and inverts pytest's JUnit node-identity split, so a task is only
*evaluable* if its declared command is a pytest invocation and **every** declared
FAIL_TO_PASS and PASS_TO_PASS entry is a pytest node id.  A declared node whose
file path is not ``.py`` (e.g. ``tests/x.yml::test_y`` from pytest-mypy-plugins,
or ``plugin/test_x.txt::test_x``) is evaluable but not a Python test file.

The operative registration is therefore the set that is BOTH
evaluator-compatible AND Python-confirmed, ordered by ``created_at`` descending
then ``instance_id`` ascending — the rule already frozen in
:mod:`qwen_train.f2_task_plan`.

This module adds only the two predicates, the registration builder, and a
hash-only tracked manifest.  It changes no evaluator, population or frozen rule,
and it never runs a task command (``test_cmd`` is inert text throughout).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

from qwen_train.f2_task_plan import (
    ORDERING_RULE_ID,
    PlanError,
    load_eligible_created_at,
    order_tasks,
    registration_artifacts,
)

__all__ = [
    "SCHEMA_VERSION",
    "REGISTRATION_ID",
    "MANIFEST_NAME",
    "DEFAULT_MANIFEST_DIR",
    "PREDICATE",
    "is_pytest_command",
    "all_pytest_node_ids",
    "evaluator_compatible",
    "python_confirmed",
    "eligible",
    "build_manifest",
    "main",
]

SCHEMA_VERSION = "f2_registration_v1"
REGISTRATION_ID = "evaluator_compat_python_confirmed_v1"
MANIFEST_NAME = "registration_manifest.json"
DEFAULT_MANIFEST_DIR = Path("qwen_train") / "curriculum" / "f2_registration"
S8 = "S8_evidence_provenance"

PREDICATE = (
    "metadata-eligible (every screen except S8 passes) AND test_cmd matches "
    r"\bpytest\b with no existing --junitxml AND every declared FAIL_TO_PASS and "
    "PASS_TO_PASS entry is a '<path>::[<Class>::]<test>' pytest node id with "
    "non-empty segments AND every declared node path ends '.py'"
)

_PYTEST = re.compile(r"\bpytest\b|\bpy\.test\b")


class RegistrationError(ValueError):
    """The inputs are absent or the registration invariants are violated."""


def is_pytest_command(test_cmd: Any) -> bool:
    """True only for a command the evaluator can actually drive.

    ``augment_test_command`` refuses a command that already carries
    ``--junitxml``, so such a command is not usable as a declared contract.
    """
    text = str(test_cmd or "").strip()
    if not text or "--junitxml" in text:
        return False
    return bool(_PYTEST.search(text))


def all_pytest_node_ids(values: Iterable[Any] | None) -> bool:
    """True if non-empty and every entry is a well-formed pytest node id."""
    items = [str(v) for v in (values or []) if str(v).strip()]
    if not items:
        return False
    for item in items:
        if "::" not in item:
            return False
        path, *rest = item.split("::")
        if not path or any(not seg for seg in rest):
            return False
    return True


def python_confirmed(
    fail_to_pass: Iterable[Any] | None, pass_to_pass: Iterable[Any] | None = None
) -> bool:
    """True if every declared node path ends in ``.py``."""
    for field in (fail_to_pass, pass_to_pass):
        items = [str(v) for v in (field or []) if str(v).strip()]
        if not items:
            return False
        for item in items:
            if "::" not in item or not item.split("::")[0].endswith(".py"):
                return False
    return True


def evaluator_compatible(
    test_cmd: Any, fail_to_pass: Iterable[Any] | None, pass_to_pass: Iterable[Any] | None
) -> bool:
    return (
        is_pytest_command(test_cmd)
        and all_pytest_node_ids(fail_to_pass)
        and all_pytest_node_ids(pass_to_pass)
    )


def eligible(
    test_cmd: Any, fail_to_pass: Iterable[Any] | None, pass_to_pass: Iterable[Any] | None
) -> bool:
    """The operative registration predicate."""
    return evaluator_compatible(test_cmd, fail_to_pass, pass_to_pass) and python_confirmed(
        fail_to_pass, pass_to_pass
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    *,
    union_report: Path,
    acquired_paths: Sequence[Path],
    top_n: int = 30,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(manifest, ordered, excluded)`` for the operative registration.

    Reads the pinned inputs only.  No task command is executed; ``test_cmd`` is
    used solely to compute the eligibility predicate and a digest.
    """
    union_report = Path(union_report)
    report = json.loads(union_report.read_text(encoding="utf-8"))
    eligible_ids = [
        row["instance_id"]
        for row in report.get("screen_rejections") or []
        if set(row.get("failing_rules") or ()) == {S8}
    ]
    if not eligible_ids:
        raise RegistrationError("no metadata-eligible identities in the union report")

    meta = load_eligible_created_at(
        union_report=union_report,
        acquired_paths=[Path(p) for p in acquired_paths],
        eligible_ids=eligible_ids,
    )

    compatible = 0
    python_ok = 0
    rows: list[dict[str, Any]] = []
    shortfall: Counter[str] = Counter()
    for iid in eligible_ids:
        record = meta.get(iid)
        if record is None:
            shortfall["missing_metadata"] += 1
            continue
        rows.append({"instance_id": iid, **record})
    # Declarations are read in one second pass so the streaming metadata loader
    # stays untouched; both passes treat the task fields as inert data.
    declarations = _load_declarations(acquired_paths, set(eligible_ids))
    kept: list[dict[str, Any]] = []
    for row in rows:
        iid = row["instance_id"]
        f2p, p2p = declarations.get(iid, (None, None))
        if evaluator_compatible(row.get("test_cmd"), f2p, p2p):
            compatible += 1
        else:
            shortfall["not_evaluator_compatible"] += 1
            continue
        if not python_confirmed(f2p, p2p):
            shortfall["not_python_confirmed"] += 1
            continue
        python_ok += 1
        kept.append(row)

    ordered, excluded = order_tasks(kept)
    inputs = {
        "union_report_sha256": _sha256_file(union_report),
    }
    for path in acquired_paths:
        path = Path(path)
        provenance = path.parent / "PROVENANCE.json"
        sha = json.loads(provenance.read_text(encoding="utf-8")).get("normalised_sha256", "")
        inputs[f"{path.parent.name}_acquired_sha256"] = sha

    arts = registration_artifacts(ordered, excluded, inputs=inputs, top_n=top_n,
                                  generated_at=generated_at)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "registration_id": REGISTRATION_ID,
        "operative": True,
        "predicate": PREDICATE,
        "ordering_rule": ORDERING_RULE_ID,
        "counts": {
            "metadata_eligible": len(eligible_ids),
            "evaluator_compatible": compatible,
            "python_confirmed": python_ok,
            "registered": len(ordered),
            "excluded": len(excluded),
            "shortfall": dict(sorted(shortfall.items())),
        },
        "inputs": inputs,
        "registration_content_sha256": arts["content_sha256"],
        "artifacts_sha256": {
            "first_30": arts["first_30_sha256"],
            "full_ordered_metadata": arts["full_ordered_metadata_sha256"],
            "source_manifest": arts["source_manifest_sha256"],
            "ordering_rule_manifest": arts["ordering_rule_manifest_sha256"],
            "excluded": arts["excluded_sha256"],
        },
        "generated_at": arts["generated_at"],
        "note": (
            "Hash-only manifest. The ordered list and its per-task metadata live "
            "under data/f2_population/registration/ (git-ignored). created_at is "
            "ordering metadata only; eligibility never depends on a run outcome."
        ),
    }
    return manifest, ordered, excluded


def _load_declarations(
    acquired_paths: Sequence[Path], wanted: set[str]
) -> dict[str, tuple[list[str], list[str]]]:
    """Read FAIL_TO_PASS/PASS_TO_PASS for the wanted identities, as data only."""
    out: dict[str, tuple[list[str], list[str]]] = {}
    for path in acquired_paths:
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                iid = str(row.get("instance_id") or "")
                if iid in wanted and iid not in out:
                    out[iid] = (
                        [str(x) for x in (row.get("FAIL_TO_PASS") or [])],
                        [str(x) for x in (row.get("PASS_TO_PASS") or [])],
                    )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--union-report", type=Path, required=True)
    parser.add_argument("--acquired", type=Path, action="append", required=True)
    parser.add_argument("--registration-dir", type=Path,
                        default=Path("data") / "f2_population" / "registration")
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest, ordered, excluded = build_manifest(
            union_report=args.union_report,
            acquired_paths=[Path(p) for p in args.acquired],
            top_n=args.top_n,
        )
    except (RegistrationError, PlanError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    reg_dir = Path(args.registration_dir)
    reg_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("first_30.json", [dict(r) for r in ordered[: args.top_n]]),
        ("full_ordered_metadata.json", [dict(r) for r in ordered]),
        ("excluded.json", [dict(e) for e in excluded]),
    ):
        body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (reg_dir / name).write_text(body, encoding="utf-8", newline="\n")

    manifest_dir = Path(args.manifest_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True) if args.json
          else f"wrote {manifest_path} (registered={manifest['counts']['registered']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
