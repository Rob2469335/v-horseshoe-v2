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
S10 contamination provenance - when a ``model_cutoff`` is declared, the task's
    ``created_at`` must exist and be at or after it (a PROXY for freshness, per
    the refreshed-benchmark literature; NOT proof, and the disclosure below stands)

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
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
    "screen_pool_rows",
    "deduplicate_rows",
    "contamination_policy_record",
    "load_pool_rows",
    "parse_timestamp",
    "normalise_timestamp",
    "TimestampError",
    "CONTAMINATION_CLEAN",
    "CONTAMINATION_POTENTIALLY",
    "CONTAMINATION_UNKNOWN",
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


#: The contamination classification vocabulary (S10). UNKNOWN must never be
#: silently treated as CLEAN.
CONTAMINATION_CLEAN = "CLEAN"
CONTAMINATION_POTENTIALLY = "POTENTIALLY CONTAMINATED"
CONTAMINATION_UNKNOWN = "UNKNOWN"

#: contamination_state -> classification
_CONTAMINATION_CLASS = {
    "POST_CUTOFF": CONTAMINATION_CLEAN,
    "PRE_CUTOFF": CONTAMINATION_POTENTIALLY,
    "NO_DATE": CONTAMINATION_UNKNOWN,
    "NOT_DECLARED": CONTAMINATION_UNKNOWN,
    # F2-IMPL-AUTH-029: an unparseable date, or an unparseable cutoff, is
    # UNKNOWN - never CLEAN. Fail-closed: malformed evidence is not a pass.
    "MALFORMED_DATE": CONTAMINATION_UNKNOWN,
    "CUTOFF_INVALID": CONTAMINATION_UNKNOWN,
}


class TimestampError(ValueError):
    """A timestamp (task ``created_at`` or the declared ``model_cutoff``) is unusable."""


#: Extended-format ISO-8601 only. The basic form ``YYYYMMDD`` is refused because it
#: is ambiguous and is not what any population source produces.
_ISO_EXTENDED = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"
    r"(?:Z|z|[+-]\d{2}:\d{2})?$"
)


