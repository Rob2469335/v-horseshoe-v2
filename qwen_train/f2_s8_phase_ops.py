"""F2-S8 phase operations — planning and post-phase reconciliation only.

Ported from the external operator script with the six defects the owner named
fixed:

1. the repository revision is ``git rev-parse HEAD`` (a real 40-hex commit id),
   never a commit timestamp;
2. ``candidate_manifest_digest`` is the SHA-256 of the ACTUAL build-manifest
   bytes passed on the command line, never a placeholder string;
3. every supplied ledger chain is consulted (``CombinedLedger``), so an
   admission or terminal record in the corrective chain is honoured;
4. no phase-2 path is hardcoded: the build manifest, registration, ledgers and
   staging manifests are explicit arguments;
5. ``Any`` is imported (the external script used it in an annotation without
   importing it);
6. the scheduler receives only the BUILT task set, so a withheld workspace such
   as ``stanfordnlp__dspy-8739`` can never be ranked first.

It never executes a task, never consumes a grant, never writes a ledger and
never awards credit.  ``pre`` emits a phase manifest; ``post`` recomputes the
credited union read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "PHASE_SCHEMA",
    "PhaseOpsError",
    "CombinedLedger",
    "build_phase",
    "main",
]

PHASE_SCHEMA = "f2-s8-phase-manifest/v2"
TARGET = 300
MAX_BATCH = 10


class PhaseOpsError(RuntimeError):
    """The phase operation cannot be conducted safely."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _revision(repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PhaseOpsError(f"git rev-parse HEAD failed: {exc}") from exc
    revision = (proc.stdout or "").strip()
    if proc.returncode != 0 or len(revision) != 40 or any(
        c not in "0123456789abcdef" for c in revision
    ):
        raise PhaseOpsError(
            "repository revision must be a real 40-hex `git rev-parse HEAD` value"
        )
    return revision


class CombinedLedger:
    """Read-only view over every supplied chain, for the scheduler's skip logic."""

    def __init__(self, paths: Sequence[Path | str]) -> None:
        from qwen_train.f2_s8_factory import AppendOnlyLedger

        if not paths:
            raise PhaseOpsError("at least one ledger is required")
        self.ledgers = [AppendOnlyLedger(Path(p)) for p in paths]
        for ledger in self.ledgers:
            ok, reason = ledger.verify()
            if not ok:
                raise PhaseOpsError(f"ledger {ledger.path} failed verification: {reason}")

    def records(self) -> list[dict[str, Any]]:
        return [row for ledger in self.ledgers for row in ledger.records()]

    def admitted(self, task_id: str) -> dict[str, Any] | None:
        for ledger in self.ledgers:
            row = ledger.admitted(task_id)
            if row is not None:
                return row
        return None

    def terminal_scientific(self, task_id: str) -> dict[str, Any] | None:
        for ledger in self.ledgers:
            row = ledger.terminal_scientific(task_id)
            if row is not None:
                return row
        return None

    def attempts(self, task_id: str) -> list[dict[str, Any]]:
        return [row for ledger in self.ledgers for row in ledger.attempts(task_id)]


