"""S8 production batch driver: resumable, idempotent, observable.

This is the production counterpart to the one-shot composition in
:mod:`qwen_train.f2_s8_compose`.  It drives many tasks through an injected,
fully-authorized per-task callable, using the append-only ledger and the
deterministic :func:`qwen_train.f2_s8_factory.plan_run` selector.

Guarantees
----------
* **Resumable.** The ledger is the durable state.  Re-invoking the driver selects
  only tasks that hold no terminal scientific record and whose retry cap is not
  exhausted, so an interrupted batch continues where it stopped.
* **No duplicate admissions.** ``plan_run`` skips every task that already holds
  a terminal scientific record (admitted or not) and
  ``AppendOnlyLedger`` refuses a second terminal scientific record for a task.
* **Bounded retry.** A task is never selected once ``attempts >= max_attempts``.
* **Infrastructure is not a task verdict.** A task whose callable raises is
  recorded as ``infrastructure_failure`` and retried under the cap; it is never
  recorded as a scientific negative.
* **Observable progress.** Each task appends one progress line (JSONL) naming the
  task, attempt, outcome and the running admission count derived from the ledger.
* **Fail-closed population binding.** A registration digest mismatch raises
  before any task is selected.

The driver performs no execution itself: ``process_task`` is the injected,
already-governed per-task entry point (e.g. a closure over
:func:`qwen_train.f2_s8_compose.produce_governed_task`).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train.f2_s8_factory import (
    ADMITTED_OUTCOME,
    AppendOnlyLedger,
    INFRASTRUCTURE_OUTCOMES,
    RetryPolicy,
    plan_run,
)

__all__ = [
    "BATCH_SCHEMA",
    "BatchError",
    "BatchReport",
    "drive_batch",
    "reconcile_admissions_across_chains",
    "reconcile_counts",
]

BATCH_SCHEMA = "f2_s8_batch_v1"


class BatchError(Exception):
    """The batch contract was violated."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class BatchReport:
    registration_id: str
    registration_sha256: str
    policy: Mapping[str, int]
    selected: tuple[str, ...]
    results: tuple[Mapping[str, Any], ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    progress_path: str = ""
    skipped_admitted: tuple[str, ...] = ()
    skipped_terminal: tuple[str, ...] = ()
    skipped_exhausted: tuple[str, ...] = ()
    stopped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BATCH_SCHEMA,
            "registration_id": self.registration_id,
            "registration_sha256": self.registration_sha256,
            "policy": dict(self.policy),
            "selected": list(self.selected),
            "results": [dict(r) for r in self.results],
            "counts": dict(self.counts),
            "progress_path": self.progress_path,
            "skipped_admitted": list(self.skipped_admitted),
            "skipped_terminal": list(self.skipped_terminal),
            "skipped_exhausted": list(self.skipped_exhausted),
            "stopped_reason": self.stopped_reason,
        }


def reconcile_counts(
    ledger: AppendOnlyLedger, ordered_task_ids: Sequence[str]
) -> dict[str, int]:
    """Ledger-derived counts.  Every value is computed from authoritative records.

    ``planned`` / ``evidence_valid`` / ``analyzable`` are deliberately **not**
    invented here: planning is a scheduler output, evidence validity requires
    re-verifying retained artifacts, and ``analyzable`` belongs to the later T/X
    phase.  Those are reported by their owning layers, never inferred from a
    ledger row.
    """
    universe = set(ordered_task_ids)
    records = [r for r in ledger.records() if str(r.get("task_id")) in universe]
    attempted = {str(r["task_id"]) for r in records}
    scientific = {str(r["task_id"]) for r in records if r.get("outcome_class") == "scientific"}
    admitted = {str(r["task_id"]) for r in records if r.get("admitted") is True}
    infra = {
        str(r["task_id"])
        for r in records
        if r.get("outcome") in INFRASTRUCTURE_OUTCOMES
    }
    return {
        "registered": len(ordered_task_ids),
        "attempted": len(attempted),
        "completed": len(scientific),
        "admitted": len(admitted),
        "infrastructure_only": len(infra - scientific),
    }


