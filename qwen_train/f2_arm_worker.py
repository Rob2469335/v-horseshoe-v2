"""F2 Orchestrator — Child Worker Process.

Implements the F2 execution/import contract (authorized ``bb38ed85``) and the
child-worker responsibilities of the F2 orchestrator design (§4.2):

    bootstrap (sys.path.insert(0, REPO_ROOT))
    → load manifest
    → independently verify manifest
    → install replay state (FROZEN_REPLAY)
    → validate arm (requested arm == manifest.arm)
    → deliver via the authoritative get_delivery_artifact()
    → instrument (lesson_block_hash, final_prompt_hash, delivery_timestamp)
    → execute the governed arm (bounded; see EXECUTION GATE below)
    → return arm receipt

Contract invariants enforced here:
  - Repository imports happen ONLY after ``sys.path.insert(0, REPO_ROOT)``.
  - No PYTHONPATH, no cwd reliance, no editable-install reliance.
  - Fail-closed: any manifest/verify/arm/delivery failure => nonzero exit and a
    JSON error line; there is NO silent LIVE fallback.

Execution gate: this first implementation step does NOT run a live backend
worker loop.  Arm execution is carried by the ``f2_arm_execute`` callback /
``F2_ARM_EXEC_CMD`` seam (see ORCHESTRATOR).  In the default (no exec seam)
mode it records execution as ``delegated`` and returns a complete delivery
receipt.  This is an implementation-boundary decision, not a scientific change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Fresh-process repository-root bootstrap (BEFORE any repository import).
# This is the accepted F2 execution/import contract (bb38ed85).
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))  # repo root

from runtime_v2.services.f2_freeze import (  # noqa: E402
    FreezeVerificationError,
    load_manifest,
    verify_manifest,
)
from runtime_v2.services.f2_replay import (  # noqa: E402
    get_delivery_artifact,
    install_replay_state,
)

# F2-OP-INFRA-004 §1 (D2): the explicit absolute directory an F2 arm uses for
# delivery-evidence transport. Must match the name the adapter propagates and
# that ``AgentServiceV2._TRAJ_DIR`` reads.
F2_TRAJ_DIR_ENV = "SWARM_F2_TRAJ_DIR"

# F2-OP-INFRA-004 §2 (D3): the delivery-evidence write outcomes the adapter
# collected from P2 for THIS execution. Set by the adapter wiring before
# verification; consumed only to classify a missing-evidence failure correctly.
_ADAPTER_WRITE_OUTCOMES: ContextVar[list[dict[str, Any]]] = ContextVar(
    "f2_adapter_write_outcomes", default=[]
)


def _compute_final_prompt_hash(system_prompt: str, delivered: str) -> str:
    return hashlib.sha256((system_prompt + delivered).encode("utf-8")).hexdigest()


def _read_evidence_write_outcome(
    write_outcomes: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Return the first FAILED evidence-write outcome for this execution, else None.

    F2-OP-INFRA-004 §2 (D3). The adapter collects P2's explicit
    ``f2_evidence_write_outcome`` lines; a ``write_failed`` outcome means the
    evidence was ATTEMPTED and could not be persisted, which is categorically
    different from evidence that never existed.
    """
    for rec in write_outcomes or []:
        if isinstance(rec, dict) and rec.get("outcome") == "write_failed":
            return rec
    return None


def _resolve_traj_dir(workspace_root: Path) -> Path:
    """Resolve the ONE directory P2 writes delivery evidence to.

    F2-OP-INFRA-004 §1 (D2). For an F2 arm the adapter supplies an explicit
    absolute directory (``SWARM_F2_TRAJ_DIR``) and BOTH the P2 writer
    (``AgentServiceV2._TRAJ_DIR``) and this reader resolve to it. Exactly one
    directory is ever consulted — this deliberately does NOT search candidates,
    because selecting among several directories would reintroduce the ambiguity
    that made the join unprovable.

    When the variable is unset the historical ``<workspace_root>/data/trajectories``
    derivation is preserved (used by non-F2 paths and unit tests).
    """
    override = os.environ.get(F2_TRAJ_DIR_ENV, "").strip()
    if override:
        resolved = Path(override)
        if not resolved.is_absolute():
            raise FreezeVerificationError(
                f"F2 fail-closed: {F2_TRAJ_DIR_ENV} must be an absolute path, got "
                f"{override!r}. The P2 evidence writer and the F2 worker reader must "
                "resolve to exactly one deterministic directory."
            )
        return resolved
    return Path(workspace_root) / "data" / "trajectories"