def build_phase(
    *,
    repo_root: Path | str,
    build_manifest_path: Path | str,
    registration_path: Path | str,
    registration_id: str,
    registration_sha256: str,
    ledger_paths: Sequence[Path | str],
    out_path: Path | str,
    size: int = MAX_BATCH,
    staging_manifests_path: Path | str | None = None,
) -> dict[str, Any]:
    """Derive and write one phase manifest.  No execution, no ledger write."""
    from qwen_train.f2_s8_factory import RetryPolicy, plan_run

    repo = Path(repo_root).resolve()
    manifest_file = Path(build_manifest_path)
    manifest_bytes = manifest_file.read_bytes()
    manifest_digest = _sha256_file(manifest_file)
    build = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(build, dict):
        raise PhaseOpsError("build manifest is not a JSON object")
    registration = json.loads(Path(registration_path).read_text(encoding="utf-8"))
    if isinstance(registration, dict):
        registration = (
            registration.get("task_ids") or registration.get("tasks") or list(registration)
        )
    registered = [str(t) for t in registration]

    # ONLY built tasks enter the scheduler's universe.  This is defect 6: the
    # withheld dspy-8739 workspace is not built and can never be ranked first.
    built_registered = [
        task for task in registered
        if isinstance(build.get(task), dict) and build[task].get("status") == "built"
    ]
    policy = RetryPolicy()
    plan = plan_run(
        ordered_task_ids=built_registered,
        ledger=CombinedLedger(ledger_paths),
        registration_id=registration_id,
        registration_sha256=registration_sha256,
        expected_registration_sha256=registration_sha256,
        policy=policy,
    )
    effective = max(1, min(int(size or MAX_BATCH), MAX_BATCH))
    selected = list(plan.runnable[:effective])
    if not selected:
        raise PhaseOpsError(
            "no built, unrecorded, under-cap candidate is runnable; refusing to "
            "emit an empty phase"
        )

    staged: dict[str, dict[str, Any]] = {}
    if staging_manifests_path:
        staging_doc = json.loads(Path(staging_manifests_path).read_text(encoding="utf-8"))
        staged = {
            str(w.get("task_id")): w for w in staging_doc.get("workspaces", [])
        }

    tasks: list[dict[str, Any]] = []
    for task in selected:
        entry = build.get(task) or {}
        workspace = str(entry.get("workspace") or "")
        tasks.append({
            "task_id": task,
            "recorded_build_status": entry.get("status"),
            "recorded_workspace": workspace or None,
            "host_workspace_exists": bool(workspace) and Path(workspace).is_dir(),
            "recorded_staging_digest": (staged.get(task) or {}).get("manifest_digest"),
            "recorded_total_bytes": (staged.get(task) or {}).get("total_bytes"),
        })

    revision = _revision(repo)
    phase = {
        "schema": PHASE_SCHEMA,
        "status": "PLANNING ONLY - NOT AUTHORIZED - EXECUTES NOTHING",
        "repository_revision_at_generation": revision,
        "build_manifest_path": str(manifest_file.resolve()),
        "candidate_manifest_digest": manifest_digest,
        "registration_path": str(Path(registration_path).resolve()),
        "registration_id": registration_id,
        "registration_sha256": registration_sha256,
        "policy": policy.as_dict(),
        "selection": {
            "rule": "built only; plan_run skips admitted, terminal-scientific and "
                    "retry-exhausted; then registration order; cap 10",
            "registered": len(registered),
            "built_registered": len(built_registered),
            "runnable": len(plan.runnable),
            "selected": len(selected),
            "skipped_admitted": list(plan.skipped_admitted),
            "skipped_terminal": list(plan.skipped_terminal),
            "skipped_exhausted": list(plan.skipped_exhausted),
        },
        "tasks": tasks,
        "grant_binding_required": {
            "schema": "f2-authorization/v1",
            "stage": "S8-001",
            "operations": ["base_gold_production"],
            "approver": "f2-s8-owner-v1",
            "repository_revision": revision,
            "candidate_manifest_digest": manifest_digest,
            "registration_sha256": registration_sha256,
            "note": "one single-use grant per execution; re-verify every field "
                    "immediately before signing",
        },
        "operator_sequence": [
            "1. independently re-run `python -m qwen_train.f2_s8_precheck` over this "
            "built set and require PASS for every selected task",
            "2. owner signs a single-use grant bound to the revision, manifest "
            "digest and registration digest above",
            "3. stage the built workspaces and run ONLY the selected task list",
            "4. run the D4 post-batch audit; a non-PASS audit blocks credit and the "
            "next batch",
            "5. stop if infrastructure failures exceed 10% of the attempted tasks",
        ],
    }
    phase["phase_digest"] = _digest(phase)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(phase, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return phase


def _reconcile(
    *,
    registration_path: Path | str,
    ledger_paths: Sequence[Path | str],
    out_path: Path | str,
    baseline_credited: int | None = None,
) -> dict[str, Any]:
    from qwen_train.f2_s8_batch import reconcile_admissions_across_chains
    from qwen_train.f2_s8_factory import AppendOnlyLedger

    registration = json.loads(Path(registration_path).read_text(encoding="utf-8"))
    if isinstance(registration, dict):
        registration = (
            registration.get("task_ids") or registration.get("tasks") or list(registration)
        )
    registered = [str(t) for t in registration]
    chains = [Path(p) for p in ledger_paths]
    integrity: dict[str, Any] = {}
    for path in chains:
        ledger = AppendOnlyLedger(path)
        ok, msg = ledger.verify()
        integrity[path.name] = {
            "sha256": _sha256_file(path),
            "rows": len(ledger.records()),
            "verify": [ok, msg],
        }
    out = reconcile_admissions_across_chains(chains, registered)
    credited = int(out["credited_count"])
    baseline = credited if baseline_credited is None else int(baseline_credited)
    report = {
        "schema": "f2-s8-phase-reconciliation/v2",
        "chains": integrity,
        "credited_union": credited,
        "baseline_credited": baseline,
        "newly_credited": credited - baseline,
        "target": TARGET,
        "deficit": max(0, TARGET - credited),
        "corrections_verified": out["corrections_verified"],
        "refused_terminal_override": out["refused_terminal_override"],
        "registered_universe": len(registered),
        "note": "the credited union is the counting rule's figure; this report is "
                "read-only and changes no ledger row",
    }
    report["report_digest"] = _digest(report)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m qwen_train.f2_s8_phase_ops")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pre = sub.add_parser("pre", help="derive one phase manifest (executes nothing)")
    pre.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    pre.add_argument("--build-manifest", required=True)
    pre.add_argument("--registration", required=True)
    pre.add_argument("--registration-id", required=True)
    pre.add_argument("--registration-sha256", required=True)
    pre.add_argument("--ledger", action="append", required=True)
    pre.add_argument("--out", required=True)
    pre.add_argument("--size", type=int, default=MAX_BATCH)
    pre.add_argument("--staging-manifests", default="")

    post = sub.add_parser("post", help="reconcile admissions read-only")
    post.add_argument("--registration", required=True)
    post.add_argument("--ledger", action="append", required=True)
    post.add_argument("--out", required=True)
    post.add_argument("--baseline-credited", type=int, default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.cmd == "pre":
            phase = build_phase(
                repo_root=args.repo_root,
                build_manifest_path=args.build_manifest,
                registration_path=args.registration,
                registration_id=args.registration_id,
                registration_sha256=args.registration_sha256,
                ledger_paths=args.ledger,
                out_path=args.out,
                size=args.size,
                staging_manifests_path=args.staging_manifests or None,
            )
            print(json.dumps({
                "selected": len(phase["tasks"]),
                "candidate_manifest_digest": phase["candidate_manifest_digest"],
                "repository_revision": phase["repository_revision_at_generation"],
                "phase_digest": phase["phase_digest"],
                "out": args.out,
            }, indent=2, sort_keys=True))
            return 0
        report = _reconcile(
            registration_path=args.registration,
            ledger_paths=args.ledger,
            out_path=args.out,
            baseline_credited=args.baseline_credited,
        )
        print(json.dumps({
            "credited_union": report["credited_union"],
            "deficit": report["deficit"],
            "report_digest": report["report_digest"],
            "out": args.out,
        }, indent=2, sort_keys=True))
        return 0
    except PhaseOpsError as exc:
        print(json.dumps({"error": str(exc), "status": "REFUSED"}, indent=2))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
