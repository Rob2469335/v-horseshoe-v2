"""D4 — read-only post-batch audit of retained S8 evidence.

WHY THIS EXISTS
---------------
The frozen artifact contract does **not** promote raw JUnit to a required
trusted-artifact role, so a batch report's own ``base_junit_sha256`` /
``gold_junit_sha256`` and its declared verdicts are the only in-report claims
about what the retained evidence said.  Those claims are produced by the same
code that produced the batch; nothing independently re-derived them from the
bytes on disk.  This module is the compensating **detection** control: after a
batch completes it re-reads the retained JUnit files and recomputes, with the
authoritative evaluator, what the report says they prove.

WHAT IT CAN AND CANNOT ESTABLISH
--------------------------------
* It CAN establish that, for every task row that declares a JUnit hash, the
  named file exists in the bound staging root, hashes to the declared digest,
  parses, and yields the declared ``collection_errors``,
  ``collection_error_cause`` and ``verdict`` under the task's declared
  FAIL_TO_PASS / PASS_TO_PASS contract and the gold patch's changed modules.
* It CAN establish that a row claiming no evidence is an infrastructure row
  whose reason code is one of the registered F0 §12 infrastructure reasons,
  rather than a scientific claim with its evidence quietly missing.
* It CAN establish that the report is bound to the manifest bytes, the staging
  root, the registration and a run id, and that no evidence file in the staging
  root belongs to a different task or an earlier batch.
* It CANNOT establish that the JUnit bytes themselves are authentic — a writer
  who can rewrite the staging root can rewrite both the evidence and the
  report.  It also cannot establish that an operator did not deliberately
  delete evidence *and* rewrite the report to match; only an independent
  evidence anchor could.  That residual risk is documented, not eliminated.

FAIL-CLOSED RULES
-----------------
A missing declared value is a failure, never a pass.  A malformed or
unparseable artifact is a failure.  A discrepancy is a failure.  Only a report
whose every task row was independently re-derived and matched is ``PASS``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from qwen_train.f2_evaluator import (
    changed_module_paths,
    enum_member_values,
    evaluate_execution,
)
from qwen_train.f2_population_acquire import DATASET_ROOTS

__all__ = [
    "SCHEMA",
    "AUDIT_SCHEMA_VERSION",
    "BatchAuditError",
    "BatchAuditBlocked",
    "TaskContract",
    "audit_batch",
    "audit_payload_is_intact",
    "load_contracts",
    "contract_from_record",
    "write_audit",
    "load_audit",
    "require_passing",
    "main",
]

SCHEMA = "f2-s8-post-batch-audit/v1"
#: Alias kept explicit for callers that name the schema by version.
AUDIT_SCHEMA_VERSION = SCHEMA

#: Retained per-side evidence naming: ``<task_id>.<side>.junit.hostcopy.xml``.
EVIDENCE_SUFFIX = ".junit.hostcopy.xml"
SIDES = ("base", "gold")

VALID_CAUSES = frozenset({"environment", "project", "unknown"})
VALID_VERDICTS = frozenset({"pass", "fail", "error"})
VALID_OUTCOME_CLASSES = frozenset({"scientific", "infrastructure"})

#: Reason codes.  Each names a distinct, reviewable failure mode; the audit
#: verdict is FAIL whenever any one of them is present.
REASON_BATCH_UNREADABLE = "BATCH_UNREADABLE"
REASON_MANIFEST_UNREADABLE = "MANIFEST_UNREADABLE"
REASON_BATCH_BINDING_MISSING = "BATCH_BINDING_MISSING"
REASON_MANIFEST_DIGEST_MISMATCH = "MANIFEST_DIGEST_MISMATCH"
REASON_MANIFEST_PATH_MISMATCH = "MANIFEST_PATH_MISMATCH"
REASON_STAGING_ROOT_MISMATCH = "STAGING_ROOT_MISMATCH"
REASON_RUN_ID_MISSING = "RUN_ID_MISSING"
REASON_RUN_ID_MISMATCH = "RUN_ID_MISMATCH"
REASON_REGISTRATION_MISSING = "REGISTRATION_MISSING"
REASON_REGISTRATION_MISMATCH = "REGISTRATION_MISMATCH"
REASON_REVISION_MISMATCH = "REVISION_MISMATCH"
REASON_NO_TASKS = "NO_TASKS"
REASON_TASK_DUPLICATE = "TASK_DUPLICATE"
REASON_TASK_NOT_IN_MANIFEST = "TASK_NOT_IN_MANIFEST"
REASON_TASK_NOT_BUILT = "TASK_NOT_BUILT"
REASON_CONTRACT_MISSING = "CONTRACT_MISSING"
REASON_CONTRACT_MALFORMED = "CONTRACT_MALFORMED"
REASON_DECLARED_HASH_MALFORMED = "DECLARED_HASH_MALFORMED"
REASON_JUNIT_MISSING = "JUNIT_MISSING"
REASON_JUNIT_UNDECLARED = "JUNIT_UNDECLARED"
REASON_JUNIT_HASH_MISMATCH = "JUNIT_HASH_MISMATCH"
REASON_JUNIT_MALFORMED = "JUNIT_MALFORMED"
REASON_EVIDENCE_DUPLICATE = "EVIDENCE_DUPLICATE"
REASON_UNEXPECTED_EVIDENCE_FILE = "UNEXPECTED_EVIDENCE_FILE"
REASON_DECLARED_COLLECTION_ERRORS_MISSING = "DECLARED_COLLECTION_ERRORS_MISSING"
REASON_DECLARED_COLLECTION_ERRORS_MALFORMED = "DECLARED_COLLECTION_ERRORS_MALFORMED"
REASON_DECLARED_CAUSE_MISSING = "DECLARED_CAUSE_MISSING"
REASON_DECLARED_CAUSE_MALFORMED = "DECLARED_CAUSE_MALFORMED"
REASON_DECLARED_VERDICT_MISSING = "DECLARED_VERDICT_MISSING"
REASON_DECLARED_VERDICT_MALFORMED = "DECLARED_VERDICT_MALFORMED"
REASON_COLLECTION_ERRORS_MISMATCH = "COLLECTION_ERRORS_MISMATCH"
REASON_CAUSE_MISMATCH = "CAUSE_MISMATCH"
REASON_VERDICT_MISMATCH = "VERDICT_MISMATCH"
REASON_ADMISSION_CONTRADICTED = "ADMISSION_CONTRADICTED"
REASON_EVIDENCE_MISSING = "EVIDENCE_MISSING"
REASON_INFRA_UNCLASSIFIED = "INFRA_UNCLASSIFIED"
REASON_OUTCOME_CLASS_UNKNOWN = "OUTCOME_CLASS_UNKNOWN"
REASON_TOTALS_MISMATCH = "TOTALS_MISMATCH"
REASON_AUDIT_DIGEST_MISMATCH = "AUDIT_DIGEST_MISMATCH"
REASON_EVIDENCE_INDEX_MISSING = "EVIDENCE_INDEX_MISSING"
REASON_EVIDENCE_INDEX_MALFORMED = "EVIDENCE_INDEX_MALFORMED"
REASON_EVIDENCE_INDEX_BINDING_MISMATCH = "EVIDENCE_INDEX_BINDING_MISMATCH"
REASON_EVIDENCE_INDEX_HASH_MISMATCH = "EVIDENCE_INDEX_HASH_MISMATCH"
REASON_EVIDENCE_INDEX_INCOMPLETE = "EVIDENCE_INDEX_INCOMPLETE"


#: Non-scientific outcomes that occur BEFORE execution: the driver refused to
#: spend an attempt (e.g. the task already holds a terminal scientific record).
#: They legitimately carry no retained evidence, so a no-evidence row with one of
#: these reasons is validly audited.  This is a closed, explicit set -- an
#: arbitrary string never qualifies.
PRE_EXECUTION_REASONS = frozenset({"ledger_refused"})


class BatchAuditError(RuntimeError):
    """The audit could not be conducted at all (fail closed)."""


class BatchAuditBlocked(RuntimeError):
    """A batch audit did not pass, so progression/credit is refused."""


_MISSING = object()

#: F0 §12 infrastructure reasons, imported lazily so this module stays free of
#: the pilot's heavier import graph.  The equality is asserted by a test.
def _infrastructure_reasons() -> frozenset[str]:
    from qwen_train.f2_s8_pilot import INFRASTRUCTURE_REASONS

    return frozenset(INFRASTRUCTURE_REASONS)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _audit_digest(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "audit_digest"}
    return _sha256_bytes(_canonical_bytes(body))


def audit_payload_is_intact(payload: Mapping[str, Any]) -> bool:
    """True only when a result carries a valid digest over its own content.

    This is the in-memory counterpart of :func:`load_audit`.  A caller that
    passes a mapping (rather than a file) must not be able to assert PASS
    without also carrying the integrity value: CWE-354 requires the check value
    to be validated *before* the data is used, not merely present.
    """
    declared = str(payload.get("audit_digest") or "")
    return _is_sha256(declared) and declared == _audit_digest(payload)


# ---------------------------------------------------------------------------
# task contracts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaskContract:
    """The declared test contract + the gold patch's own changed modules."""

    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    changed_modules: tuple[str, ...]
    #: Identity bindings (see :func:`qwen_train.f2_evaluator.canonical_node_identity`).
    #: Carried ON THE CONTRACT so the scorer derives them the same way the
    #: pre-check does, from the same declarations -- a binding supplied in one
    #: place only is exactly the disagreement this exists to prevent.
    #:
    #: ``workspace`` is the built task workspace; the EXECUTION root is its
    #: ``repo``/``repo_gold`` per side, resolved at the comparison. ``dataset_root``
    #: comes from the source's declared harness contract. ``enum_values`` is read
    #: from the project's own source. All default to empty, which maps nothing and
    #: preserves the previous behaviour for callers that do not supply them.
    workspace: str = ""
    dataset_root: str = ""
    enum_values: Mapping[str, Mapping[str, int]] | None = None