def _capture_workspace_mutation(
    workspace_root: "Path | str",
    base_commit: str = "",
) -> dict[str, Any]:
    """Post-run: record the repository paths the arm actually touched.

    Integrity evidence, not a gate. The F2 endpoint is measured from the
    TRAJECTORY (ordered behavioral records), and the task outcome comes from the
    evaluator run over the retained workspace, so nothing else notices a write
    outside the authorized surface - for example an arm editing the evaluator's
    own test files to manufacture a pass. Recording the observed dirty-path set
    lets an independent reviewer detect that from persisted evidence instead of
    trusting the agent's self-report.

    Fail-soft by design: this is an OBSERVATION. If the workspace cannot be
    inspected the receipt says so explicitly (``captured: False``) rather than
    implying the workspace was clean.

    F2-IMPL-AUTH-007: the dirty-path set alone is DEFEATABLE - an arm that runs
    ``git add -A && git commit`` leaves a clean working tree, so ``git status``
    reports nothing while the repository has in fact been mutated. The post-run
    HEAD commit and ref list are therefore recorded too: a commit moves HEAD away
    from the base the workspace was reset to, and a new ref is exactly the
    history-manipulation channel the pre-run stripping removes. Ignored paths are
    sampled because ``-uall`` alone does not report them.
    """
    import subprocess as _sub

    from qwen_train.arm_workspace import _observed_dirty_paths, resolve_task_repo

    def _git(*a: str) -> tuple[bool, str]:
        try:
            r = _sub.run(["git", *a], cwd=str(repo), capture_output=True, text=True)
        except Exception:  # noqa: BLE001
            return False, ""
        return r.returncode == 0, r.stdout.strip()

    try:
        repo = resolve_task_repo(workspace_root)
        touched = sorted(_observed_dirty_paths(repo))
    except Exception as exc:  # noqa: BLE001 - evidence capture never fails the arm
        return {
            "captured": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }

    ok_head, head_after = _git("rev-parse", "HEAD")
    ok_refs, refs_raw = _git("for-each-ref", "--format=%(refname)")
    ok_ign, ign_raw = _git("status", "--porcelain", "-uall", "--ignored")
    ignored = sorted(
        ln[3:].strip().strip('"')
        for ln in ign_raw.splitlines()
        if ln.startswith("!! ")
    ) if ok_ign else []

    base = (base_commit or "").strip()
    return {
        "captured": True,
        "repository": str(repo),
        "touched_paths": touched,
        "touched_count": len(touched),
        # Commit-bypass detection: HEAD must still be the reset base.
        "base_commit_expected": base,
        "head_after": head_after if ok_head else "",
        "head_unchanged": (bool(base) and ok_head and head_after == base),
        # History-manipulation detection: any ref beyond HEAD is unexpected.
        "refs_after": sorted(r.strip() for r in refs_raw.splitlines() if r.strip())
        if ok_refs
        else [],
        # Hidden-artifact detection (bounded sample).
        "ignored_paths_sample": ignored[:50],
        "ignored_count": len(ignored),
    }


MUTATION_ALLOWED = "allowed"
MUTATION_EXPECTED = "expected"
MUTATION_SUSPICIOUS = "suspicious"
MUTATION_PROHIBITED = "prohibited"
MUTATION_UNKNOWN = "unknown"

#: Integrity assessment values derived from the classification.
INTEGRITY_CLEAN = "clean"
INTEGRITY_SUSPECT = "suspect"
INTEGRITY_COMPROMISED = "compromised"
INTEGRITY_UNKNOWN = "unknown"