#: REGISTERED chains, code-pinned (owner-authorized OD-2). The key is the exact
#: ledger FILENAME; the value is the SHA-256 of that chain's registered historical
#: LINE PREFIX - the cumulative digest over the file's leading non-empty lines,
#: line endings included (see :func:`_chain_prefix_digests`). The operative chain
#: is index 0. A future append grows the operative chain while this historical
#: prefix digest stays valid; a reordered, swapped or altered chain does not.
REGISTERED_CHAINS: dict[str, str] = {
    "s8_pilot_ledger.jsonl":
        "5fb0aa8eda37f220ea2fce5ca96688bb4a7edf94576b73b32b1678c1a1971430",
    "s8_pilot_ledger_corrected.jsonl":
        "e030c05a21fd22baf840f80a9cbd4645dd6c1aa8492350281f563a835f96d5ae",
}

#: The ONE grandfathered historical correction (owner-authorized OD-2, option
#: b-2). Exactly this task may override a prior TERMINAL SCIENTIFIC REJECTION
#: through its recorded correction; every other such attempt is refused. The set
#: is code-pinned - the runtime policy is never read from a Markdown document -
#: and a test proves it agrees with the counting rule's grandfathering table.
GRANDFATHERED_CORRECTION_TASK_IDS: frozenset[str] = frozenset({
    "HypothesisWorks__hypothesis-4532",
})

#: Test-only sentinel (see ``reconcile_admissions_across_chains``).  Private by
#: design: production code must never hold a reference to it, and passing
#: anything else -- including ``False`` -- leaves registered-chain enforcement
#: ON.  There is deliberately no boolean switch to disable the policy.
_ALLOW_UNREGISTERED_CHAINS = object()


