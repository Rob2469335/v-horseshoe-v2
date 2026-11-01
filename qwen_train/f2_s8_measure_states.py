"""F2-S8 measured base/gold execution-state derivation for the built candidates.

Why this module exists
----------------------
Before the VM resource upgrade, ``SWARM_S8_BASE_STATE_DIGEST`` /
``SWARM_S8_GOLD_STATE_DIGEST`` were **operator assertions**: any 64-lowercase-hex
string passed the shape check in ``f2_s8_compose`` (lines 146-156), so nothing
demonstrated that either value measured a real execution state.  After the VM
upgrade the two historical digests became stale, and the repository had **no
authoritative procedure** for deriving current ones -- they were classified an
owner determination rather than an agent inference.

This module is that derivation procedure.  It is **read-only**: it measures the
already-built host workspaces and emits ``f2-execution-state/v1`` records
(``qwen_train/f2_execution_state``) whose ``measured_state_digest`` is the value
intended for the S8 environment variables.  It never consumes an authorization
grant, never runs a task's declared test command, and never mutates a workspace.

What it measures -- and what it deliberately does not
-----------------------------------------------------
For each ``status == "built"`` entry in the Stage-1 build manifest:

* **base state** -- the ``repo`` tree (``base_commit`` + ``test_patch``);
* **gold state** -- the ``repo_gold`` tree (base + ``gold_patch``).

The two sides are measured **independently** from their own trees; their digests
are never forced to differ by touching an input.  The interpreter identity and
the installed-distribution set are read from **that task's own virtual
environment** (the side's venv), not from the measuring process, so the record
describes the environment that will actually execute the declared command.

Deliberately **not** measured: VM vCPU/RAM.  Resource configuration is not part
of an execution state -- it is the environment's capacity, not the state of the
evaluated program.  The environment *identity* is a separate field.

Environment identity
--------------------
``environment_identity`` names the environment the measurement was taken in, built
from measured facts (platform + the task venv's interpreter identity) rather than
from a constant that could outlive the machine it names.  A host measurement and
a guest measurement of the same tree therefore agree on ``tree_digest`` (staging
is byte-identical) but carry **different** ``environment_identity`` values, which
is the intended distinction: the tree is portable, the environment is not.

``run_nonce`` -- declared, not implied
--------------------------------------
These are **baseline** records, so ``run_nonce`` is the DETERMINISTIC label
``stage1-baseline-<task>-<side>``, not a per-attempt random nonce.  That is a
deliberate, declared property: the same workspace must always derive the same
baseline digest, so the value is reproducible.  It follows that **these records
carry no replay protection and must not be presented as run-time evidence**.
The authoritative per-execution records -- the ones the proposal's replay rule
(``run_nonce`` unique per grant/task/side/attempt, single-use in the
``f2_authz_ledger``) applies to -- are produced in the guest at execution time.
This module measures *what is staged*; it does not attest *what ran*.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qwen_train import f2_evaluator  # noqa: E402
from qwen_train.f2_execution_state import (  # noqa: E402
    SIDE_BASE,
    SIDE_GOLD,
    MeasurementError,
    build_execution_state,
    measure_tree,
    verify_execution_state_pair,
)

__all__ = [
    "SCHEMA_VERSION",
    "DerivationError",
    "resolve_side_paths",
    "measure_venv_environment",
    "derive_task_states",
    "derive_manifest_states",
    "main",
]

SCHEMA_VERSION = "f2-s8-state-derivation/v1"

DEFAULT_BUILD_MANIFEST = r"C:\Users\rober\F2_EVIDENCE\s8\pilot_build_phase2\build_manifest.json"
DEFAULT_PATCHES_MANIFEST = (
    r"C:\Users\rober\F2_EVIDENCE\s8\pilot_build_phase2\phase2_patches_manifest.json"
)

# Read-only subprocess hygiene: never write byte-code into a measured tree and
# never let a user site-packages shadow the venv's own distribution set.
_MEASURE_ENV = {
    **os.environ,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
}

_PROBE = r"""
import json, sys
import importlib.metadata as md
names = set()
for dist in md.distributions():
    try:
        name = dist.metadata["Name"]
    except Exception:
        name = None
    if not name:
        continue
    names.add(f"{name}=={dist.version}")