def parse_timestamp(value: Any) -> datetime:
    """Parse a date or ISO-8601 timestamp to a timezone-aware UTC datetime.

    Accepted (F2-IMPL-AUTH-029 D4): ``YYYY-MM-DD`` and ISO-8601 date-times with an
    optional ``Z``/offset. A date-only value is midnight UTC. Everything else is
    rejected -- a malformed or ambiguous date is NEVER silently reinterpreted, and
    a missing value is NEVER converted into a passing result.

    The comparison is type-safe: two parsed datetimes are compared, not two
    strings, so a date-only cutoff and a full timestamp compare correctly.
    """
    if value is None:
        raise TimestampError("empty timestamp")
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            raise TimestampError("empty timestamp")
        if not _ISO_EXTENDED.match(text):
            raise TimestampError(f"malformed timestamp {value!r}")
        candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise TimestampError(f"malformed timestamp {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalise_timestamp(value: Any) -> str:
    """Return the canonical ``YYYY-MM-DDTHH:MM:SS`` UTC form, or raise TimestampError."""
    return parse_timestamp(value).strftime("%Y-%m-%dT%H:%M:%S")


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
    # S10: task publication timestamp and the contamination-provenance verdict.
    created_at: str = ""
    contamination_state: str = ""
    #: The provenance of the contamination verdict. A temporal cutoff is a PROXY
    #: (F2-IMPL-AUTH-029): this records what the classification is based on so a
    #: ``CLEAN`` label is never read as evidence of zero contamination.
    contamination_basis: str = ""
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
    def contamination_class(self) -> str:
        """S10 classification: CLEAN / POTENTIALLY CONTAMINATED / UNKNOWN.

        A temporal cutoff is a PROXY, not proof of zero contamination, and an
        undeclared or dateless task is UNKNOWN - never silently CLEAN.
        """
        return _CONTAMINATION_CLASS.get(
            self.contamination_state, CONTAMINATION_UNKNOWN
        )

    @property
    def admitted(self) -> bool:
        """Admission = every screen passed AND contamination is CLEAN.

        F2-IMPL-AUTH-029 D2. Previously admission depended only on the screens,
        and S10 passes vacuously when no cutoff is declared (``NOT_DECLARED``).
        That let a task be ADMITTED while ``contamination_class == UNKNOWN``,
        contradicting AUTH-028 ("UNKNOWN contamination status is NOT CLEAN").
        An undeclared cutoff therefore admits nothing.
        """
        return all(s.passed for s in self.screens) and (
            self.contamination_class == CONTAMINATION_CLEAN
        )

    @property
    def metadata_eligible(self) -> bool:
        """Passes every screen EXCEPT the base/gold execution gate (S8).

        Reported separately so the operator can see the metadata-level ceiling
        without it being confused with admission. This is NOT admission.
        """
        return all(s.passed for s in self.screens if s.rule != "S8_evidence_provenance")

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
            # F2-IMPL-AUTH-029 D1/H2: contamination now gates admission, so the
            # timestamp, verdict and its basis are part of the integrity identity.
            "created_at": self.created_at,
            "contamination_state": self.contamination_state,
            "contamination_basis": self.contamination_basis,
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
                # F2-IMPL-AUTH-029 D1: the contamination evidence must survive
                # serialization. Before this, to_dict() dropped created_at and the
                # contamination verdict, so no census could be re-screened or
                # audited for contamination.
                "created_at": self.created_at,
                "contamination_state": self.contamination_state,
                "contamination_class": self.contamination_class,
                "contamination_basis": self.contamination_basis,
                "metadata_eligible": self.metadata_eligible,
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
    created_at: str = "",
    model_cutoff: str = "",
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

    # S10: contamination provenance. A declared cutoff makes the temporal rule
    # binding and fail-closed; an undeclared cutoff leaves the disclosed-limitation
    # behaviour unchanged. F2-IMPL-AUTH-029 D4: the comparison is on PARSED
    # datetimes, and a malformed date/cutoff is UNKNOWN (never CLEAN).
    _created = str(created_at or "").strip()
    _cutoff = str(model_cutoff or "").strip()
    contamination_basis = ""
    if not _cutoff:
        contamination_state = "NOT_DECLARED"
        screens.append(
            ScreenResult(
                "S10_contamination_provenance",
                True,
                "no contamination cutoff declared; provenance remains a DISCLOSED "
                "limitation and the classification is UNKNOWN (never CLEAN)",
            )
        )
    else:
        try:
            cutoff_dt = parse_timestamp(_cutoff)
        except TimestampError as exc:
            contamination_state = "CUTOFF_INVALID"
            screens.append(
                ScreenResult("S10_contamination_provenance", False, f"cutoff unusable: {exc}")
            )
            cutoff_dt = None
        if cutoff_dt is not None:
            contamination_basis = f"temporal_proxy:{normalise_timestamp(cutoff_dt)}"
            if not _created:
                contamination_state = "NO_DATE"
                screens.append(
                    ScreenResult(
                        "S10_contamination_provenance",
                        False,
                        f"a cutoff {_cutoff!r} is in force but the task declares no "
                        "created_at, so freshness cannot be established",
                    )
                )
            else:
                try:
                    created_dt = parse_timestamp(_created)
                except TimestampError as exc:
                    contamination_state = "MALFORMED_DATE"
                    screens.append(
                        ScreenResult(
                            "S10_contamination_provenance", False, f"created_at unusable: {exc}"
                        )
                    )
                else:
                    if created_dt < cutoff_dt:
                        contamination_state = "PRE_CUTOFF"
                        screens.append(
                            ScreenResult(
                                "S10_contamination_provenance",
                                False,
                                f"created_at {normalise_timestamp(created_dt)!r} precedes "
                                f"the cutoff {normalise_timestamp(cutoff_dt)!r}",
                            )
                        )
                    else:
                        contamination_state = "POST_CUTOFF"
                        screens.append(
                            ScreenResult(
                                "S10_contamination_provenance",
                                True,
                                f"created_at {normalise_timestamp(created_dt)!r} is at or "
                                f"after the cutoff {normalise_timestamp(cutoff_dt)!r} "
                                "(temporal PROXY, not proof of non-contamination)",
                            )
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
        created_at=_created,
        contamination_state=contamination_state,
        contamination_basis=contamination_basis,
        screens=tuple(screens),
        _proof=_ENTRY_PROOF,
    )


@dataclass(frozen=True)
class PopulationManifest:
    """An ordered, hash-bound collection of screened task entries."""

    entries: tuple[PopulationEntry, ...]
    schema: str = MANIFEST_SCHEMA_VERSION
    #: Records every candidate excluded BEFORE screening (F2-IMPL-AUTH-029 D5),
    #: so a duplicate is never silently dropped. Each item is a mapping with
    #: ``instance_id``/``reason``/``detail``. Excluded rows never enter ``entries``.
    rejections: tuple[Mapping[str, Any], ...] = ()
    #: The contamination policy in force, recorded on the manifest so a census can
    #: never be read without it (F2-IMPL-AUTH-029 D1/D3). Empty when none declared.
    contamination_policy: Mapping[str, Any] = field(default_factory=dict)

    @property
    def admitted(self) -> tuple[PopulationEntry, ...]:
        return tuple(e for e in self.entries if e.admitted)

    @property
    def metadata_eligible(self) -> tuple[PopulationEntry, ...]:
        return tuple(e for e in self.entries if e.metadata_eligible)

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
            # F2-IMPL-AUTH-029 D1/D2/D3: contamination must be visible in the
            # summary, and the metadata ceiling is reported WITHOUT being confused
            # with admission.
            "metadata_eligible": len(self.metadata_eligible),
            "contamination_states": dict(
                sorted(
                    {
                        st: sum(1 for e in self.entries if e.contamination_state == st)
                        for st in {e.contamination_state for e in self.entries}
                    }.items()
                )
            ),
            "contamination_classes": dict(
                sorted(
                    {
                        c: sum(1 for e in self.entries if e.contamination_class == c)
                        for c in {e.contamination_class for e in self.entries}
                    }.items()
                )
            ),
            "contamination_policy": dict(self.contamination_policy),
            "rejections": [dict(r) for r in self.rejections],
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


def deduplicate_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministically de-duplicate candidate rows by ``instance_id`` (D5).

    Policy (F2-IMPL-AUTH-029 D5), evidence-preserving and never arbitrary:

    * No ``instance_id`` -> kept (S1 rejects it later; that is a screening result,
      not a pre-screening exclusion).
    * Byte-identical duplicates -> keep the FIRST occurrence; record the exclusion.
    * CONFLICTING duplicates (same id, different payload) -> exclude EVERY copy and
      record the conflict. The manifest could not be verified otherwise, and
      guessing which record is the real task would be an unrecorded decision.

    Returns ``(kept_rows, rejections)`` where each rejection names the excluded
    identity, its source index, and the reason.
    """
    seen: dict[str, int] = {}
    first_payload: dict[str, str] = {}
    kept: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    conflicted: set[str] = set()
    pending: list[tuple[int, dict[str, Any], str]] = []
    for index, raw in enumerate(rows):
        row = dict(raw)
        iid = str(row.get("instance_id") or "").strip()
        if not iid:
            kept.append(row)
            continue
        payload = _canon(row)
        if iid not in seen:
            seen[iid] = index
            first_payload[iid] = payload
            pending.append((index, row, payload))
        elif first_payload[iid] == payload:
            rejections.append(
                {
                    "instance_id": iid,
                    "source_index": index,
                    "kept_index": seen[iid],
                    "reason": "duplicate_instance_id_identical",
                    "detail": "byte-identical duplicate; first occurrence kept",
                }
            )
        else:
            conflicted.add(iid)
            rejections.append(
                {
                    "instance_id": iid,
                    "source_index": index,
                    "kept_index": seen[iid],
                    "reason": "duplicate_instance_id_conflicting",
                    "detail": "same instance_id, different payload; all copies excluded",
                }
            )
    for index, row, _payload in pending:
        iid = str(row.get("instance_id") or "").strip()
        if iid in conflicted:
            rejections.append(
                {
                    "instance_id": iid,
                    "source_index": index,
                    "kept_index": None,
                    "reason": "duplicate_instance_id_conflicted_removed",
                    "detail": "first occurrence withdrawn because the id conflicted",
                }
            )
        else:
            kept.append(row)
    rejections.sort(key=lambda r: (r["instance_id"], r["source_index"]))
    return kept, rejections


def contamination_policy_record(
    model_cutoff: str, *, basis: str = "operator-declared temporal proxy"
) -> dict[str, Any]:
    """The contamination policy to record on a manifest/census (D1/D3).

    A temporal cutoff is a PROXY, never proof of non-contamination. This record
    makes that explicit wherever the population is written, so a ``CLEAN`` label
    is never read as evidence the gold patch was absent from pre-training.
    """
    cutoff = str(model_cutoff or "").strip()
    record: dict[str, Any] = {
        "is_proxy": True,
        "declared": bool(cutoff),
        "cutoff": cutoff,
        "basis": basis,
        "limitation": (
            "Temporal screening is a PROXY for freshness, not proof of zero "
            "contamination; the served model's training cutoff is NOT ESTABLISHED."
        ),
    }
    if cutoff:
        try:
            record["cutoff_normalised"] = normalise_timestamp(cutoff)
        except TimestampError as exc:
            record["cutoff_normalised"] = None
            record["cutoff_error"] = str(exc)
    return record


def screen_pool_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    relevant_file_sets: Mapping[str, Sequence[str]] | None = None,
    evidence_digests: Mapping[str, tuple[str, str]] | None = None,
    evidence_records: Mapping[str, tuple[Any, Any]] | None = None,
    artifact_root: Any = None,
    authorized_evaluators: Mapping[str, tuple[str, ...]] | None = None,
    expected_gold_state_digests: Mapping[str, str] | None = None,
    model_cutoff: str = "",
    rejections: Sequence[Mapping[str, Any]] = (),
    contamination_policy: Mapping[str, Any] | None = None,
) -> PopulationManifest:
    """Screen raw pool rows into a manifest.

    ``relevant_file_sets`` and evidence are supplied by the caller because neither
    is derivable offline: designating a relevant file set is a scientific act (R8)
    and evidence comes from an actual base/gold run (R5). Absence is recorded as a
    FAILING rule, never silently defaulted.

    ``model_cutoff`` (S10) activates the temporal contamination-provenance proxy;
    when empty, contamination provenance stays a disclosed limitation.

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
                created_at=str(row.get("created_at") or ""),
                model_cutoff=model_cutoff,
            )
        )
    return PopulationManifest(
        entries=tuple(entries),
        rejections=tuple(dict(r) for r in rejections),
        contamination_policy=dict(contamination_policy or {}),
    )