def reconcile_admissions_across_chains(
    chains: Sequence[AppendOnlyLedger | Path | str],
    ordered_task_ids: Sequence[str],
    *,
    allow_unregistered_chains: object | None = None,
) -> dict[str, Any]:
    """Credit admissions across the registered chains (owner rule, option B).

    The operative chain is credited by :func:`reconcile_counts`; a separately
    owner-sanctioned corrective chain lives outside it.  The owner chose
    (2026-10-15) that a task counts toward the target iff it holds at least one
    admitted scientific record in ANY registered chain, **each task exactly
    once**.  This function implements that rule read-only.

    Fail-closed: every chain is verified first, and a chain that fails
    verification aborts the whole reconciliation -- a broken chain must never
    silently reduce or inflate the credited total.  No ledger is mutated and no
    admission predicate changes; a task admitted in more than one chain is
    reported in ``credited_in_multiple_chains`` and still counted once.

    A corrective record must also BIND to the chain it corrects
    (:func:`_require_correction_binding`): the named chain must be part of this
    reconciliation, the corrected record digest must exist in it, and
    ``correction_of_chain_sha256`` must be a historical LINE PREFIX of that
    chain's bytes.  A correction whose binding is absent, malformed or unproven
    aborts the reconciliation rather than being credited.

    ``chains`` is ordered: the first entry is the **operative** chain (the one
    the runbook pins with ``--ledger``), whose admitted set is returned
    separately as ``operative_admitted`` -- the code-bound canonical figure.
    """
    if not chains:
        raise BatchError("cross-chain reconciliation needs at least one chain")
    universe = set(ordered_task_ids)

    # Registered-chain enforcement is ON unless the caller passes the module
    # private test-only sentinel.  A caller cannot relax the policy by passing a
    # boolean: ``enforce_registered_chains=False`` is now a TypeError (no such
    # parameter), and any other value -- including ``False`` -- leaves the
    # enforcement enabled.  Only in-tree test modules hold the sentinel.
    enforce_registered_chains = (
        allow_unregistered_chains is not _ALLOW_UNREGISTERED_CHAINS
    )

    # REGISTERED-CHAIN ENFORCEMENT (owner-authorized OD-2). The supplied chains
    # must be exactly the code-pinned registered set, in registered order, and
    # each must actually CONTAIN its registered historical prefix digest. A
    # caller-supplied name or digest that merely "looks valid" is never accepted.
    if enforce_registered_chains:
        supplied = [
            (c.path if isinstance(c, AppendOnlyLedger) else Path(c)).name
            for c in chains
        ]
        unknown = [n for n in supplied if n not in REGISTERED_CHAINS]
        if unknown:
            raise BatchError(
                f"unregistered chain(s) {unknown}; registered chains are "
                f"{list(REGISTERED_CHAINS)}")
        if supplied != list(REGISTERED_CHAINS):
            raise BatchError(
                "chains must be supplied in registered order "
                f"{list(REGISTERED_CHAINS)}; got {supplied}")

    # Pass 1: verify and index EVERY chain first, so a correction row can be
    # resolved against the chain it names even when that chain appears later.
    indexes: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for chain in chains:
        ledger = chain if isinstance(chain, AppendOnlyLedger) else AppendOnlyLedger(chain)
        ok, reason = ledger.verify()
        if not ok:
            raise BatchError(f"chain {ledger.path} failed verification: {reason}")
        records = ledger.records()
        raw = ledger.path.read_bytes() if ledger.path.is_file() else b""
        # Line numbers are 1-based over the file's NON-EMPTY lines (the rows the
        # ledger's own reader enumerates), so a record's position is comparable to
        # the verified prefix boundary.
        line_numbers: dict[str, int] = {}
        digests: dict[str, tuple[int, str]] = {}
        number = 0
        for row in raw.splitlines():
            if not row.strip():
                continue
            number += 1
            try:
                parsed = json.loads(row)
            except Exception:  # noqa: BLE001 - verify() already vouched for the chain
                continue
            digest = str(parsed.get("record_digest") or "")
            if digest and digest not in digests:
                digests[digest] = (number, str(parsed.get("task_id") or ""))
                line_numbers[digest] = number
        index = {
            "ledger": ledger,
            "records": records,
            "prefix_digests": _chain_prefix_digests(raw),
            "record_digests": set(digests),
            "record_task_and_line": digests,
        }
        indexes.append(index)
        by_name[ledger.path.name] = index
        by_name[str(ledger.path)] = index

    # Pass 2: validate registrations, bindings and the grandfather exception
    # (all fail closed), then credit.
    registered_names = [idx["ledger"].path.name for idx in indexes]
    if enforce_registered_chains:
        for index in indexes:
            name = index["ledger"].path.name
            pinned = REGISTERED_CHAINS[name]
            if pinned not in index["prefix_digests"]:
                raise BatchError(
                    f"chain {name!r} does not contain its registered historical "
                    f"prefix digest {pinned}; the chain content is not the "
                    "registered history")

    per_chain: list[dict[str, Any]] = []
    credited: set[str] = set()
    in_multiple: set[str] = set()
    unregistered: set[str] = set()
    corrections_verified = 0
    refused_override: list[dict[str, str]] = []
    prior_first: dict[str, tuple[str, Mapping[str, Any]]] = {}

    for index in indexes:
        ledger = index["ledger"]
        name = ledger.path.name
        records = index["records"]

        # Validated corrections in THIS chain (verified binding), by task.
        corrected: dict[str, Mapping[str, Any]] = {}
        for record in records:
            if _require_correction_binding(record, by_name=by_name):
                corrections_verified += 1
                corrected[str(record.get("task_id"))] = record

        # The canonical admission rule (AppendOnlyLedger.admitted): the FIRST
        # scientific record for a task decides, and only ADMITTED_OUTCOME
        # credits.  Computed in one pass; a test pins it to the per-task method.
        first_scientific: dict[str, dict[str, Any]] = {}
        for record in records:
            task_id = str(record.get("task_id"))
            if record.get("outcome_class") == "scientific" and task_id not in first_scientific:
                first_scientific[task_id] = record
        admitted_all = {
            task_id
            for task_id, record in first_scientific.items()
            if record.get("outcome") == ADMITTED_OUTCOME
        }

        # --- CROSS-CHAIN CREDIT RULES (owner-authorized OD-2) ----------------
        for task_id in sorted(admitted_all):
            prior = prior_first.get(task_id)
            if prior is None:
                continue
            prior_chain, prior_rec = prior
            correction = corrected.get(task_id)
            if correction is None:
                raise BatchError(
                    f"task {task_id!r} is admitted in chain {name!r} but already "
                    f"appears in the earlier registered chain {prior_chain!r} "
                    "without a verified correction binding; a cross-chain credit "
                    "requires one")
            bound = str(correction.get("correction_of_chain") or "").strip()
            if bound != prior_chain:
                raise BatchError(
                    f"task {task_id!r} correction in chain {name!r} binds "
                    f"{bound!r}, not the earlier registered chain {prior_chain!r}")
            claimed_prior = str(correction.get("prior_outcome") or "")
            actual_prior = str(prior_rec.get("outcome") or "")
            if claimed_prior != actual_prior:
                raise BatchError(
                    f"task {task_id!r} correction claims prior_outcome "
                    f"{claimed_prior!r} but the earlier record is {actual_prior!r}")
            claimed_class = str(correction.get("prior_outcome_class") or "")
            actual_class = str(prior_rec.get("outcome_class") or "")
            if claimed_class != actual_class:
                raise BatchError(
                    f"task {task_id!r} correction claims prior_outcome_class "
                    f"{claimed_class!r} but the earlier record is {actual_class!r}")
            # A correction may override a TERMINAL SCIENTIFIC REJECTION only
            # through the one code-pinned historical exception; every other such
            # attempt is REFUSED (never credited).
            if (prior_rec.get("outcome_class") == "scientific"
                    and actual_prior != ADMITTED_OUTCOME):
                if not enforce_registered_chains or task_id not in GRANDFATHERED_CORRECTION_TASK_IDS:
                    refused_override.append({
                        "task_id": task_id,
                        "chain": name,
                        "prior_chain": prior_chain,
                        "prior_outcome": actual_prior,
                        "reason": "terminal scientific rejection may only be "
                                  "overridden by the code-pinned grandfathered "
                                  "historical correction",
                    })
                    admitted_all = admitted_all - {task_id}

        unregistered |= {t for t in admitted_all if t not in universe}
        admitted = admitted_all & universe
        in_multiple |= credited & admitted
        credited |= admitted
        per_chain.append({
            "path": str(ledger.path),
            "rows": len(records),
            "admitted": sorted(admitted),
        })
        # Record the FIRST record for each task in THIS chain for later chains.
        for record in records:
            prior_first.setdefault(str(record.get("task_id")), (name, record))

    return {
        "chains": per_chain,
        "registered_chains": registered_names,
        "operative_admitted": per_chain[0]["admitted"],
        "credited_admitted": sorted(credited),
        "credited_count": len(credited),
        "credited_in_multiple_chains": sorted(in_multiple),
        "unregistered_admitted": sorted(unregistered),
        "refused_terminal_override": sorted(refused_override, key=lambda r: r["task_id"]),
        "corrections_verified": corrections_verified,
        "registered": len(ordered_task_ids),
    }