def _enum_class_names(values: Sequence[str]) -> list[str]:
    """Class names the declared ids reference as ``Name.MEMBER`` (bounded)."""
    names: set[str] = set()
    for value in values:
        names.update(re.findall(r"\b([A-Za-z_]\w*)\.[A-Za-z_]\w*\b", str(value)))
    return sorted(names)


def _as_node_ids(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        # Input records sometimes carry a JSON-encoded list.  A bare node id is
        # refused: a single string is an ambiguous declaration, not a list.
        try:
            value = json.loads(value)
        except Exception as exc:  # noqa: BLE001 - normalise to a contract error
            raise BatchAuditError(
                f"{label} is a bare string, not a list of node ids"
            ) from exc
    if not isinstance(value, (list, tuple)):
        raise BatchAuditError(f"{label} must be a list of node ids")
    out: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if text:
            out.append(text)
    if len(set(out)) != len(out):
        raise BatchAuditError(f"{label} declares duplicate node ids")
    return tuple(out)


def contract_from_record(
    record: Mapping[str, Any], *, workspace: str = ""
) -> TaskContract:
    """Build a contract from a pilot input record (the canonical source).

    The identity bindings are derived HERE, from the same declarations the
    pre-check reads: ``dataset_root`` from the record's own ``source``, and
    ``enum_values`` from the built project's source. Nothing is inferred from the
    expected ids themselves.
    """
    f2p = _as_node_ids(
        record.get("FAIL_TO_PASS", record.get("fail_to_pass")), "FAIL_TO_PASS"
    )
    p2p = _as_node_ids(
        record.get("PASS_TO_PASS", record.get("pass_to_pass")), "PASS_TO_PASS"
    )
    patch = str(record.get("patch") or "")
    source_key = str(record.get("source") or "swe-bench-live")
    enum_values: dict[str, dict[str, int]] = {}
    if workspace:
        enum_values = enum_member_values(
            Path(workspace) / "repo", _enum_class_names((*f2p, *p2p))
        )
    return TaskContract(
        fail_to_pass=f2p,
        pass_to_pass=p2p,
        changed_modules=changed_module_paths(patch),
        workspace=str(workspace or ""),
        dataset_root=DATASET_ROOTS.get(source_key, ""),
        enum_values=enum_values,
    )


def load_contracts(
    inputs_dir: Path | str, *, workspaces: Mapping[str, str] | None = None
) -> dict[str, TaskContract]:
    """Load every task contract from a pilot inputs directory, fail closed."""
    root = Path(inputs_dir)
    if not root.is_dir():
        raise BatchAuditError(f"inputs directory does not exist: {root}")
    by_task = {str(k): str(v) for k, v in (workspaces or {}).items()}
    contracts: dict[str, TaskContract] = {}
    for path in sorted(root.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise BatchAuditError(f"input record {path} is unreadable: {exc}") from exc
        if not isinstance(record, Mapping):
            raise BatchAuditError(f"input record {path} is not a JSON object")
        task_id = str(record.get("instance_id") or path.stem)
        try:
            contracts[task_id] = contract_from_record(
                record, workspace=by_task.get(task_id, ""))
        except BatchAuditError as exc:
            raise BatchAuditError(f"input record {path} is malformed: {exc}") from exc
    return contracts


# ---------------------------------------------------------------------------
# evidence discovery
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvidenceFile:
    task_id: str
    side: str
    path: Path


def _group_evidence(paths: Iterable[Path]) -> tuple[list[EvidenceFile], list[Path]]:
    """Split candidate evidence into recognised files and unexplained files.

    Recognition is on the *filename contract*, not on a directory listing being
    trusted: ``<task>.<side>.junit.hostcopy.xml`` with ``side in {base, gold}``.
    A file whose name does not fit is returned as unexplained, and so is a
    recognised name that collides with another.  Collision follows the HOST's
    case semantics (``os.path.normcase``): on Windows two spellings can name the
    same bytes and make the binding ambiguous; on POSIX they are distinct task
    ids and must not be collapsed.
    """
    recognised: list[EvidenceFile] = []
    unexplained: list[Path] = []
    seen: dict[tuple[str, str], Path] = {}
    for path in paths:
        path = Path(path)
        name = path.name
        if not name.endswith(EVIDENCE_SUFFIX):
            unexplained.append(path)
            continue
        stem = name[: -len(EVIDENCE_SUFFIX)]
        if "." not in stem:
            unexplained.append(path)
            continue
        task_id, side = stem.rsplit(".", 1)
        if side not in SIDES or not task_id:
            unexplained.append(path)
            continue
        key = (os.path.normcase(task_id), side)
        if key in seen:
            # A duplicate is not silently collapsed: the caller must fail.
            recognised.append(EvidenceFile(task_id, side, path))
            unexplained.append(seen[key])
            continue
        seen[key] = path
        recognised.append(EvidenceFile(task_id, side, path))
    return recognised, unexplained


def _expected_path(staging: Path, task_id: str, side: str) -> Path:
    return staging / f"{task_id}.{side}{EVIDENCE_SUFFIX}"


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def _discrepancy(
    task_id: str, side: str, reason_code: str, detail: str
) -> dict[str, str]:
    return {
        "task_id": task_id,
        "side": side,
        "reason_code": reason_code,
        "detail": detail,
    }


def _manifest_entries(document: Any) -> dict[str, Mapping[str, Any]]:
    if isinstance(document, Mapping):
        return {
            str(k): v for k, v in document.items() if isinstance(v, Mapping)
        }
    if isinstance(document, list):
        out: dict[str, Mapping[str, Any]] = {}
        for item in document:
            if isinstance(item, Mapping) and item.get("task_id"):
                out[str(item["task_id"])] = item
        return out
    raise BatchAuditError("manifest must be an object keyed by task_id or a list")


def _task_rows(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = document.get("tasks")
    if not isinstance(raw, list):
        raw = document.get("results")
    if not isinstance(raw, list):
        raise BatchAuditError("batch report has no 'tasks'/'results' list")
    rows = [r for r in raw if isinstance(r, Mapping)]
    if len(rows) != len(raw):
        raise BatchAuditError("batch report contains a non-object task row")
    return rows


def _sha256_file_or_empty(path: Path) -> str:
    """Hash a file for the batch header; an unreadable file is an empty value.

    The audit must still return a reviewable FAIL result when the report or
    manifest exists but cannot be read (permissions, a lock, I/O error), rather
    than raising out of the live batch path.
    """
    try:
        return _sha256_file(path)
    except OSError:
        return ""


def _base_result(
    *,
    results_path: Path,
    staging: Path,
    manifest_file: Path,
    counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "verdict": "FAIL",
        "batch": {
            "results_path": str(results_path),
            "results_sha256": _sha256_file_or_empty(results_path),
            "staging_root": str(staging),
            "manifest_path": str(manifest_file),
            "manifest_sha256": _sha256_file_or_empty(manifest_file),
            "evidence_index_path": "",
            "evidence_index_sha256": "",
            "run_id": "",
            "registration_id": "",
            "registration_sha256": "",
            "repository_revision": "",
            "task_ids": [],
        },
        "counts": dict(counts or {}),
        "tasks": [],
        "discrepancies": [],
        "reason_codes": [],
    }


def audit_batch(
    *,
    batch_results_path: Path | str,
    staging_root: Path | str,
    manifest_path: Path | str,
    contracts: Mapping[str, TaskContract] | None = None,
    inputs_dir: Path | str | None = None,
    expected_manifest_digest: str | None = None,
    expected_repository_revision: str | None = None,
    expected_run_id: str | None = None,
    expected_staging_root: Path | str | None = None,
    evidence_index_path: Path | str | None = None,
    require_evidence_index: bool = True,
    require_contracts: bool = True,
) -> dict[str, Any]:
    """Audit one completed batch and return a deterministic, reviewable result.

    ``contracts`` may be supplied directly (the live path already holds the
    task specs); otherwise ``inputs_dir`` is read.  A side that declares
    evidence but has no contract cannot be re-derived, so it fails closed.
    """
    results_path = Path(batch_results_path)
    staging = Path(staging_root)
    manifest_file = Path(manifest_path)
    result = _base_result(
        results_path=results_path, staging=staging, manifest_file=manifest_file
    )
    discrepancies: list[dict[str, str]] = result["discrepancies"]

    # ---- load report ------------------------------------------------------
    try:
        report_bytes = results_path.read_bytes()
        report = json.loads(report_bytes.decode("utf-8"))
        if not isinstance(report, Mapping):
            raise ValueError("not a JSON object")
        rows = _task_rows(report)
    except Exception as exc:  # noqa: BLE001 - fail closed, but still reviewable
        discrepancies.append(
            _discrepancy("", "", REASON_BATCH_UNREADABLE, f"{results_path}: {exc}")
        )
        return _seal(result)

    # ---- load manifest ----------------------------------------------------
    try:
        manifest_bytes = manifest_file.read_bytes()
        manifest_digest = _sha256_bytes(manifest_bytes)
        manifest = _manifest_entries(json.loads(manifest_bytes.decode("utf-8")))
    except Exception as exc:  # noqa: BLE001
        discrepancies.append(
            _discrepancy(
                "", "", REASON_MANIFEST_UNREADABLE, f"{manifest_file}: {exc}"
            )
        )
        return _seal(result)

    result["batch"]["manifest_sha256"] = manifest_digest
    result["batch"]["results_sha256"] = _sha256_bytes(report_bytes)

    # ---- batch binding ----------------------------------------------------
    declared_manifest = str(report.get("manifest_digest") or "")
    if not declared_manifest:
        discrepancies.append(
            _discrepancy(
                "", "", REASON_BATCH_BINDING_MISSING,
                "report does not declare manifest_digest",
            )
        )
    elif declared_manifest != manifest_digest:
        discrepancies.append(
            _discrepancy(
                "", "", REASON_MANIFEST_DIGEST_MISMATCH,
                f"declared={declared_manifest} actual={manifest_digest}",
            )
        )
    if expected_manifest_digest and manifest_digest != expected_manifest_digest:
        discrepancies.append(
            _discrepancy(
                "", "", REASON_MANIFEST_DIGEST_MISMATCH,
                "manifest digest differs from the caller's expected digest "
                f"(actual={manifest_digest})",
            )
        )

    declared_manifest_path = str(report.get("manifest_path") or "")
    if not declared_manifest_path:
        discrepancies.append(
            _discrepancy(
                "", "", REASON_BATCH_BINDING_MISSING,
                "report does not declare manifest_path",
            )
        )
    else:
        try:
            same_manifest = (
                Path(declared_manifest_path).resolve() == manifest_file.resolve()
            )
        except OSError:
            same_manifest = False
        if not same_manifest:
            discrepancies.append(
                _discrepancy(
                    "", "", REASON_MANIFEST_PATH_MISMATCH,
                    f"declared={declared_manifest_path} actual={manifest_file}",
                )
            )

    declared_staging = str(report.get("staging_root") or "")
    if not declared_staging:
        discrepancies.append(
            _discrepancy(
                "", "", REASON_BATCH_BINDING_MISSING,
                "report does not declare staging_root",
            )
        )
    else:
        try:
            same_staging = Path(declared_staging).resolve() == staging.resolve()
        except OSError:
            same_staging = False
        if not same_staging:
            discrepancies.append(
                _discrepancy(
                    "", "", REASON_STAGING_ROOT_MISMATCH,
                    f"declared={declared_staging} actual={staging}",
                )
            )
    if expected_staging_root is not None:
        try:
            same_expected = Path(expected_staging_root).resolve() == staging.resolve()
        except OSError:
            same_expected = False
        if not same_expected:
            discrepancies.append(
                _discrepancy(
                    "", "", REASON_STAGING_ROOT_MISMATCH,
                    "staging root differs from the caller's expected root",
                )
            )

    run_id = str(report.get("run_id") or "")
    if not run_id:
        discrepancies.append(
            _discrepancy("", "", REASON_RUN_ID_MISSING, "report does not declare run_id")
        )
    elif expected_run_id and run_id != expected_run_id:
        discrepancies.append(
            _discrepancy(
                "", "", REASON_RUN_ID_MISMATCH,
                f"declared={run_id} expected={expected_run_id}",
            )
        )

    registration_id = str(report.get("registration_id") or "")
    registration_sha256 = str(report.get("registration_sha256") or "")
    if not registration_id or not _is_sha256(registration_sha256):
        discrepancies.append(
            _discrepancy(
                "", "", REASON_REGISTRATION_MISSING,
                "report does not declare a registration id and SHA-256",
            )
        )

    revision = str(report.get("repository_revision") or "")
    if expected_repository_revision and revision != expected_repository_revision:
        discrepancies.append(
            _discrepancy(
                "", "", REASON_REVISION_MISMATCH,
                "repository revision differs from the caller's expected revision "
                f"(declared={revision or '<absent>'})",
            )
        )

    result["batch"].update({
        "run_id": run_id,
        "registration_id": registration_id,
        "registration_sha256": registration_sha256,
        "repository_revision": revision,
    })

    # ---- evidence sidecar index (owner decision E1) -----------------------
    index: dict[str, Any] | None = None
    if evidence_index_path is None:
        if require_evidence_index:
            discrepancies.append(
                _discrepancy(
                    "", "", REASON_EVIDENCE_INDEX_MISSING,
                    "no sidecar evidence index was supplied for this batch",
                )
            )
    else:
        try:
            from qwen_train.f2_s8_evidence_index import load_index

            index = load_index(evidence_index_path)
        except Exception as exc:  # noqa: BLE001 - fail closed
            discrepancies.append(
                _discrepancy("", "", REASON_EVIDENCE_INDEX_MALFORMED, str(exc))
            )
        if index is not None:
            result["batch"]["evidence_index_path"] = str(Path(evidence_index_path))
            result["batch"]["evidence_index_sha256"] = str(
                index.get("index_digest") or ""
            )
            binding = index.get("batch") if isinstance(index.get("batch"), Mapping) else {}
            if (
                str(binding.get("results_sha256") or "")
                != result["batch"]["results_sha256"]
                or str(binding.get("manifest_digest") or "") != manifest_digest
                or (run_id and str(binding.get("run_id") or "") != run_id)
                or str(binding.get("registration_sha256") or "")
                != registration_sha256
            ):
                discrepancies.append(
                    _discrepancy(
                        "", "", REASON_EVIDENCE_INDEX_BINDING_MISMATCH,
                        "the sidecar index does not describe this report, manifest, "
                        "run id and registration",
                    )
                )
            entries = (
                index.get("entries") if isinstance(index.get("entries"), list) else []
            )
            by_record: dict[str, Mapping[str, Any]] = {}
            by_task: dict[str, Mapping[str, Any]] = {}
            for entry in entries:
                if isinstance(entry, Mapping):
                    by_record[str(entry.get("record_digest") or "")] = entry
                    by_task[str(entry.get("task_id") or "")] = entry
            for row in rows:
                task_id = str(row.get("task_id") or "")
                ledger_row = (
                    row.get("ledger_record")
                    if isinstance(row.get("ledger_record"), Mapping) else {}
                )
                record_digest = str(ledger_row.get("record_digest") or "")
                entry = by_record.get(record_digest) if record_digest else None
                if entry is None:
                    entry = by_task.get(task_id)
                if entry is None:
                    discrepancies.append(
                        _discrepancy(
                            task_id, "", REASON_EVIDENCE_INDEX_INCOMPLETE,
                            "the index has no entry for this task/record",
                        )
                    )
                    continue
                if record_digest and (
                    not entry.get("chain_bound")
                    or str(entry.get("record_digest") or "") != record_digest
                ):
                    discrepancies.append(
                        _discrepancy(
                            task_id, "", REASON_EVIDENCE_INDEX_BINDING_MISMATCH,
                            "the index entry is not bound to this row's "
                            f"record_digest {record_digest[:16]}...",
                        )
                    )
                    continue
                if bool(row.get("admitted")) and not entry.get("chain_bound"):
                    discrepancies.append(
                        _discrepancy(
                            task_id, "", REASON_EVIDENCE_INDEX_INCOMPLETE,
                            "an admitted row must be chain-bound in the index",
                        )
                    )
                for side in SIDES:
                    declared = str(row.get(f"{side}_junit_sha256") or "").strip()
                    indexed = (
                        (entry.get("sides") or {}).get(side) or {}
                    ).get("junit_sha256")
                    if declared and str(indexed or "") != declared:
                        discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_EVIDENCE_INDEX_HASH_MISMATCH,
                                f"declared={declared} index={indexed!r}",
                            )
                        )

    # ---- task identity ----------------------------------------------------
    if not rows:
        discrepancies.append(
            _discrepancy("", "", REASON_NO_TASKS, "batch report has no task rows")
        )
    task_ids = [str(r.get("task_id") or "") for r in rows]
    if any(not t for t in task_ids):
        discrepancies.append(
            _discrepancy("", "", REASON_BATCH_BINDING_MISSING,
                         "a task row has no task_id")
        )
    if len(set(task_ids)) != len(task_ids):
        duplicates = sorted({t for t in task_ids if task_ids.count(t) > 1})
        discrepancies.append(
            _discrepancy("", "", REASON_TASK_DUPLICATE,
                         f"duplicate task rows: {duplicates}")
        )
    result["batch"]["task_ids"] = sorted(set(task_ids))

    # ---- evidence discovery ----------------------------------------------
    try:
        candidates = sorted(staging.glob("*" + EVIDENCE_SUFFIX)) if staging.is_dir() else []
    except OSError as exc:
        candidates = []
        discrepancies.append(
            _discrepancy("", "", REASON_JUNIT_MISSING,
                         f"staging root unreadable: {exc}")
        )
    recognised, unexplained = _group_evidence(candidates)
    by_key: dict[tuple[str, str], list[EvidenceFile]] = {}
    for item in recognised:
        by_key.setdefault((os.path.normcase(item.task_id), item.side), []).append(item)

    # ---- contracts --------------------------------------------------------
    if contracts is None and inputs_dir is not None:
        try:
            # The built workspace per task, so the contract can carry the
            # execution-side identity bindings the pre-check used for it.
            contracts = load_contracts(inputs_dir, workspaces={
                str(k): str((v or {}).get("workspace") or "")
                for k, v in manifest.items()
            })
        except BatchAuditError as exc:
            contracts = {}
            discrepancies.append(
                _discrepancy("", "", REASON_CONTRACT_MALFORMED, str(exc))
            )
    contracts = dict(contracts or {})

    # ---- per-task audit ---------------------------------------------------
    task_reports: list[dict[str, Any]] = []
    referenced: set[str] = set()
    audited = 0
    passed = 0
    failed = 0
    infra_reasons = _infrastructure_reasons()

    for row in rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        task_discrepancies: list[dict[str, str]] = []
        sides_report: dict[str, Any] = {}
        manifest_entry = manifest.get(task_id)
        if manifest_entry is None:
            task_discrepancies.append(
                _discrepancy(task_id, "", REASON_TASK_NOT_IN_MANIFEST,
                             "task row is not present in the bound manifest")
            )
        elif str(manifest_entry.get("status") or "") != "built":
            task_discrepancies.append(
                _discrepancy(
                    task_id, "", REASON_TASK_NOT_BUILT,
                    f"manifest status={manifest_entry.get('status')!r}",
                )
            )

        attempted = bool(row.get("attempted", True))
        outcome = str(row.get("outcome") or "")
        outcome_class = str(row.get("outcome_class") or "")
        admitted = bool(row.get("admitted"))
        reason_code = str(row.get("reason_code") or "")
        if outcome_class and outcome_class not in VALID_OUTCOME_CLASSES:
            task_discrepancies.append(
                _discrepancy(task_id, "", REASON_OUTCOME_CLASS_UNKNOWN,
                             f"outcome_class={outcome_class!r}")
            )
        if attempted is False:
            # A row explicitly not attempted has no evidence to inspect.  It is
            # recorded, not silently dropped, but it cannot be credited.
            sides_report["_attempted"] = False
        else:
            for side in SIDES:
                declared_hash = str(row.get(f"{side}_junit_sha256") or "").strip()
                expected = _expected_path(staging, task_id, side)
                matches = by_key.get((os.path.normcase(task_id), side), [])
                side_info: dict[str, Any] = {
                    "declared_sha256": declared_hash,
                    "expected_path": str(expected),
                    "status": "",
                }
                sides_report[side] = side_info

                if len(matches) > 1:
                    task_discrepancies.append(
                        _discrepancy(
                            task_id, side, REASON_EVIDENCE_DUPLICATE,
                            "multiple evidence files resolve to this task/side: "
                            + ", ".join(sorted(str(m.path) for m in matches)),
                        )
                    )
                    side_info["status"] = "duplicate"
                    continue

                present = matches[0].path if matches else None
                if present is not None:
                    referenced.add(str(present.resolve()))

                if declared_hash:
                    if not _is_sha256(declared_hash):
                        task_discrepancies.append(
                            _discrepancy(task_id, side,
                                         REASON_DECLARED_HASH_MALFORMED,
                                         f"declared_sha256={declared_hash!r}")
                        )
                        side_info["status"] = "declared_hash_malformed"
                        continue
                    if present is None or not present.is_file():
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_JUNIT_MISSING,
                                "a JUnit hash is declared but the file is absent: "
                                f"{expected}",
                            )
                        )
                        side_info["status"] = "missing"
                        continue
                    actual_hash = _sha256_file(present)
                    side_info["actual_sha256"] = actual_hash
                    side_info["path"] = str(present)
                    if actual_hash != declared_hash:
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_JUNIT_HASH_MISMATCH,
                                f"declared={declared_hash} actual={actual_hash}",
                            )
                        )
                        side_info["status"] = "hash_mismatch"
                        continue
                    # Hash matches; now re-derive from the bytes.
                    try:
                        junit_bytes = present.read_bytes()
                    except OSError as exc:
                        task_discrepancies.append(
                            _discrepancy(task_id, side, REASON_JUNIT_MALFORMED,
                                         f"unreadable: {exc}")
                        )
                        side_info["status"] = "unreadable"
                        continue
                    contract = contracts.get(task_id)
                    if contract is None:
                        if require_contracts:
                            task_discrepancies.append(
                                _discrepancy(
                                    task_id, side, REASON_CONTRACT_MISSING,
                                    "no FAIL_TO_PASS/PASS_TO_PASS contract was "
                                    "supplied for this task, so the verdict cannot "
                                    "be re-derived",
                                )
                            )
                        side_info["status"] = "no_contract"
                        continue
                    try:
                        # Same identity bindings the pre-check used for this task:
                        # the dataset root the source declares, this side's actual
                        # execution root, and the project's own enum table. A
                        # binding applied in one place only would make the two
                        # disagree about the same retained evidence.
                        execution_root = (
                            str(Path(contract.workspace) /
                                ("repo" if side == "base" else "repo_gold"))
                            if contract.workspace else ""
                        )
                        outcome_obj = evaluate_execution(
                            junit_bytes,
                            fail_to_pass=list(contract.fail_to_pass),
                            pass_to_pass=list(contract.pass_to_pass),
                            instance_id=task_id,
                            execution_state_identity=side,
                            changed_modules=list(contract.changed_modules),
                            dataset_root=contract.dataset_root,
                            execution_root=execution_root,
                            enum_values=contract.enum_values,
                        )
                    except Exception as exc:  # noqa: BLE001 - any evaluator failure is malformed evidence
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_JUNIT_MALFORMED,
                                f"the retained JUnit could not be evaluated: {exc}",
                            )
                        )
                        side_info["status"] = "malformed"
                        continue

                    recounted_errors = int(outcome_obj.collection_errors)
                    recounted_cause = str(outcome_obj.collection_error_cause)
                    recounted_verdict = str(outcome_obj.declared_result)
                    side_info.update({
                        "collection_errors": recounted_errors,
                        "collection_error_cause": recounted_cause,
                        "verdict": recounted_verdict,
                        "status": "valid",
                    })

                    declared_errors = row.get(f"{side}_collection_errors", _MISSING)
                    if declared_errors is _MISSING or declared_errors is None:
                        if outcome_class != "infrastructure":
                            task_discrepancies.append(
                                _discrepancy(
                                    task_id, side,
                                    REASON_DECLARED_COLLECTION_ERRORS_MISSING,
                                    "the report declares no collection-error count",
                                )
                            )
                    elif (
                        isinstance(declared_errors, bool)
                        or not isinstance(declared_errors, int)
                        or declared_errors < 0
                    ):
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side,
                                REASON_DECLARED_COLLECTION_ERRORS_MALFORMED,
                                f"declared={declared_errors!r}",
                            )
                        )
                    elif int(declared_errors) != recounted_errors:
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_COLLECTION_ERRORS_MISMATCH,
                                f"declared={declared_errors} "
                                f"recounted={recounted_errors}",
                            )
                        )

                    declared_cause = row.get(f"{side}_collection_error_cause", _MISSING)
                    if declared_cause is _MISSING or declared_cause is None or str(
                        declared_cause
                    ) == "":
                        if outcome_class != "infrastructure":
                            task_discrepancies.append(
                                _discrepancy(
                                    task_id, side, REASON_DECLARED_CAUSE_MISSING,
                                    "the report declares no collection-error cause",
                                )
                            )
                    elif str(declared_cause) not in VALID_CAUSES:
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_DECLARED_CAUSE_MALFORMED,
                                f"declared={declared_cause!r}",
                            )
                        )
                    elif str(declared_cause) != recounted_cause:
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_CAUSE_MISMATCH,
                                f"declared={declared_cause} "
                                f"recounted={recounted_cause}",
                            )
                        )

                    declared_verdict = row.get(f"{side}_verdict", _MISSING)
                    if declared_verdict is _MISSING or declared_verdict is None or str(
                        declared_verdict
                    ) == "":
                        if outcome_class != "infrastructure":
                            task_discrepancies.append(
                                _discrepancy(
                                    task_id, side, REASON_DECLARED_VERDICT_MISSING,
                                    "the report declares no verdict",
                                )
                            )
                    elif str(declared_verdict) not in VALID_VERDICTS:
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_DECLARED_VERDICT_MALFORMED,
                                f"declared={declared_verdict!r} is not one of "
                                f"{sorted(VALID_VERDICTS)}",
                            )
                        )
                    elif str(declared_verdict) != recounted_verdict:
                        task_discrepancies.append(
                            _discrepancy(
                                task_id, side, REASON_VERDICT_MISMATCH,
                                f"declared={declared_verdict} "
                                f"recounted={recounted_verdict}",
                            )
                        )
                    continue

                # No declared hash.
                if present is not None:
                    task_discrepancies.append(
                        _discrepancy(
                            task_id, side, REASON_JUNIT_UNDECLARED,
                            f"an evidence file exists but no hash is declared: {present}",
                        )
                    )
                    side_info["status"] = "undeclared"
                    continue
                # No hash and no file: legitimate ONLY for an infrastructure row
                # with a registered F0 §12 reason.  A scientific claim without
                # evidence is a failure, never a pass.
                if (
                    outcome_class == "infrastructure"
                    and reason_code in (infra_reasons | PRE_EXECUTION_REASONS)
                ):
                    side_info["status"] = "no_evidence_expected"
                elif outcome_class == "infrastructure":
                    task_discrepancies.append(
                        _discrepancy(
                            task_id, side, REASON_INFRA_UNCLASSIFIED,
                            f"infrastructure row has reason_code={reason_code!r}, "
                            "which is not a registered F0 §12 reason",
                        )
                    )
                    side_info["status"] = "infra_unclassified"
                else:
                    task_discrepancies.append(
                        _discrepancy(
                            task_id, side, REASON_EVIDENCE_MISSING,
                            "no JUnit hash is declared and no evidence file exists "
                            f"for a non-infrastructure outcome ({outcome!r})",
                        )
                    )
                    side_info["status"] = "evidence_missing"

            # Admission must agree with the independently re-derived sides.
            if admitted:
                base_v = sides_report.get("base", {}).get("verdict")
                gold_v = sides_report.get("gold", {}).get("verdict")
                if base_v != "fail" or gold_v != "pass":
                    task_discrepancies.append(
                        _discrepancy(
                            task_id, "", REASON_ADMISSION_CONTRADICTED,
                            "the row is admitted but the retained evidence "
                            f"re-derives base={base_v!r}, gold={gold_v!r}",
                        )
                    )

        task_verdict = "PASS" if not task_discrepancies else "FAIL"
        if task_discrepancies:
            failed += 1
        else:
            passed += 1
        if attempted is not False:
            audited += 1
        task_reports.append({
            "task_id": task_id,
            "attempted": attempted,
            "admitted": admitted,
            "outcome": outcome,
            "outcome_class": outcome_class,
            "reason_code": reason_code,
            "verdict": task_verdict,
            "sides": sides_report,
            "discrepancies": sorted(
                task_discrepancies,
                key=lambda d: (d["side"], d["reason_code"], d["detail"]),
            ),
        })
        discrepancies.extend(task_discrepancies)

    # ---- unexplained evidence --------------------------------------------
    for path in unexplained:
        if str(path.resolve()) in referenced:
            continue
        discrepancies.append(
            _discrepancy(
                "", "", REASON_UNEXPECTED_EVIDENCE_FILE,
                f"evidence file does not belong to any report task/side: {path}",
            )
        )
    # A file can fit the NAMING contract yet belong to a task/side this report
    # never claims (a stale batch, a different task, an unattempted row).  It is
    # not silently ignored just because it parsed as evidence.
    for item in recognised:
        if str(item.path.resolve()) in referenced:
            continue
        already_duplicate = any(
            d.get("reason_code") == REASON_EVIDENCE_DUPLICATE
            and d.get("task_id") == item.task_id
            and d.get("side") == item.side
            for d in discrepancies
        )
        if already_duplicate:
            continue
        discrepancies.append(
            _discrepancy(
                item.task_id, item.side, REASON_UNEXPECTED_EVIDENCE_FILE,
                "evidence file belongs to no report row/side: " + str(item.path),
            )
        )

    # ---- totals consistency (only when the report declares totals) --------
    totals = report.get("totals")
    if isinstance(totals, Mapping):
        admissed_rows = sum(1 for r in rows if bool(r.get("admitted")))
        attempted_rows = sum(1 for r in rows if bool(r.get("attempted", True)))
        for field, actual in (("admitted", admissed_rows), ("attempted", attempted_rows)):
            declared = totals.get(field, _MISSING)
            if declared is _MISSING:
                continue
            parsed = (
                declared
                if isinstance(declared, int) and not isinstance(declared, bool)
                else None
            )
            if parsed is None or parsed < 0:
                discrepancies.append(
                    _discrepancy(
                        "", "", REASON_TOTALS_MISMATCH,
                        f"totals.{field}={declared!r} is not a non-negative integer",
                    )
                )
            elif parsed != actual:
                discrepancies.append(
                    _discrepancy(
                        "", "", REASON_TOTALS_MISMATCH,
                        f"totals.{field}={parsed} rows={actual}",
                    )
                )

    result["tasks"] = sorted(task_reports, key=lambda t: t["task_id"])
    result["counts"] = {
        "tasks_total": len(rows),
        "tasks_audited": audited,
        "tasks_passed": passed,
        "tasks_failed": failed,
        "evidence_files_inspected": sum(
            1
            for t in task_reports
            for s in SIDES
            if t["sides"].get(s, {}).get("status") == "valid"
        ),
    }
    discrepancies.sort(
        key=lambda d: (d["task_id"], d["side"], d["reason_code"], d["detail"])
    )
    return _seal(result)


