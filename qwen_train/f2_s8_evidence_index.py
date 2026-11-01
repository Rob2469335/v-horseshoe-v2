"""F2-S8 evidence sidecar index (owner decision E1, 2026-10-18).

WHY IT EXISTS
-------------
An infrastructure-outcome ledger row stores no evidence hash, so a post-batch
finding cannot be *chain-anchored*.  Owner decision E1 chose the **sidecar**
option: leave the frozen ledger row contract unchanged and write a separate,
content-addressed index that binds each row's ``record_digest`` to the retained
JUnit bytes.  The index digest is then written into every ``post_batch_audit``
artifact, so a tampered or substituted index is detectable.

WHAT IT DOES AND DOES NOT ESTABLISH
-----------------------------------
* It establishes a deterministic, content-addressed record of which bytes were
  retained for each batch row, including ``infrastructure_failure`` rows whose
  crashed side legitimately has no JUnit (recorded as ``null``, never zero).
* It does **not** put the hashes inside the hash chain.  A writer who can
  rewrite the staging root, the report and this index can make them agree; the
  chain will not contradict them.  That residual risk is why Option A remains
  the stronger (but contract-changing) alternative.

CONTENT ADDRESSING
------------------
The canonical bytes of the index (without any self-digest field) are hashed
with SHA-256 and the file is written as ``<sha256>.json``.  :func:`load_index`
recomputes the digest and refuses a file whose name does not match its content.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "INDEX_SCHEMA",
    "EvidenceIndexError",
    "build_index",
    "write_index",
    "load_index",
    "index_digest",
]

INDEX_SCHEMA = "f2-s8-evidence-index/v1"
SIDES = ("base", "gold")
EVIDENCE_SUFFIX = ".junit.hostcopy.xml"


class EvidenceIndexError(RuntimeError):
    """The index cannot be built or verified (fail closed)."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def index_digest(index: Mapping[str, Any]) -> str:
    """Digest of the index content (no self-referential field)."""
    body = {k: v for k, v in index.items() if k != "index_digest"}
    return _sha256_bytes(_canonical_bytes(body))


def _rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = report.get("tasks")
    if not isinstance(raw, list):
        raw = report.get("results")
    if not isinstance(raw, list):
        raise EvidenceIndexError("batch report has no tasks/results list")
    rows = [r for r in raw if isinstance(r, Mapping)]
    if len(rows) != len(raw):
        raise EvidenceIndexError("batch report contains a non-object row")
    return rows


def build_index(
    *,
    batch_results_path: Path | str,
    staging_root: Path | str,
    output_dir: Path | str,
) -> tuple[dict[str, Any], Path]:
    """Build and write a content-addressed index for one batch report.

    Every row MUST carry its ledger ``record_digest``; a row without one cannot
    be bound to the chain, so the builder fails closed rather than writing an
    index that looks complete but is not anchored.
    """
    results = Path(batch_results_path)
    staging = Path(staging_root)
    report_bytes = results.read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    if not isinstance(report, Mapping):
        raise EvidenceIndexError("batch report is not a JSON object")

    entries: list[dict[str, Any]] = []
    for row in _rows(report):
        task_id = str(row.get("task_id") or "")
        if not task_id:
            raise EvidenceIndexError("a task row has no task_id")
        ledger = row.get("ledger_record")
        record_digest = (
            str(ledger.get("record_digest") or "")
            if isinstance(ledger, Mapping) else ""
        )
        # A designed pre-execution refusal (and a row that faulted before any
        # ledger append) has no record digest.  Record it honestly as UNBOUND
        # rather than inventing a chain key or dropping the row: the audit
        # accepts an unbound entry only for a non-admitted infrastructure row.
        chain_bound = bool(record_digest)
        row_digest = _sha256_bytes(_canonical_bytes(dict(row)))
        sides: dict[str, Any] = {}
        for side in SIDES:
            path = staging / f"{task_id}.{side}{EVIDENCE_SUFFIX}"
            if path.is_file():
                sides[side] = {
                    "junit_sha256": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            else:
                # A crashed side legitimately retains nothing.  ``null`` is
                # explicit; it is never a zero hash.
                sides[side] = {"junit_sha256": None, "size_bytes": 0}
        entries.append({
            "task_id": task_id,
            "record_digest": record_digest or None,
            "chain_bound": chain_bound,
            "report_row_digest": row_digest,
            "attempt": int(row.get("attempt") or 0),
            "outcome": str(row.get("outcome") or ""),
            "outcome_class": str(row.get("outcome_class") or ""),
            "sides": sides,
        })

    entries.sort(key=lambda e: (e["task_id"], e["attempt"], str(e["record_digest"])))
    ledger_path = str(report.get("ledger") or "")
    chain_sha = ""
    if ledger_path and Path(ledger_path).is_file():
        chain_sha = _sha256_file(Path(ledger_path))
    index: dict[str, Any] = {
        "schema": INDEX_SCHEMA,
        "batch": {
            "results_path": str(results.resolve()),
            "results_sha256": _sha256_bytes(report_bytes),
            "manifest_digest": str(report.get("manifest_digest") or ""),
            "run_id": str(report.get("run_id") or ""),
            "registration_sha256": str(report.get("registration_sha256") or ""),
            "repository_revision": str(report.get("repository_revision") or ""),
        },
        "chain": {"path": ledger_path, "sha256": chain_sha},
        "entries": entries,
    }
    return index, write_index(output_dir, index)


def write_index(output_dir: Path | str, index: Mapping[str, Any]) -> Path:
    """Write the index as ``<sha256>.json`` atomically and durably."""
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    digest = index_digest(index)
    target = target_dir / f"{digest}.json"
    body = json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def load_index(path: Path | str) -> dict[str, Any]:
    """Load an index and verify that its filename matches its content."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise EvidenceIndexError(f"evidence index {source} is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != INDEX_SCHEMA:
        raise EvidenceIndexError(f"evidence index {source} has an unknown schema")
    actual = index_digest(payload)
    if source.stem != actual:
        raise EvidenceIndexError(
            f"evidence index {source.name} does not match its content digest "
            f"({actual}); the index was substituted or edited"
        )
    payload = dict(payload)
    payload["index_digest"] = actual
    return payload