#: A correction record binds to the chain it corrects through these three fields;
#: they are required together or not at all.
_CORRECTION_KEYS = (
    "correction_of_chain",
    "correction_of_chain_sha256",
    "correction_of_record_digest",
)


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _chain_prefix_digests(raw: bytes) -> dict[str, int]:
    """Every SHA-256 of a LINE PREFIX of a ledger file's bytes -> rows included.

    A correction records ``correction_of_chain_sha256`` = the SHA-256 of the
    corrected chain's bytes *as they stood when the correction was written*, i.e.
    the first N non-empty rows with their line endings.  Later appends grow the
    chain, so the recorded value is a prefix hash of the CURRENT file -- verified
    on the real ledgers (2026-10-16): the corrected chain's recorded digest is
    exactly the first-7-rows digest of the 9-row operative chain
    (``07cb4545...``).

    Returning the ROW COUNT for each prefix digest (not a bare set) is what lets
    the binding check place the referenced record relative to the verified
    boundary: the corrected record must occur at or before it.

    Both the literal byte prefix and its LF-normalised equivalent are accepted:
    they agree for the current (LF) ledgers, and newline normalisation is the only
    representation difference that does not change the chain's content.  A
    boundary observed in either representation records the same row count.
    """
    boundaries: dict[str, int] = {}
    for variant in (raw, raw.replace(b"\r\n", b"\n")):
        running = hashlib.sha256()
        rows = 0
        for line in variant.splitlines(keepends=True):
            running.update(line)
            if line.strip():
                rows += 1
                boundaries.setdefault(running.hexdigest(), rows)
    return boundaries