def _seal(result: dict[str, Any]) -> dict[str, Any]:
    """Finalise the verdict, reason codes and deterministic digest."""
    discrepancies = result.get("discrepancies") or []
    result["verdict"] = "PASS" if not discrepancies else "FAIL"
    result["reason_codes"] = sorted(
        {str(d.get("reason_code") or "") for d in discrepancies if d.get("reason_code")}
    )
    result["audit_digest"] = _audit_digest(result)
    return result


# ---------------------------------------------------------------------------
# artifact persistence + progression gate
# ---------------------------------------------------------------------------
def write_audit(path: Path | str, result: Mapping[str, Any]) -> Path:
    """Write the audit artifact atomically and durably.  Never touches evidence."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def load_audit(path: Path | str) -> dict[str, Any]:
    """Load and integrity-check a written audit artifact, fail closed."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise BatchAuditError(f"audit artifact {source} is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise BatchAuditError(f"audit artifact {source} has an unknown schema")
    declared = str(payload.get("audit_digest") or "")
    if not _is_sha256(declared):
        raise BatchAuditError(f"audit artifact {source} has no valid audit_digest")
    if declared != _audit_digest(payload):
        raise BatchAuditError(
            f"audit artifact {source} does not match its audit_digest (tampered)"
        )
    return payload