def _classify_workspace_mutation(
    evidence: Mapping[str, Any],
    *,
    relevant_file_set: Sequence[str] | None = None,
    authorized_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Classify observed workspace activity and assess its integrity.

    ENGINEERING assessment, NOT an exclusion rule. The F2 admission authority -
    not the arm, and not this function - decides admissibility; this only turns
    raw observation into a reasoned classification so that decision is auditable
    rather than re-derived ad hoc. Nothing here changes the endpoint, the outcome
    or the statistics.

    Order matters: a prohibited signal (history manipulation) dominates, because a
    clean working tree can otherwise be manufactured by committing.
    """
    reasons: list[str] = []
    by_class: dict[str, list[str]] = {
        MUTATION_ALLOWED: [],
        MUTATION_EXPECTED: [],
        MUTATION_SUSPICIOUS: [],
        MUTATION_PROHIBITED: [],
    }

    if not evidence.get("captured"):
        return {
            "classification": MUTATION_UNKNOWN,
            "integrity": INTEGRITY_UNKNOWN,
            "reasons": ["workspace could not be inspected"],
            "by_class": by_class,
        }

    allowed = {str(p) for p in (authorized_paths or ())}
    expected = {str(p) for p in (relevant_file_set or ())}

    # -- prohibited: history manipulation ---------------------------------
    if evidence.get("base_commit_expected") and not evidence.get("head_unchanged"):
        reasons.append(
            f"HEAD moved from the reset base: {evidence.get('head_after')!r} != "
            f"{evidence.get('base_commit_expected')!r} (a commit can hide a mutation)"
        )
        by_class[MUTATION_PROHIBITED].append("<HEAD>")

    # _strip_future_history deletes EVERY ref and detaches at base, so the
    # expected post-run ref set is EMPTY; any surviving ref is unexpected.
    refs = [str(r).strip() for r in (evidence.get("refs_after") or []) if str(r).strip()]
    unexpected_refs = refs
    if unexpected_refs:
        reasons.append(f"unexpected git refs present: {unexpected_refs[:5]}")
        by_class[MUTATION_PROHIBITED].extend(unexpected_refs[:5])

    # -- per-path classification ------------------------------------------
    for p in evidence.get("touched_paths") or []:
        sp = str(p)
        if sp.startswith(".git/") or sp == ".git":
            by_class[MUTATION_PROHIBITED].append(sp)
            reasons.append(f"git internals modified: {sp}")
        elif sp in allowed:
            by_class[MUTATION_ALLOWED].append(sp)
        elif sp in expected:
            by_class[MUTATION_EXPECTED].append(sp)
        else:
            by_class[MUTATION_SUSPICIOUS].append(sp)

    if evidence.get("ignored_count"):
        reasons.append(
            f"{evidence['ignored_count']} ignored path(s) present "
            "(hidden artifacts are invisible to -uall)"
        )
        by_class[MUTATION_SUSPICIOUS].extend(
            str(p) for p in (evidence.get("ignored_paths_sample") or [])[:5]
        )

    # -- assessment -------------------------------------------------------
    if by_class[MUTATION_PROHIBITED]:
        classification, integrity = MUTATION_PROHIBITED, INTEGRITY_COMPROMISED
    elif by_class[MUTATION_SUSPICIOUS]:
        classification, integrity = MUTATION_SUSPICIOUS, INTEGRITY_SUSPECT
    elif by_class[MUTATION_EXPECTED] or by_class[MUTATION_ALLOWED]:
        classification, integrity = MUTATION_EXPECTED, INTEGRITY_CLEAN
    else:
        classification, integrity = MUTATION_ALLOWED, INTEGRITY_CLEAN

    return {
        "classification": classification,
        "integrity": integrity,
        "reasons": reasons,
        "by_class": {k: sorted(v) for k, v in by_class.items()},
    }


def _read_p2_delivery_evidence(
    workspace_root: Path, rollout_id: str | None = None
) -> dict[str, Any] | None:
    """Read P2's authoritative delivery-evidence record for THIS rollout.

    Transport decision (authorized per F2 worker-exec §10 L280): the narrowest
    existing mechanism is trajectory readback — P2 writes delivery_evidence to
    data/trajectories/{run_id}.jsonl at the model-facing delivery seam
    (runtime_v2/api/agent_service_v2.py). This function reads that record and
    returns it.

    Identity binding: selection is on the record's ``rollout_id`` field, which P2
    reads from the ``SWARM_F2_ROLLOUT_ID`` environment value P1 set on the P2
    process. The record's ``run_id`` is P2's own per-invocation uuid4 and names
    the trajectory FILE; it is never a join key for P1, and the file name is not
    used to select evidence.

    Fail-closed. Returns None — never a partially-trusted record — when:
      * no rollout identity was supplied, so nothing can be selected safely;
      * no delivery_evidence record carries this rollout's identity (missing
        evidence, or another rollout's / stale evidence);
      * several records match this rollout AND disagree on any authoritative
        field, because which one P2 actually delivered is then unknown.
    Byte-identical duplicate records are an idempotent re-write, not a
    conflict, and collapse to one.
    """
    expected = (rollout_id or "").strip()
    if not expected:
        return None

    traj_dir = _resolve_traj_dir(workspace_root)
    if not traj_dir.is_dir():
        return None
    traj_files = sorted(
        traj_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    matches: list[dict[str, Any]] = []
    for traj_file in traj_files:
        try:
            with open(traj_file, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("record_type") != "delivery_evidence":
                        continue
                    # Identity-less evidence is never selectable: an F2 arm must
                    # be bound to a rollout it can name.
                    if not str(rec.get("rollout_id") or "").strip():
                        continue
                    if str(rec.get("rollout_id")).strip() != expected:
                        continue
                    matches.append(rec)
        except OSError:
            continue

    if not matches:
        return None

    first = matches[0]
    for other in matches[1:]:
        if json.dumps(other, sort_keys=True, default=str) != json.dumps(
            first, sort_keys=True, default=str
        ):
            # Conflicting evidence for one rollout: refuse to guess which
            # delivery is authoritative.
            return None
    return first


def _arm_report(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def _verify_p2_evidence_binding(p2_evidence: dict[str, Any], artifact: Any) -> str:
    """Prove P2's evidence was produced under THIS F2 manifest/arm.

    Returns the model/agent identity P2 reported (e.g. ``"coder"``).

    Why the manifest and not the ``arm`` string: P2's evidence record carries the
    MODEL/AGENT identity in a field named ``arm`` — see
    ``runtime_v2/services/f2_replay.record_f2_delivery_evidence`` — which is NOT
    the Experiment-J F2 arm (``T``/``X``/``C0``). Comparing those two strings
    rejected every real execution. The authoritative F2 arm binding is the frozen
    manifest the evidence was produced under (the arm is a manifest field), so the
    content address is verified instead. This is strictly stronger than the
    previous equality check: it also rejects evidence produced under any other
    manifest, and it never treats agent identity as arm identity.

    Fail-closed: missing or mismatched manifest binding raises.
    """
    p2_agent_id = p2_evidence.get("arm")
    p2_manifest_content_address = p2_evidence.get("manifest_content_address")
    if not p2_manifest_content_address:
        raise RuntimeError(
            "F2 fail-closed: P2 delivery evidence carries no manifest_content_address "
            "and therefore cannot be bound to an F2 arm. Requested "
            f"content_address={artifact.content_address!r} (arm={artifact.arm!r})."
        )
    if p2_manifest_content_address != artifact.content_address:
        raise RuntimeError(
            "F2 fail-closed: P2 delivery evidence is bound to a different F2 "
            "manifest/arm: evidence manifest_content_address="
            f"{p2_manifest_content_address!r} != requested "
            f"{artifact.content_address!r} (requested arm={artifact.arm!r})."
        )
    return p2_agent_id if isinstance(p2_agent_id, str) else ""


def run_worker(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F2 fresh arm worker")
    parser.add_argument("--manifest", required=True, help="Path to the frozen F2 manifest")
    parser.add_argument("--arm", required=True, choices=("T", "X", "C0"), help="Requested arm")
    parser.add_argument("--system-prompt", default="", help="System prompt prefix for final_prompt_hash")
    # F2-OP-INFRA-004 §5 (production wiring): the STRUCTURED execution seam.
    # This replaces the F2_ARM_EXEC_CMD environment variable, which no tracked
    # production caller ever set, so the production path always terminated at
    # "delegated". Execution is now an explicit argument carried by the caller.
    parser.add_argument(
        "--execute", action="store_true",
        help="Execute this arm through the F2 execution adapter (production model seam)",
    )
    parser.add_argument("--task-prompt", default="", help="Task prompt driven over loopback HTTP/SSE")
    parser.add_argument("--task-id", default="", help="Task identity for this execution")
    parser.add_argument("--instance-id", default="", help="Curriculum instance_id (task provenance)")
    parser.add_argument("--agent-id", default="coder", help="Agent identity (NOT the F2 arm)")
    parser.add_argument("--port", type=int, default=8211, help="Loopback port for fresh P2")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    rollout_id = os.environ.get("SWARM_F2_ROLLOUT_ID", "unknown-rollout")
    trajectory_run_id = os.environ.get("SWARM_F2_TRAJECTORY_RUN_ID", "")
    exec_cmd = os.environ.get("F2_ARM_EXEC_CMD", "")
    structured_execute = bool(args.execute)

    exec_start = time.time()
    # The validated task-workspace identity for an EXECUTING arm (None otherwise).
    exec_workspace_root: Path | None = None
    process_identity = {
        "role": "f2_arm_worker",
        "pid": os.getpid(),
        "executable": sys.executable,
        "argv": sys.argv,
    }

    try:
        # -- independent load + verification --
        artifact = load_manifest(manifest_path)
        verify_manifest(artifact)

        # -- arm validation: the child executes ONLY the authorized arm --
        if artifact.arm != args.arm:
            raise FreezeVerificationError(
                f"arm mismatch: requested={args.arm!r}, manifest={artifact.arm!r}"
            )

        # -- install replay state; authoritative delivery --
        install_replay_state(artifact, str(manifest_path))
        delivered = get_delivery_artifact()
        delivered_actual = delivered if args.arm == "C0" or delivered else artifact.rendered_artifact
        lesson_block_hash = hashlib.sha256(delivered_actual.encode("utf-8")).hexdigest()
        final_prompt_hash = _compute_final_prompt_hash(args.system_prompt, delivered_actual)
        delivery_timestamp = time.time()

        # -- execution seam (F2-OP-INFRA-004 §5) --
        execution_result: dict[str, Any]
        adapter_evidence: dict[str, Any] | None = None
        if structured_execute:
            # An execution arm MUST carry a declared task identity: without an
            # ``instance_id`` there is no pool ``base_commit`` and therefore no
            # way to establish per-arm workspace isolation (design §13.5). Fail
            # closed rather than execute against an unreset, shared workspace.
            if not args.instance_id:
                raise FreezeVerificationError(
                    "F2 fail-closed: --execute requires --instance-id (a declared "
                    "task identity). An undeclared task has no base_commit and its "
                    "workspace cannot be isolated; refusing to execute."
                )
            # Production path: invoke the F2 execution adapter directly and take
            # its structured evidence. No shell string, no unset env var.
            from qwen_train.f2_execution_adapter import (  # noqa: PLC0415
                F2ExecutionAdapter,
                bind_task_environment,
                resolve_f2_traj_dir,
            )

            # F2 fail-closed workspace identity. An executing arm MUST declare
            # its isolated task workspace explicitly. There is NO fallback to
            # the code root: the code root is itself a git repository and so
            # satisfies resolve_task_repo(), which would silently turn arm
            # preparation into a reset of the main repository. Resolved BEFORE
            # any workspace preparation, adapter construction, model spawn or
            # task execution.
            from qwen_train.arm_workspace import resolve_required_workspace_root

            workspace_root = resolve_required_workspace_root(
                code_root=_HERE.parent,
            )
            exec_workspace_root = workspace_root
            os.environ[F2_TRAJ_DIR_ENV] = str(resolve_f2_traj_dir(workspace_root))

            task_binding: dict[str, Any] = {}
            if args.instance_id:
                task_binding = bind_task_environment(
                    instance_id=args.instance_id,
                    task_id=args.task_id or args.instance_id,
                    workspace_root=workspace_root,
                    repo_root=os.environ.get("SWARM_F2_REPO_ROOT", str(_HERE.parent)),
                )
                # PER-ARM FILESYSTEM ISOLATION (design §13.5). Reset the task
                # repository to the declared base_commit + authorized test patch
                # BEFORE the arm can mutate it, so an arm never inherits another
                # arm's (or a failed attempt's) repository state. Reuses the F1
                # arm_workspace machinery; fail closed.
                from qwen_train.arm_workspace import prepare_arm_workspace

                prepare_arm_workspace(
                    workspace_root, str(task_binding.get("base_commit") or "")
                )

            adapter = F2ExecutionAdapter(
                arm=artifact.arm,
                manifest_path=manifest_path,
                repo_root=os.environ.get("SWARM_F2_REPO_ROOT", str(_HERE.parent)),
                workspace_root=workspace_root,
                port=args.port,
            )
            adapter_evidence = adapter.execute_arm_real(
                task_prompt=args.task_prompt,
                task_id=args.task_id or (task_binding.get("instance_id") or ""),
                rollout_id=rollout_id,
                trajectory_run_id=trajectory_run_id,
                agent_id=args.agent_id,
                port=args.port,
            )
            execution_result = {
                "delegated": False,
                "mode": adapter_evidence.get("mode"),
                "p2_pid": adapter_evidence.get("p2_pid"),
                "p2_launcher_pid": adapter_evidence.get("p2_launcher_pid"),
                "serving_pid_source": adapter_evidence.get("serving_pid_source"),
                "backend_healthy": adapter_evidence.get("backend_healthy"),
                "task_binding": task_binding,
            }
        elif exec_cmd:
            import subprocess

            proc = subprocess.run(exec_cmd, shell=True, capture_output=True, text=True, timeout=30)
            execution_result = {
                "delegated": True,
                "cmd": exec_cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
            }
        else:
            execution_result = {
                "delegated": True,
                "reason": "no F2_ARM_EXEC_CMD provided (first implementation step); "
                          "live worker execution is a future authorized step",
            }

        exec_end = time.time()

        # -- P2 delivery-evidence transport (authorized per F2 worker-exec §10) --
        # P2 is the authoritative producer of delivery evidence at the
        # model-facing delivery seam.  We read P2's trajectory record here.
        # P1's own computation (above) is kept only as a verification baseline.
        # Transport decision: trajectory readback — the narrowest existing
        # mechanism (no new IPC).  P2 writes delivery_evidence into
        # data/trajectories/{run_id}.jsonl during execution; this worker reads
        # it back and uses P2's values in the receipt.
        # Reuse the workspace identity resolved (and validated) on the execution
        # path above. Do NOT re-derive it here from a code-root default: an
        # executing arm has no fallback to the main repository, and this read
        # must not reintroduce one. Outside --execute there is no resolved
        # workspace, so the documented code-root derivation is preserved for the
        # delegated path.
        workspace_root = (
            exec_workspace_root
            if exec_workspace_root is not None
            else Path(os.environ.get("SWARM_WORKSPACE_ROOT", str(_HERE.parent)))
        )
        p2_evidence: dict[str, Any] | None = None
        p2_agent_id: str = ""
        if adapter_evidence is not None:
            # Make P2's explicit write outcomes available for failure
            # classification (D3) before any evidence lookup.
            _ADAPTER_WRITE_OUTCOMES.set(list(adapter_evidence.get("evidence_write_outcomes") or []))
        if structured_execute or exec_cmd:
            p2_evidence = _read_p2_delivery_evidence(workspace_root, rollout_id)
            if p2_evidence is None:
                # F2-OP-INFRA-004 §2 (D3): a FAILED write is not the same as
                # evidence that never existed. P2 emits an explicit
                # f2_evidence_write_outcome line on its stdout (captured by the
                # adapter); if one of those reports a failure for THIS rollout we
                # must say so rather than claiming the evidence was absent.
                wf = _read_evidence_write_outcome(_ADAPTER_WRITE_OUTCOMES.get())
                if wf is not None:
                    raise RuntimeError(
                        "F2 fail-closed: EVIDENCE_WRITE_FAILED — P2 reported that "
                        f"persisting delivery evidence failed for rollout_id="
                        f"{rollout_id!r} ({wf.get('error_type')}: "
                        f"{wf.get('error')}). This is NOT missing evidence and must "
                        "not be reported as EVIDENCE_NOT_FOUND."
                    )
                raise RuntimeError(
                    "F2 fail-closed: EVIDENCE_NOT_FOUND — no unambiguous P2 delivery "
                    f"evidence bound to rollout_id={rollout_id!r} in the trajectory "
                    "store, and P2 reported no write failure. An F2 arm with real "
                    "execution MUST receive P2-produced delivery evidence for its own "
                    "rollout; missing evidence, another rollout's evidence, or "
                    "conflicting duplicates are not a valid receipt."
                )
            p2_agent_id = _verify_p2_evidence_binding(p2_evidence, artifact)

        # Use P2-produced values as authoritative; keep P1 baseline for comparison
        if p2_evidence:
            lesson_block_hash = p2_evidence.get("lesson_block_hash", lesson_block_hash)
            final_prompt_hash = p2_evidence.get("final_prompt_hash", final_prompt_hash)
            delivery_timestamp = p2_evidence.get("delivery_timestamp", delivery_timestamp)
            p2_serving_pid = p2_evidence.get("serving_pid", "")
            p2_serving_start_time = p2_evidence.get("serving_start_time", "")
            evidence_source = "p2_trajectory"
        else:
            p2_serving_pid = ""
            p2_serving_start_time = ""
            evidence_source = "p1_computed_delegated"

        # Post-run workspace-mutation evidence. Observation only: recorded on the
        # receipt so an independent reviewer can see every path the arm touched.
        workspace_mutation = _capture_workspace_mutation(
            workspace_root,
            str((artifact.task_readiness or {}).get("base_commit", "")),
        )
        # ENGINEERING classification of the observation (not an exclusion rule).
        _rfs = tuple((artifact.task_readiness or {}).get("relevant_file_set") or ())
        workspace_mutation["classification"] = _classify_workspace_mutation(
            workspace_mutation, relevant_file_set=_rfs
        )

        receipt: dict[str, Any] = {
            "experiment_id": artifact.experiment_id,
            "protocol_version": artifact.protocol_version,
            "git_sha": artifact.git_sha,
            "task_id": artifact.task_id,
            "arm": artifact.arm,
            "rollout_id": rollout_id,
            "trajectory_run_id": trajectory_run_id,
            "process_identity": process_identity,
            "treatment_identity": {
                "ordered_lesson_ids": [le.lesson_id for le in artifact.ordered_lessons],
                "ordered_lesson_hashes": [le.lesson_hash for le in artifact.ordered_lessons],
                "lesson_l_id": artifact.lesson_l_id,
                "lesson_l_hash": artifact.lesson_l_hash,
            },
            "tx_identity": {
                "content_address": artifact.content_address,
                "treatment_set_hash": artifact.treatment_set_hash,
            },
            "manifest_identity": {
                "content_address": artifact.content_address,
                "manifest_hash": artifact.manifest_hash,
                "manifest_path": str(manifest_path),
                "source_t_manifest": artifact.promotion_proof_ref,
            },
            "delivery_identity": {
                "lesson_block_hash": lesson_block_hash,
                "final_prompt_hash": final_prompt_hash,
                "delivery_timestamp": delivery_timestamp,
                "evidence_source": evidence_source,
            },
            "lesson_block_hash": lesson_block_hash,
            "final_prompt_hash": final_prompt_hash,
            "delivery_timestamp": delivery_timestamp,
            "p2_delivery_evidence": {
                "source": evidence_source,
                "serving_pid": p2_serving_pid,
                "serving_start_time": p2_serving_start_time,
                # Model/agent identity reported by P2 (e.g. "coder"). Distinct
                # from the F2 arm, which is the manifest field verified above.
                "agent_identity": p2_agent_id,
                "manifest_treatment_set_hash": p2_evidence.get("manifest_treatment_set_hash", "") if p2_evidence else "",
                "manifest_content_address": p2_evidence.get("manifest_content_address", "") if p2_evidence else "",
            },
            "execution_timestamps": {"started": exec_start, "completed": exec_end},
            "verification_result": "verified",
            "failure_reason": None,
            "exit_status": 0,
            "outcome_evidence": execution_result,
            "delivered_artifact": delivered_actual,
            # Integrity evidence: which repository paths this arm touched.
            "workspace_mutation_evidence": workspace_mutation,
        }
        print(_arm_report(receipt))
        return 0

    except Exception as exc:  # noqa: BLE001 - fail closed
        error_report = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "arm": args.arm,
            "manifest_path": str(manifest_path),
            "pid": os.getpid(),
        }
        print(_arm_report(error_report), file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(run_worker())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}), file=sys.stderr)
        sys.exit(2)