def _require_correction_binding(
    record: Mapping[str, Any],
    *,
    by_name: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Fail closed unless a correction record binds to a historical prefix.

    Four conditions must ALL hold; any failure aborts the reconciliation:

    1. the named chain is part of this reconciliation;
    2. ``correction_of_record_digest`` names a record that EXISTS in that chain;
    3. that record is for the SAME ``task_id`` as the corrective record (a
       correction may only correct the same task, never a different one);
    4. the corrected record occurs AT OR BEFORE the verified prefix boundary
       identified by ``correction_of_chain_sha256`` -- i.e. it was already in the
       chain when the correction was written.

    Returns ``True`` for a validated correction and ``False`` for an ordinary
    record.  This makes no credit decision: it only refuses to credit a correction
    whose binding is unproven.
    """
    present = [k for k in _CORRECTION_KEYS if k in record]
    if not present:
        return False
    missing = [k for k in _CORRECTION_KEYS if not str(record.get(k) or "").strip()]
    if missing:
        raise BatchError(
            f"malformed correction record: it carries {sorted(present)} but omits "
            f"{missing}; every correction field is required together "
            f"({list(_CORRECTION_KEYS)})")
    name = str(record["correction_of_chain"]).strip()
    target = by_name.get(name) or by_name.get(Path(name).name)
    if target is None:
        raise BatchError(
            f"correction references chain {name!r}, which is not part of this "
            "reconciliation; a correction cannot be validated without the chain "
            "it corrects")
    ref = str(record["correction_of_record_digest"]).strip()
    if not _is_sha256_hex(ref):
        raise BatchError(
            f"correction_of_record_digest {ref!r} is not a 64-character lowercase "
            "SHA-256 hex digest")
    located = target["record_task_and_line"].get(ref)
    if located is None:
        raise BatchError(
            f"correction references record {ref!r}, which does not exist in "
            f"{Path(target['ledger'].path).name}")
    ref_line, ref_task = located

    corrective_task = str(record.get("task_id") or "")
    if not corrective_task or ref_task != corrective_task:
        raise BatchError(
            f"correction for task {corrective_task!r} references a record for task "
            f"{ref_task!r}; a correction may only correct the SAME task")

    claimed = str(record["correction_of_chain_sha256"]).strip()
    if not _is_sha256_hex(claimed):
        raise BatchError(
            f"correction_of_chain_sha256 {claimed!r} is not a 64-character "
            "lowercase SHA-256 hex digest")
    boundary = target["prefix_digests"].get(claimed)
    if boundary is None:
        raise BatchError(
            f"correction_of_chain_sha256 {claimed!r} is not a historical prefix of "
            f"{Path(target['ledger'].path).name}; the correction binding is not "
            "established")
    if ref_line > boundary:
        raise BatchError(
            f"correction references a record at row {ref_line} of "
            f"{Path(target['ledger'].path).name}, outside the verified "
            f"{boundary}-row prefix recorded by correction_of_chain_sha256; the "
            "corrected record must already exist within that prefix")
    return True


def _append_progress(path: Path | None, entry: Mapping[str, Any]) -> None:
    if path is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(entry), sort_keys=True) + "\n")


def _infra_record(
    *,
    ledger: AppendOnlyLedger,
    task_id: str,
    registration_id: str,
    registration_sha256: str,
    attempt: int,
    exc: BaseException,
) -> dict[str, Any]:
    return ledger.append({
        "task_id": task_id,
        "registration_id": registration_id,
        "registration_sha256": registration_sha256,
        "outcome": "infrastructure_failure",
        "outcome_class": "infrastructure",
        "attempt": attempt,
        "reason": f"driver caught {type(exc).__name__}",
        "admitted": False,
    })


def drive_batch(
    *,
    ordered_task_ids: Sequence[str],
    ledger: AppendOnlyLedger,
    registration_id: str,
    registration_sha256: str,
    expected_registration_sha256: str,
    process_task: Callable[[str, int], Mapping[str, Any]],
    policy: RetryPolicy | None = None,
    progress_path: Path | str | None = None,
    in_flight: Sequence[str] = (),
    stop_after: int | None = None,
    clock: Callable[[], str] | None = None,
) -> BatchReport:
    """Drive the next batch of tasks, resumably and idempotently.

    ``process_task(task_id, attempt)`` performs one fully-governed task and
    returns the ledger record it appended (or any mapping summarising it).  The
    driver never weakens or re-interprets that record.
    """
    policy = policy or RetryPolicy()
    now = clock or _utc_now
    plan = plan_run(
        ordered_task_ids=ordered_task_ids,
        ledger=ledger,
        registration_id=registration_id,
        registration_sha256=registration_sha256,
        expected_registration_sha256=expected_registration_sha256,
        policy=policy,
        in_flight=in_flight,
    )

    results: list[dict[str, Any]] = []
    stopped_reason = ""
    for index, task_id in enumerate(plan.runnable):
        if stop_after is not None and index >= stop_after:
            stopped_reason = f"stop_after={stop_after} reached"
            break
        attempt = len(ledger.attempts(task_id)) + 1
        error = ""
        try:
            record = dict(process_task(task_id, attempt))
        except Exception as exc:  # noqa: BLE001 - infrastructure boundary
            record = _infra_record(
                ledger=ledger, task_id=task_id, registration_id=registration_id,
                registration_sha256=registration_sha256, attempt=attempt, exc=exc,
            )
            error = f"{type(exc).__name__}: {exc}"
        admitted_so_far = len(
            {r["task_id"] for r in ledger.records() if r.get("admitted") is True}
        )
        summary = {
            "task_id": task_id,
            "attempt": attempt,
            "outcome": record.get("outcome", ""),
            "outcome_class": record.get("outcome_class", ""),
            "admitted": bool(record.get("admitted", False)),
            "error": error,
            "admitted_total": admitted_so_far,
            "ts": now(),
        }
        results.append(summary)
        _append_progress(Path(progress_path) if progress_path else None, summary)

    return BatchReport(
        registration_id=registration_id,
        registration_sha256=registration_sha256,
        policy=policy.as_dict(),
        selected=tuple(plan.runnable),
        results=tuple(results),
        counts=reconcile_counts(ledger, ordered_task_ids),
        progress_path=str(progress_path or ""),
        skipped_admitted=plan.skipped_admitted,
        skipped_terminal=plan.skipped_terminal,
        skipped_exhausted=plan.skipped_exhausted,
        stopped_reason=stopped_reason,
    )
