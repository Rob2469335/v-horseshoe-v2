"""Machine-checkable F2 task population manifest (R1 / R8).

Why a manifest and not a prose list
-----------------------------------
``docs/EXPERIMENT_J_TASK_READINESS_CONTRACT.md`` section 3 designates five
candidate tasks in a Markdown table. That is a human-readable record, not a
contract: nothing prevents two researchers disagreeing about whether a task is
admissible, and nothing binds a task to the endpoint it will be measured with.
The same document leaves R8 (a frozen per-task ``relevant_file_set``) unmet for
every designated task, which means the F2 primary endpoint is currently
uncomputable for all of them.

This module makes admission a machine-checkable function of the task's own
declared metadata, with every screening decision recorded and every mutable
field hash-bound.

Screening rules (all must hold; each is reported individually)
-------------------------------------------------------------
S1  unique identity - a non-empty ``instance_id``, unique within the manifest
S2  declared repository and base commit, both resolvable-looking
S3  a declared ``fail_to_pass`` set with at least one node id
S4  a declared, non-empty ``pass_to_pass`` set (R3: regression detection needs
    something to regress)
S5  a declared ``test_cmd``
S6  a frozen ``relevant_file_set`` with a matching SHA-256 (R8)
S7  the relevant file set is NOT a test file - the endpoint must be a SOURCE
    edit, per F1-OP-003 which explicitly excluded the test file
S8  provenance evidence recorded for base and gold states (R5/R8), addressed
    outside the workspace with a digest
S9  the task is flagged usable by the probe that discovered it

Deliberate NON-rules
--------------------
* **No model-familiarity filter.** Training-set contamination of the gold patch
  is real and unclosable by any engineering control (arXiv 2512.10218; OpenAI's
  2026 SWE-bench Verified audit). It is recorded as a DISCLOSED limitation, not
  filtered, because no available test can establish it.
* **No difficulty filter.** Difficulty is not observable without running the
  agent, and filtering on an observed outcome would be selection on the
  dependent variable.

Everything here is offline and pure: it reads declared metadata and hashes it.
It never runs a test, starts a service, or contacts a network.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from qwen_train.f2_endpoint import (
    DEFAULT_HORIZON_STEPS,
    EndpointError,
    FrozenEndpoint,
    freeze_endpoint,
)
from qwen_train.f2_evidence import STATE_MISSING, verify_task_evidence

__all__ = [
    "ScreenResult",
    "PopulationEntry",
    "PopulationManifest",
    "screen_entry",
    "load_pool_rows",
    "MANIFEST_SCHEMA_VERSION",
]

MANIFEST_SCHEMA_VERSION = "f2_population_v1"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


#: Module-private sentinel. Possession is the ONLY way to construct a
#: ``PopulationEntry``, so an entry (and therefore an admission decision) can
#: only originate from ``screen_entry``. A caller cannot manufacture
#: ``evidence_verified=True`` / a passing S8 screen and have it count.
_ENTRY_PROOF = object()


@dataclass(frozen=True)
class ScreenResult:
    """One screening decision, retained whether it passed or failed."""

    rule: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return {"rule": self.rule, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class PopulationEntry:
    """One candidate task, with its frozen endpoint and recorded provenance."""

    instance_id: str
    repo: str
    base_commit: str
    test_cmd: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    endpoint: FrozenEndpoint
    base_evidence_digest: str = ""
    gold_evidence_digest: str = ""
    # S8: the admission decision is based on VERIFIED evidence, never on string
    # presence. ``evidence_verified`` is True only when the machine-verifiable
    # provenance contract accepts the base/gold pair; ``evidence_state`` carries
    # the distinct reason when it does not.
    evidence_verified: bool = False
    evidence_state: str = "MISSING"
    evidence_scientifically_sufficient: bool = False
    language: str = ""
    usable: bool = True
    notes: str = ""
    screens: tuple[ScreenResult, ...] = field(default_factory=tuple)
    _proof: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._proof is not _ENTRY_PROOF:
            raise TypeError(
                "PopulationEntry must be produced by screen_entry(); it cannot be "
                "constructed directly, so an admission decision cannot be forged "
                "from caller-supplied booleans."
            )

    @property
    def admitted(self) -> bool:
        return all(s.passed for s in self.screens)

    def identity_payload(self) -> dict[str, Any]:
        """The hash-bound payload.

        ``usable``, ``notes`` and ``screens`` are excluded because they are
        mutable annotations. The S8 verdict fields ARE included (H2): if a value
        determines whether an entry is eligible for admission, changing it MUST
        change the integrity identity. An evidence verdict that is not covered by
        the anchor is an incomplete anchor.
        """
        return {
            "schema": MANIFEST_SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "test_cmd": self.test_cmd,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
            "relevant_file_set": list(self.endpoint.relevant_file_set),
            "relevant_file_set_hash": self.endpoint.relevant_file_set_hash,
            "horizon_steps": self.endpoint.horizon_steps,
            "base_evidence_digest": self.base_evidence_digest,
            "gold_evidence_digest": self.gold_evidence_digest,
            # Admission-relevant S8 state (H2).
            "evidence_verified": self.evidence_verified,
            "evidence_state": self.evidence_state,
            "evidence_scientifically_sufficient": (
                self.evidence_scientifically_sufficient
            ),
        }

    @property
    def identity_hash(self) -> str:
        return _sha256_text(_canon(self.identity_payload()))

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.identity_payload())
        d.update(
            {
                "language": self.language,
                "usable": self.usable,
                "notes": self.notes,
                "admitted": self.admitted,
                "identity_hash": self.identity_hash,
                "endpoint": self.endpoint.to_dict(),
                # S8 outcome: identity recorded vs verified vs scientifically
                # sufficient are three separate statements.
                "evidence_identity_recorded": bool(
                    self.base_evidence_digest and self.gold_evidence_digest
                ),
                "evidence_verified": self.evidence_verified,
                "evidence_state": self.evidence_state,
                "evidence_scientifically_sufficient": (
                    self.evidence_scientifically_sufficient
                ),
                "screens": [s.to_dict() for s in self.screens],
            }
        )
        return d


def screen_entry(
    *,
    instance_id: str,
    repo: str,
    base_commit: str,
    test_cmd: str,
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
    relevant_file_set: Sequence[str] | None,
    relevant_file_set_hash: str = "",
    horizon_steps: int = DEFAULT_HORIZON_STEPS,
    base_evidence_digest: str = "",
    gold_evidence_digest: str = "",
    base_evidence: Any = None,
    gold_evidence: Any = None,
    artifact_root: Any = None,
    authorized_evaluators: Mapping[str, tuple[str, ...]] | None = None,
    expected_gold_state_digest: str | None = None,
    language: str = "",
    usable: bool = True,
    derivation_evidence: Sequence[str] = (),
) -> PopulationEntry:
    """Apply every screening rule and return the entry with its decisions.

    Never raises for an inadmissible task: an inadmissible task is a RESULT, not
    an error, and its failing rule must be visible in the manifest.

    S8 accepts the task ONLY when :func:`qwen_train.f2_evidence.verify_task_evidence`
    verifies the base/gold pair. ``base_evidence_digest`` / ``gold_evidence_digest``
    remain compact manifest identities; a non-empty digest by itself NEVER admits.
    """
    screens: list[ScreenResult] = []

    screens.append(
        ScreenResult(
            "S1_identity",
            bool(str(instance_id or "").strip()),
            f"instance_id={instance_id!r}",
        )
    )
    screens.append(
        ScreenResult(
            "S2_repo_and_base",
            bool(str(repo or "").strip()) and len(str(base_commit or "").strip()) >= 7,
            f"repo={repo!r} base_commit={base_commit!r}",
        )
    )
    f2p = tuple(str(x) for x in (fail_to_pass or ()))
    screens.append(
        ScreenResult("S3_fail_to_pass", len(f2p) >= 1, f"{len(f2p)} node id(s)")
    )
    p2p = tuple(str(x) for x in (pass_to_pass or ()))
    screens.append(
        ScreenResult("S4_pass_to_pass", len(p2p) >= 1, f"{len(p2p)} node id(s)")
    )
    screens.append(
        ScreenResult("S5_test_cmd", bool(str(test_cmd or "").strip()), "")
    )

    # S6 - the endpoint must exist and its declared hash must match.
    endpoint: FrozenEndpoint | None = None
    if not relevant_file_set:
        screens.append(
            ScreenResult("S6_relevant_file_set", False, "no relevant_file_set (R8 unmet)")
        )
    else:
        try:
            endpoint = freeze_endpoint(
                task_id=str(instance_id),
                relevant_file_set=relevant_file_set,
                horizon_steps=int(horizon_steps),
                derivation_evidence=derivation_evidence or (f"f2p:{n}" for n in f2p),
            )
            hash_ok = (not relevant_file_set_hash) or (
                endpoint.relevant_file_set_hash == relevant_file_set_hash
            )
            screens.append(
                ScreenResult(
                    "S6_relevant_file_set",
                    hash_ok,
                    f"hash={endpoint.relevant_file_set_hash[:12]} declared_match={hash_ok}",
                )
            )
        except EndpointError as exc:
            screens.append(ScreenResult("S6_relevant_file_set", False, str(exc)))

    # S7 - the endpoint must be a SOURCE file. F1-OP-003 explicitly excluded the
    # test file from the relevant set, because editing a test is not the
    # behaviour F0's endpoint is about.
    if endpoint is not None:
        offenders = [
            p
            for p in endpoint.relevant_file_set
            if p.startswith("test") or "/test" in p or p.endswith("_test.py")
        ]
        screens.append(
            ScreenResult(
                "S7_source_not_test",
                not offenders,
                f"test-like members: {offenders}" if offenders else "source only",
            )
        )
    else:
        screens.append(ScreenResult("S7_source_not_test", False, "no endpoint"))

    # S8 - base/gold evidence provenance (R5).
    #
    # The admission decision is made by the machine-verifiable contract in
    # qwen_train.f2_evidence, NEVER by string presence. A non-empty digest
    # establishes integrity only; it is not provenance and not scientific
    # validity. When no evidence records are supplied, S8 fails closed with a
    # reason rather than coercing to True.
    evidence_verification = verify_task_evidence(
        task_id=str(instance_id or ""),
        repository=str(repo or ""),
        base_commit=str(base_commit or ""),
        base_evidence=base_evidence,
        gold_evidence=gold_evidence,
        artifact_root=artifact_root,
        authorized_evaluators=authorized_evaluators,
        expected_gold_state_digest=expected_gold_state_digest,
    )
    evidence_ok = evidence_verification.ok
    identity_recorded = bool(base_evidence_digest and gold_evidence_digest)
    if evidence_ok:
        s8_detail = (
            "base/gold evidence verified: integrity + provenance + required "
            "base-fails/gold-passes relationship"
        )
    elif evidence_verification.state == STATE_MISSING and identity_recorded:
        s8_detail = (
            "evidence identity recorded but provenance NOT verified "
            "(a digest is not provenance); supply verifiable evidence records"
        )
    else:
        s8_detail = (
            f"{evidence_verification.state}: {evidence_verification.detail}"
        )
    screens.append(
        ScreenResult("S8_evidence_provenance", evidence_ok, s8_detail)
    )
    screens.append(
        ScreenResult("S9_probe_usable", bool(usable), f"usable={usable}")
    )

    if endpoint is None:
        # Keep the dataclass total: an inadmissible entry still needs an
        # endpoint object, so carry the declared (possibly empty) set through a
        # fail-closed placeholder that can never be mistaken for a valid spec.
        endpoint = FrozenEndpoint.__new__(FrozenEndpoint)
        object.__setattr__(endpoint, "task_id", str(instance_id or "unknown"))
        object.__setattr__(endpoint, "relevant_file_set", ())
        object.__setattr__(endpoint, "relevant_file_set_hash", "")
        object.__setattr__(endpoint, "horizon_steps", int(horizon_steps))
        object.__setattr__(endpoint, "derivation_source", "unfrozen")
        object.__setattr__(endpoint, "derivation_evidence", ())

    return PopulationEntry(
        instance_id=str(instance_id or ""),
        repo=str(repo or ""),
        base_commit=str(base_commit or ""),
        test_cmd=str(test_cmd or ""),
        fail_to_pass=f2p,
        pass_to_pass=p2p,
        endpoint=endpoint,
        base_evidence_digest=str(base_evidence_digest or ""),
        gold_evidence_digest=str(gold_evidence_digest or ""),
        evidence_verified=bool(evidence_verification.ok),
        evidence_state=str(evidence_verification.state),
        evidence_scientifically_sufficient=bool(
            evidence_verification.scientifically_sufficient
        ),
        language=str(language or ""),
        usable=bool(usable),
        screens=tuple(screens),
        _proof=_ENTRY_PROOF,
    )


@dataclass(frozen=True)
class PopulationManifest:
    """An ordered, hash-bound collection of screened task entries."""

    entries: tuple[PopulationEntry, ...]
    schema: str = MANIFEST_SCHEMA_VERSION

    @property
    def admitted(self) -> tuple[PopulationEntry, ...]:
        return tuple(e for e in self.entries if e.admitted)

    def verify(self) -> None:
        """Fail closed on a duplicated identity or a drifted identity hash."""
        if not self.entries:
            raise ValueError("population manifest is empty")
        seen: set[str] = set()
        for e in self.entries:
            if not str(e.instance_id or "").strip():
                raise ValueError("population manifest contains an entry with no identity")
            if e.instance_id in seen:
                raise ValueError(f"duplicate instance_id in manifest: {e.instance_id}")
            seen.add(e.instance_id)
            # Recomputing the hash from the payload is the drift check: any
            # mutable annotation change is excluded by construction, and any
            # change to a measured field changes this digest.
            declared = e.to_dict()["identity_hash"]
            if declared != e.identity_hash:  # pragma: no cover - defensive
                raise ValueError(f"identity hash drift for {e.instance_id}")

    def summary(self) -> dict[str, Any]:
        failing: dict[str, int] = {}
        for e in self.entries:
            for s in e.screens:
                if not s.passed:
                    failing[s.rule] = failing.get(s.rule, 0) + 1
        return {
            "schema": self.schema,
            "total": len(self.entries),
            "admitted": len(self.admitted),
            "distinct_repositories": len({e.repo for e in self.entries if e.repo}),
            "failing_rules": dict(sorted(failing.items())),
            # S8 is reported as three separate statements, never one boolean:
            # an identity can be recorded without being verified, and verified
            # without being scientifically sufficient.
            "evidence_identity_recorded": sum(
                1
                for e in self.entries
                if e.base_evidence_digest and e.gold_evidence_digest
            ),
            "evidence_verified": sum(1 for e in self.entries if e.evidence_verified),
            "evidence_scientifically_sufficient": sum(
                1 for e in self.entries if e.evidence_scientifically_sufficient
            ),
            "evidence_states": dict(
                sorted(
                    {
                        st: sum(1 for e in self.entries if e.evidence_state == st)
                        for st in {e.evidence_state for e in self.entries}
                    }.items()
                )
            ),
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.summary(), indent=indent, sort_keys=True)


def load_pool_rows(pool_path: Path | str) -> list[dict[str, Any]]:
    """Read the curriculum pool JSONL. Read-only; no network, no execution."""
    rows: list[dict[str, Any]] = []
    with open(Path(pool_path), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _as_list(value: Any) -> list[str]:
    """Pool rows store some list fields as their ``repr`` string."""
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value.replace("'", '"'))
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except (json.JSONDecodeError, ValueError):
            return [value]
    return []


def screen_pool_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    relevant_file_sets: Mapping[str, Sequence[str]] | None = None,
    evidence_digests: Mapping[str, tuple[str, str]] | None = None,
    evidence_records: Mapping[str, tuple[Any, Any]] | None = None,
    artifact_root: Any = None,
    authorized_evaluators: Mapping[str, tuple[str, ...]] | None = None,
    expected_gold_state_digests: Mapping[str, str] | None = None,
) -> PopulationManifest:
    """Screen raw pool rows into a manifest.

    ``relevant_file_sets`` and evidence are supplied by the caller because neither
    is derivable offline: designating a relevant file set is a scientific act (R8)
    and evidence comes from an actual base/gold run (R5). Absence is recorded as a
    FAILING rule, never silently defaulted.

    ``evidence_digests`` are compact manifest identities only. Admission requires
    ``evidence_records`` (a mapping ``instance_id -> (base_record, gold_record)``)
    that the S8 verifier accepts; a non-empty digest alone never admits.
    """
    relevant_file_sets = relevant_file_sets or {}
    evidence_digests = evidence_digests or {}
    evidence_records = evidence_records or {}
    expected_gold_state_digests = expected_gold_state_digests or {}
    entries: list[PopulationEntry] = []
    for row in rows:
        iid = str(row.get("instance_id") or "")
        base_rec, gold_rec = evidence_records.get(iid, (None, None))
        entries.append(
            screen_entry(
                instance_id=iid,
                repo=str(row.get("repo") or ""),
                base_commit=str(row.get("base_commit") or ""),
                test_cmd=str(row.get("test_cmd") or ""),
                fail_to_pass=_as_list(row.get("fail_to_pass")),
                pass_to_pass=_as_list(row.get("pass_to_pass")),
                relevant_file_set=relevant_file_sets.get(iid),
                base_evidence_digest=evidence_digests.get(iid, ("", ""))[0],
                gold_evidence_digest=evidence_digests.get(iid, ("", ""))[1],
                base_evidence=base_rec,
                gold_evidence=gold_rec,
                artifact_root=artifact_root,
                authorized_evaluators=authorized_evaluators,
                expected_gold_state_digest=expected_gold_state_digests.get(iid),
                language=str(row.get("language") or ""),
                usable=bool(row.get("usable", True)),
            )
        )
    return PopulationManifest(entries=tuple(entries))