def require_passing(
    audit: Mapping[str, Any] | Path | str, *, purpose: str = "batch progression"
) -> dict[str, Any]:
    """Refuse progression/credit unless the audit is a verified PASS.

    Accepts either a result mapping (from :func:`audit_batch`) or the path to a
    written artifact (which is integrity-checked by :func:`load_audit`).
    """
    payload = load_audit(audit) if isinstance(audit, (str, Path)) else dict(audit)
    if payload.get("schema") != SCHEMA:
        raise BatchAuditBlocked(f"{purpose}: unknown audit schema")
    if not audit_payload_is_intact(payload):
        raise BatchAuditBlocked(
            f"{purpose}: the supplied audit has no valid audit_digest or does not "
            "match its own content"
        )
    verdict = str(payload.get("verdict") or "")
    if verdict != "PASS":
        codes = ", ".join(payload.get("reason_codes") or []) or "<none>"
        raise BatchAuditBlocked(
            f"{purpose}: post-batch audit verdict is {verdict or '<absent>'} "
            f"(reason codes: {codes})"
        )
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m qwen_train.f2_s8_post_batch_audit",
        description="Read-only post-batch audit of retained S8 JUnit evidence.",
    )
    parser.add_argument("--batch-results", required=True)
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--inputs", default=None,
                        help="pilot inputs directory holding FAIL_TO_PASS/PASS_TO_PASS/patch")
    parser.add_argument("--expected-manifest-digest", default=None)
    parser.add_argument("--expected-revision", default=None)
    parser.add_argument("--expected-run-id", default=None)
    parser.add_argument("--out", default="post_batch_audit.json")
    # ``audit_batch`` requires a sidecar evidence index by default, but the CLI
    # never supplied one, so this entry point could NEVER return PASS -- it
    # always reported EVIDENCE_INDEX_MISSING regardless of the batch. That made
    # the CLI useless for the historical re-derivation it exists for, and it
    # masked the real discrepancy underneath.
    parser.add_argument("--evidence-index", default=None,
                        help="sidecar evidence index produced for this batch")
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = audit_batch(
        batch_results_path=args.batch_results,
        staging_root=args.staging_root,
        manifest_path=args.manifest,
        inputs_dir=args.inputs,
        evidence_index_path=args.evidence_index,
        expected_manifest_digest=args.expected_manifest_digest,
        expected_repository_revision=args.expected_revision,
        expected_run_id=args.expected_run_id,
    )
    write_audit(args.out, result)
    print(json.dumps({
        "verdict": result["verdict"],
        "counts": result["counts"],
        "reason_codes": result["reason_codes"],
        "discrepancies": len(result["discrepancies"]),
        "out": str(args.out),
    }, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