ordered = sorted(names)
import hashlib
digest = hashlib.sha256(("\n".join(ordered) + "\n").encode("utf-8")).hexdigest() if ordered else ""
impl = getattr(sys.implementation, "name", "python")
version = ".".join(str(p) for p in sys.version_info[:3])
print(json.dumps({
    "interpreter": f"{impl}-{version}:" + sys.executable.replace("\\", "/"),
    "distributions_digest": digest,
    "distribution_count": len(ordered),
    "version": version,
}))
"""


class DerivationError(RuntimeError):
    """A derivation could not be completed (fail closed)."""


def resolve_side_paths(entry: Mapping[str, Any], side: str) -> tuple[Path, Path]:
    """Return ``(tree_root, venv_dir)`` for one side of one built task.

    ``tree_root`` is the evaluated source tree; ``venv_dir`` is the environment
    whose interpreter and distribution set describe that side.  The two layouts
    the builder produces are resolved here, in one place:

    * uv-owned tasks -- uv creates ``repo\\.venv`` / ``repo_gold\\.venv``;
    * pip tasks -- the builder creates ``.venv`` / ``.venv_gold`` at the
      workspace root.
    """
    if side not in (SIDE_BASE, SIDE_GOLD):
        raise DerivationError(f"unknown side {side!r}")
    ws = Path(str(entry["workspace"]))
    if bool(entry.get("uses_uv")):
        tree = ws / ("repo" if side == SIDE_BASE else "repo_gold")
        venv = tree / ".venv"
    else:
        tree = ws / ("repo" if side == SIDE_BASE else "repo_gold")
        venv = ws / (".venv" if side == SIDE_BASE else ".venv_gold")
    return tree, venv


def measure_venv_environment(venv_dir: Path | str) -> dict[str, Any]:
    """Read interpreter + distribution identity from a venv (read-only).

    The venv's own interpreter is probed in a child process with byte-code
    writing disabled, so measuring a workspace cannot mutate it.  A venv that
    cannot answer is a hard failure: an environment whose interpreter is missing
    or broken is not an executable state, and guessing one would manufacture
    evidence.
    """
    venv = Path(venv_dir)
    python = venv / "Scripts" / "python.exe"
    if not python.is_file():
        raise DerivationError(f"venv interpreter missing: {python}")
    try:
        proc = subprocess.run(
            [str(python), "-B", "-I", "-c", _PROBE],
            capture_output=True,
            timeout=300,
            env=_MEASURE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DerivationError(f"could not probe venv {python}: {exc}") from exc
    if proc.returncode != 0:
        raise DerivationError(
            f"venv probe failed rc={proc.returncode}: "
            f"{proc.stderr.decode(errors='replace').strip()[:300]}"
        )
    try:
        data = json.loads(proc.stdout.decode("utf-8").splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise DerivationError(f"venv probe returned unparseable output: {exc}") from exc
    if not data.get("interpreter") or not data.get("distributions_digest"):
        raise DerivationError("venv probe omitted interpreter or distribution digest")
    return data


def derive_task_states(
    task_id: str,
    entry: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    environment_label: str,
) -> dict[str, Any]:
    """Derive the measured base and gold execution states for one built task."""
    if str(entry.get("status")) != "built":
        raise DerivationError(f"{task_id}: not a built entry (status={entry.get('status')!r})")

    test_command = str(patch.get("test_cmd") or "").strip()
    if not test_command:
        raise DerivationError(f"{task_id}: no declared test_cmd in the patches manifest")
    test_patch = str(patch.get("test_patch") or "")
    gold_patch = str(patch.get("gold_patch") or "")
    for label, path in (("test_patch", test_patch), ("gold_patch", gold_patch)):
        if not path or not Path(path).is_file():
            raise DerivationError(f"{task_id}: {label} missing on disk: {path!r}")
    base_commit = str(patch.get("base_commit") or entry.get("base_commit") or "")
    if not base_commit:
        raise DerivationError(f"{task_id}: no base_commit")

    records: dict[str, Any] = {}
    env_ids: dict[str, str] = {}
    for side in (SIDE_BASE, SIDE_GOLD):
        tree, venv = resolve_side_paths(entry, side)
        if not tree.is_dir():
            raise DerivationError(f"{task_id}: {side} tree missing: {tree}")
        env = measure_venv_environment(venv)
        env_identity = (
            f"{environment_label}/py{env['version']}/"
            f"{env['interpreter'].split(':', 1)[0]}"
        )
        try:
            tree_measurement = measure_tree(tree, label=f"{task_id}:{side}")
        except MeasurementError as exc:
            raise DerivationError(f"{task_id}: {side} tree unreadable: {exc}") from exc
        record = build_execution_state(
            instance_id=task_id,
            repository=str(patch.get("repo") or ""),
            base_commit=base_commit,
            execution_state_identity=side,
            environment_identity=env_identity,
            test_command=test_command,
            tree=tree_measurement,
            interpreter=env["interpreter"],
            installed_distributions_digest=env["distributions_digest"],
            installed_distribution_count=int(env["distribution_count"]),
            evaluator_implementation_digest=f2_evaluator.implementation_digest(),
            procedure_id=f2_evaluator.PROCEDURE_ID,
            protocol_version=f2_evaluator.PROTOCOL_VERSION,
            test_patch_digest=_file_sha256(test_patch),
            run_nonce=f"stage1-baseline-{task_id}-{side}",
            gold_patch_digest=_file_sha256(gold_patch) if side == SIDE_GOLD else "",
        )
        if not record.verify_digest():
            raise DerivationError(f"{task_id}: {side} record failed its own digest check")
        records[side] = record.to_dict()
        env_ids[side] = env_identity

    verdict = verify_execution_state_pair(
        instance_id=task_id,
        repository=str(patch.get("repo") or ""),
        base_commit=base_commit,
        base_record=records[SIDE_BASE],
        gold_record=records[SIDE_GOLD],
        environment_identity=env_ids[SIDE_BASE],
    )
    if not verdict.ok:
        raise DerivationError(f"{task_id}: pair verification failed: {verdict.state}: {verdict.detail}")

    return {
        "task_id": task_id,
        "uses_uv": bool(entry.get("uses_uv")),
        "base": records[SIDE_BASE],
        "gold": records[SIDE_GOLD],
        "pair_state": verdict.state,
        "states_distinct": records[SIDE_BASE]["measured_state_digest"]
        != records[SIDE_GOLD]["measured_state_digest"],
        "base_state_digest": records[SIDE_BASE]["measured_state_digest"],
        "gold_state_digest": records[SIDE_GOLD]["measured_state_digest"],
        "base_tree_digest": records[SIDE_BASE]["tree_digest"],
        "gold_tree_digest": records[SIDE_GOLD]["tree_digest"],
    }


def derive_manifest_states(
    *,
    build_manifest: Path | str,
    patches_manifest: Path | str,
    only: Sequence[str] = (),
    environment_label: str,
    progress: Any = None,
) -> dict[str, Any]:
    """Derive measured states for every built entry of a Stage-1 manifest."""
    say = progress or (lambda _m: None)
    manifest_path = Path(build_manifest)
    build_bytes = manifest_path.read_bytes()
    import hashlib

    build_digest = hashlib.sha256(build_bytes).hexdigest()
    build = json.loads(build_bytes.decode("utf-8"))
    patches_doc = json.loads(Path(patches_manifest).read_text(encoding="utf-8"))
    patches = {
        str(p["task_id"]): p
        for p in (patches_doc.get("tasks") or patches_doc.get("patches") or [])
    }
    if not patches:
        raise DerivationError("patches manifest contained no task entries")

    selected = [t for t in build if str(build[t].get("status")) == "built"]
    if only:
        wanted = set(only)
        unknown = wanted - set(selected)
        if unknown:
            raise DerivationError(f"requested tasks are not built entries: {sorted(unknown)}")
        selected = [t for t in selected if t in wanted]

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for task_id in sorted(selected):
        patch = patches.get(task_id)
        if not patch:
            failures.append({"task_id": task_id, "detail": "no entry in patches manifest"})
            continue
        say(task_id)
        try:
            results.append(derive_task_states(task_id, build[task_id], patch, environment_label=environment_label))
        except DerivationError as exc:
            failures.append({"task_id": task_id, "detail": str(exc)})

    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_manifest_digest": build_digest,
        "build_manifest": str(manifest_path),
        "patches_manifest": str(patches_manifest),
        "environment_label": environment_label,
        "protocol_version": f2_evaluator.PROTOCOL_VERSION,
        "procedure_id": f2_evaluator.PROCEDURE_ID,
        "evaluator_implementation_digest": f2_evaluator.implementation_digest(),
        "derived_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
    }


def _file_sha256(path: str) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m qwen_train.f2_s8_measure_states",
        description="Derive measured f2-execution-state/v1 records for built S8 candidates.",
    )
    parser.add_argument("--build-manifest", default=DEFAULT_BUILD_MANIFEST)
    parser.add_argument("--patches-manifest", default=DEFAULT_PATCHES_MANIFEST)
    parser.add_argument("--only", action="append", default=[], help="limit to one task id (repeatable)")
    parser.add_argument("--environment-label", default="f2-s8-host-baseline")
    parser.add_argument("--out", default="", help="write the derivation JSON here (default: stdout)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        doc = derive_manifest_states(
            build_manifest=args.build_manifest,
            patches_manifest=args.patches_manifest,
            only=args.only,
            environment_label=args.environment_label,
            progress=(None if args.quiet else (lambda m: print(f"  measuring {m}", file=sys.stderr))),
        )
    except DerivationError as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(doc, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(
            f"wrote {doc['derived_count']} derived task(s), {doc['failed_count']} failure(s) -> {args.out}",
            file=sys.stderr,
        )
    else:
        print(payload)
    return 0 if doc["failed_count"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
