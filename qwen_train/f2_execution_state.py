"""F2-S8 execution-state measurement contract (``f2-execution-state/v1``).

Why this module exists
----------------------
The frozen S8 evidence contract carries an **opaque** execution-state digest
(``SWARM_S8_BASE_STATE_DIGEST`` / ``SWARM_S8_GOLD_STATE_DIGEST``).  Until now that
value was a bare operator **assertion**: any 64-character lowercase hex string was
accepted after a shape check, so the contract could not distinguish "the operator
believes this state ran" from "this state was measured" (see
``docs/EXPERIMENT_J_F2_S8_GOLD_STATE_EVIDENCE_CONTRACT_PROPOSAL.md`` §4-§5).

This module implements the **Stage-1 measurement contract** proposed there: a
canonical, digest-bound record of a *measured* execution state, bound to the task,
the execution, the environment and the base/gold role.  It is deliberately
**inert**: it is not wired into any production evidence path, and it does not
change the semantics of ``f2_evidence`` / ``f2_s8_compose``.  Wiring it into the
governed evidence contract is a separate, owner-gated change.

Design rules (from the proposal and the repository conventions)
--------------------------------------------------------------
* Canonical JSON: ``json.dumps(payload, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False).encode("utf-8")`` -- the same convention as
  ``f2_evidence._canonical_bytes`` and ``f2_authorization.canonical_bytes``.
* One algorithm, one encoding.  Both are named **inside** the hashed payload, so a
  record produced under an unknown algorithm or encoding fails closed instead of
  being silently reinterpreted.
* Timestamps are audit metadata and are **excluded** from the hashed payload (a
  clock is not a trust anchor), mirroring ``EvidenceRecord.canonical_payload``.
* Fail closed with a *named* state; never a generic error.

What the measurement covers -- and what it does not
--------------------------------------------------
``tree_digest`` covers the evaluated **source tree** (relative path + size +
content digest + executable bit for every file, symlinks recorded as targets,
excluded directories recorded explicitly).  It does **not** prove semantic
equivalence of two trees that hash equally (collision resistance is assumed, not
proven), and it is **not** a hash of VM resource configuration: vCPU/RAM are not
part of an execution state.  Dependencies are captured *separately* by
``installed_distributions_digest`` and the interpreter identity, exactly as the
proposal requires.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "MEASUREMENT_ALGORITHM",
    "CANONICALIZATION",
    "SIDE_BASE",
    "SIDE_GOLD",
    "VALID_SIDES",
    "EXECUTION_STATE_STATES",
    "STATE_ES_VERIFIED",
    "STATE_ES_MISSING",
    "STATE_ES_MALFORMED",
    "STATE_ES_UNKNOWN_SCHEMA",
    "STATE_ES_UNKNOWN_ALGORITHM",
    "STATE_ES_NONCANONICAL",
    "STATE_ES_DIGEST_MISMATCH",
    "STATE_ES_IDENTITY_MISMATCH",
    "STATE_ES_ROLE_MISMATCH",
    "STATE_ES_ENVIRONMENT_MISMATCH",
    "STATE_ES_INCOMPLETE",
    "STATE_ES_PROVENANCE_NOT_ESTABLISHED",
    "STATE_ES_REPLAY",
    "STATE_ES_STALE",
    "STATE_ES_STATES_EQUAL",
    "DEFAULT_EXCLUDED_DIR_NAMES",
    "DEFAULT_EXCLUDED_DIR_SUFFIXES",
    "DEFAULT_EXCLUDED_ENV_DIRS",
    "MeasurementError",
    "TreeMeasurement",
    "ExecutionState",
    "ExecutionStateVerification",
    "measure_tree",
    "measure_interpreter",
    "measure_installed_distributions",
    "build_execution_state",
    "build_execution_state_from_disk",
    "verify_execution_state",
    "verify_execution_state_pair",
    "main",
]

SCHEMA_VERSION = "f2-execution-state/v1"
MEASUREMENT_ALGORITHM = "f2-tree-sha256/v1"
CANONICALIZATION = "json-canonical/v1"

SIDE_BASE = "base"
SIDE_GOLD = "gold"
VALID_SIDES = (SIDE_BASE, SIDE_GOLD)

#: Excluded directory *names* (matched at any depth).  The interpreter
#: environments are excluded because they are captured by
#: ``installed_distributions_digest`` + the interpreter identity; VCS metadata,
#: byte-code caches and test caches are not part of the evaluated source state.
#: Every exclusion is recorded inside the hashed payload, so the measurement is
#: self-describing and reproducible.
DEFAULT_EXCLUDED_DIR_NAMES = (
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
)
DEFAULT_EXCLUDED_DIR_SUFFIXES = (".egg-info", ".dist-info")
DEFAULT_EXCLUDED_ENV_DIRS = ("venv", ".venv", ".venv_gold")

#: Named, non-collapsible outcomes.  ES_VERIFIED is the only accepting state.
STATE_ES_VERIFIED = "ES_VERIFIED"
STATE_ES_MISSING = "ES_MISSING"
STATE_ES_MALFORMED = "ES_MALFORMED"
STATE_ES_UNKNOWN_SCHEMA = "ES_UNKNOWN_SCHEMA"
STATE_ES_UNKNOWN_ALGORITHM = "ES_UNKNOWN_ALGORITHM"
STATE_ES_NONCANONICAL = "ES_NONCANONICAL"
STATE_ES_DIGEST_MISMATCH = "ES_DIGEST_MISMATCH"
STATE_ES_IDENTITY_MISMATCH = "ES_IDENTITY_MISMATCH"
STATE_ES_ROLE_MISMATCH = "ES_ROLE_MISMATCH"
STATE_ES_ENVIRONMENT_MISMATCH = "ES_ENVIRONMENT_MISMATCH"
STATE_ES_INCOMPLETE = "ES_INCOMPLETE"
STATE_ES_PROVENANCE_NOT_ESTABLISHED = "ES_PROVENANCE_NOT_ESTABLISHED"
STATE_ES_REPLAY = "ES_REPLAY"
STATE_ES_STALE = "ES_STALE"
STATE_ES_STATES_EQUAL = "ES_STATES_EQUAL"

EXECUTION_STATE_STATES = (
    STATE_ES_VERIFIED,
    STATE_ES_MISSING,
    STATE_ES_MALFORMED,
    STATE_ES_UNKNOWN_SCHEMA,
    STATE_ES_UNKNOWN_ALGORITHM,
    STATE_ES_NONCANONICAL,
    STATE_ES_DIGEST_MISMATCH,
    STATE_ES_IDENTITY_MISMATCH,
    STATE_ES_ROLE_MISMATCH,
    STATE_ES_ENVIRONMENT_MISMATCH,
    STATE_ES_INCOMPLETE,
    STATE_ES_PROVENANCE_NOT_ESTABLISHED,
    STATE_ES_REPLAY,
    STATE_ES_STALE,
    STATE_ES_STATES_EQUAL,
)

_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class MeasurementError(RuntimeError):
    """A read-only measurement could not be completed (fail closed)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    s = str(value or "")
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Deterministic UTF-8 canonical JSON (repository convention)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _posix_rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel


# ---------------------------------------------------------------------------
# Layer 1 - read-only tree measurement
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TreeMeasurement:
    """A deterministic read-only measurement of one evaluated source tree."""

    algorithm: str
    label: str
    file_count: int
    total_bytes: int
    symlink_count: int
    excluded: tuple[str, ...]
    entries_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "label": self.label,
            "file_count": int(self.file_count),
            "total_bytes": int(self.total_bytes),
            "symlink_count": int(self.symlink_count),
            "excluded": list(self.excluded),
            "entries_digest": self.entries_digest,
        }


def _is_excluded_dir(name: str, *, names: Sequence[str], suffixes: Sequence[str]) -> bool:
    if name in names:
        return True
    return any(name.endswith(sfx) for sfx in suffixes)


def _hash_file_exact(path: Path) -> tuple[str, int]:
    """Digest and size from a SINGLE opened handle (no stat-then-open window)."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def measure_tree(
    root: Path | str,
    *,
    label: str = "",
    excluded_dir_names: Sequence[str] = DEFAULT_EXCLUDED_DIR_NAMES,
    excluded_dir_suffixes: Sequence[str] = DEFAULT_EXCLUDED_DIR_SUFFIXES,
    excluded_env_dirs: Sequence[str] = DEFAULT_EXCLUDED_ENV_DIRS,
) -> TreeMeasurement:
    """Measure the source tree at ``root`` deterministically and read-only.

    Encoding of one entry (LF-terminated, joined in ascending relative-path order)::

        <relpath>\\t<kind>\\t<size>\\t<payload>\\t<exec_bit>\\n

    ``kind`` is ``f`` (regular file: payload = sha256 of the exact bytes) or
    ``l`` (symlink: payload = link target text; never followed, never read
    through).  Excluded directories contribute **no** entry -- the digest is the
    content identity of the evaluated source tree -- but the exclusion set is
    returned in ``excluded`` and is bound into the record payload, so an
    exclusion is declared rather than silent.

    ``relpath`` is POSIX-normalised (``/``) and relative to ``root``, so the
    measurement is independent of the absolute mount point.  A missing root or an
    unreadable file raises :class:`MeasurementError` -- the caller maps that to a
    fail-closed ``ES_INCOMPLETE`` outcome rather than measuring a partial tree.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise MeasurementError(f"measurement root is not a directory: {root_path}")
    names = tuple(dict.fromkeys([*excluded_dir_names, *excluded_env_dirs]))
    suffixes = tuple(excluded_dir_suffixes)

    entries: list[tuple[str, str, int, str, int]] = []
    excluded_seen: list[str] = []
    symlinks = 0

    # Iterative, sorted, no symlink traversal.  ``os.walk(followlinks=False)``
    # would treat a symlinked directory as a leaf without recording it; this walk
    # records it explicitly instead of dropping it silently.
    dirs: list[tuple[Path, str]] = [(root_path, "dir")]
    while dirs:
        current, kind = dirs.pop()
        try:
            with os.scandir(current) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            raise MeasurementError(f"unreadable directory {current}: {exc}") from exc
        for entry in children:
            path = Path(entry.path)
            rel = _posix_rel(path, root_path)
            if entry.is_symlink():
                try:
                    target = os.readlink(path)
                except OSError as exc:
                    raise MeasurementError(f"unreadable symlink {rel}: {exc}") from exc
                entries.append((rel, "l", 0, target, 0))
                symlinks += 1
                continue
            if entry.is_dir(follow_symlinks=False):
                if kind == "dir" and _is_excluded_dir(
                    entry.name, names=names, suffixes=suffixes
                ):
                    excluded_seen.append(rel)
                    continue
                dirs.append((path, "dir"))
                continue
            try:
                digest, size = _hash_file_exact(path)
            except OSError as exc:
                raise MeasurementError(f"unreadable file {rel}: {exc}") from exc
            exec_bit = 1 if os.access(path, os.X_OK) else 0
            entries.append((rel, "f", size, digest, exec_bit))

    entries.sort(key=lambda t: t[0])
    stream = "".join(
        f"{rel}\t{kind}\t{size}\t{payload}\t{exec_bit}\n"
        for rel, kind, size, payload, exec_bit in entries
    ).encode("utf-8")

    files = [e for e in entries if e[1] == "f"]
    return TreeMeasurement(
        algorithm=MEASUREMENT_ALGORITHM,
        label=str(label or ""),
        file_count=len(files),
        total_bytes=sum(e[2] for e in files),
        symlink_count=symlinks,
        excluded=tuple(sorted(excluded_seen)),
        entries_digest=_sha256(stream),
    )


def measure_interpreter() -> str:
    """Identity of the interpreter that ran the measurement (read-only)."""
    impl = getattr(sys.implementation, "name", "python")
    version = ".".join(str(p) for p in sys.version_info[:3])
    return f"{impl}-{version}:{Path(sys.executable).as_posix()}"


def measure_installed_distributions() -> tuple[str, int]:
    """Deterministic digest + count of the installed distributions (read-only).

    ``name==version`` for every installed distribution, sorted, LF-joined.  This
    captures the *dependency set* without hashing the environment directories.
    """
    import importlib.metadata as md

    names: set[str] = set()
    for dist in md.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:  # noqa: BLE001 - a malformed dist must not abort the set
            name = None
        if not name:
            continue
        names.add(f"{name}=={dist.version}")
    ordered = sorted(names)
    digest = _sha256(("\n".join(ordered) + "\n").encode("utf-8")) if ordered else ""
    return digest, len(ordered)


# ---------------------------------------------------------------------------
# Layer 2 - the canonical execution-state record
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS = (
    "schema_version",
    "experiment",
    "stage",
    "operation",
    "instance_id",
    "repository",
    "base_commit",
    "execution_state_identity",
    "environment_identity",
    "test_command",
    "measurement_algorithm",
    "canonicalization",
    "tree_digest",
    "interpreter",
    "evaluator_implementation_digest",
    "procedure_id",
    "protocol_version",
    "test_patch_digest",
    "run_nonce",
    "measured_state_digest",
)

_INT_FIELDS = (
    "tree_file_count",
    "tree_total_bytes",
    "tree_symlink_count",
    "installed_distribution_count",
)


@dataclass(frozen=True)
class ExecutionState:
    """One canonical, digest-bound MEASUREMENT of a single base OR gold state."""

    schema_version: str
    experiment: str
    stage: str
    operation: str
    instance_id: str
    repository: str
    base_commit: str
    execution_state_identity: str
    environment_identity: str
    test_command: str
    measurement_algorithm: str
    canonicalization: str
    tree_digest: str
    tree_file_count: int
    tree_total_bytes: int
    tree_symlink_count: int
    interpreter: str
    installed_distributions_digest: str
    installed_distribution_count: int
    evaluator_implementation_digest: str
    procedure_id: str
    protocol_version: str
    test_patch_digest: str
    gold_patch_digest: str
    run_nonce: str
    measured_at: str = ""
    measured_state_digest: str = ""
    execution_state_digest: str = ""
    excluded_paths: tuple[str, ...] = ()

    # -- canonicalisation -------------------------------------------------
    def measured_state_payload(self) -> dict[str, Any]:
        """The payload of the *measured state* alone.

        Deliberately excludes task/execution identity, the base/gold role, the
        patch digests and the run nonce: those are provenance, not state.  Two
        executions of the same source tree in the same environment therefore have
        the same measured-state digest regardless of which side they ran, which
        is what lets an equal base/gold pair be *detected* instead of being
        silently forced apart.  This is the value that feeds S8's opaque
        ``SWARM_S8_{BASE,GOLD}_STATE_DIGEST``.
        """
        return {
            "measurement_algorithm": self.measurement_algorithm,
            "canonicalization": self.canonicalization,
            "tree_digest": self.tree_digest,
            "tree_file_count": int(self.tree_file_count),
            "tree_total_bytes": int(self.tree_total_bytes),
            "tree_symlink_count": int(self.tree_symlink_count),
            "excluded_paths": list(self.excluded_paths),
            "interpreter": self.interpreter,
            "installed_distributions_digest": self.installed_distributions_digest,
            "installed_distribution_count": int(self.installed_distribution_count),
            "evaluator_implementation_digest": self.evaluator_implementation_digest,
        }

    def compute_measured_state_digest(self) -> str:
        return _sha256(_canonical_bytes(self.measured_state_payload()))

    def canonical_payload(self) -> dict[str, Any]:
        """Deterministic payload.  ``measured_at`` is audit-only (excluded)."""
        return {
            "schema_version": self.schema_version,
            "experiment": self.experiment,
            "stage": self.stage,
            "operation": self.operation,
            "instance_id": self.instance_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "execution_state_identity": self.execution_state_identity,
            "environment_identity": self.environment_identity,
            "test_command": self.test_command,
            "measurement_algorithm": self.measurement_algorithm,
            "canonicalization": self.canonicalization,
            "tree_digest": self.tree_digest,
            "tree_file_count": int(self.tree_file_count),
            "tree_total_bytes": int(self.tree_total_bytes),
            "tree_symlink_count": int(self.tree_symlink_count),
            "interpreter": self.interpreter,
            "installed_distributions_digest": self.installed_distributions_digest,
            "installed_distribution_count": int(self.installed_distribution_count),
            "evaluator_implementation_digest": self.evaluator_implementation_digest,
            "procedure_id": self.procedure_id,
            "protocol_version": self.protocol_version,
            "test_patch_digest": self.test_patch_digest,
            "gold_patch_digest": self.gold_patch_digest,
            "run_nonce": self.run_nonce,
            "excluded_paths": list(self.excluded_paths),
            "measured_state_digest": self.measured_state_digest,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.canonical_payload())

    def compute_digest(self) -> str:
        return _sha256(self.canonical_bytes())

    def verify_digest(self) -> bool:
        return bool(self.execution_state_digest) and (
            self.execution_state_digest == self.compute_digest()
        )

    def to_dict(self) -> dict[str, Any]:
        d = self.canonical_payload()
        d["measured_at"] = self.measured_at
        d["execution_state_digest"] = self.execution_state_digest
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ExecutionState":
        def _int(key: str) -> int:
            try:
                return int(d.get(key) or 0)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an integer") from None

        excluded = d.get("excluded_paths") or []
        if not isinstance(excluded, (list, tuple)):
            raise ValueError("excluded_paths must be a list")
        return cls(
            schema_version=str(d.get("schema_version") or ""),
            experiment=str(d.get("experiment") or ""),
            stage=str(d.get("stage") or ""),
            operation=str(d.get("operation") or ""),
            instance_id=str(d.get("instance_id") or ""),
            repository=str(d.get("repository") or ""),
            base_commit=str(d.get("base_commit") or ""),
            execution_state_identity=str(d.get("execution_state_identity") or ""),
            environment_identity=str(d.get("environment_identity") or ""),
            test_command=str(d.get("test_command") or ""),
            measurement_algorithm=str(d.get("measurement_algorithm") or ""),
            canonicalization=str(d.get("canonicalization") or ""),
            tree_digest=str(d.get("tree_digest") or ""),
            tree_file_count=_int("tree_file_count"),
            tree_total_bytes=_int("tree_total_bytes"),
            tree_symlink_count=_int("tree_symlink_count"),
            interpreter=str(d.get("interpreter") or ""),
            installed_distributions_digest=str(
                d.get("installed_distributions_digest") or ""
            ),
            installed_distribution_count=_int("installed_distribution_count"),
            evaluator_implementation_digest=str(
                d.get("evaluator_implementation_digest") or ""
            ),
            procedure_id=str(d.get("procedure_id") or ""),
            protocol_version=str(d.get("protocol_version") or ""),
            test_patch_digest=str(d.get("test_patch_digest") or ""),
            gold_patch_digest=str(d.get("gold_patch_digest") or ""),
            run_nonce=str(d.get("run_nonce") or ""),
            measured_at=str(d.get("measured_at") or ""),
            measured_state_digest=str(d.get("measured_state_digest") or ""),
            execution_state_digest=str(d.get("execution_state_digest") or ""),
            excluded_paths=tuple(str(p) for p in excluded),
        )


@dataclass(frozen=True)
class ExecutionStateVerification:
    """Result of verifying one execution-state record or one base/gold pair.

    Trust boundary (mirrors ``f2_evidence.EvidenceVerification``): the module-private
    sentinel makes the verifier the only producer of a result object.
    """

    state: str
    detail: str = ""
    measured_ok: bool = False
    identity_ok: bool = False
    provenance_ok: bool = False
    _proof: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._proof is not _ES_PROOF:
            raise TypeError(
                "ExecutionStateVerification must be produced by "
                "verify_execution_state / verify_execution_state_pair."
            )

    @property
    def ok(self) -> bool:
        return self.state == STATE_ES_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "measured_ok": self.measured_ok,
            "identity_ok": self.identity_ok,
            "provenance_ok": self.provenance_ok,
        }


_ES_PROOF = object()


def _es(state: str, detail: str, **ok: bool) -> ExecutionStateVerification:
    return ExecutionStateVerification(state=state, detail=detail, _proof=_ES_PROOF, **ok)


def build_execution_state(
    *,
    instance_id: str,
    repository: str,
    base_commit: str,
    execution_state_identity: str,
    environment_identity: str,
    test_command: str,
    tree: TreeMeasurement,
    interpreter: str,
    installed_distributions_digest: str,
    installed_distribution_count: int,
    evaluator_implementation_digest: str,
    procedure_id: str,
    protocol_version: str,
    test_patch_digest: str,
    run_nonce: str,
    gold_patch_digest: str = "",
    measured_at: str = "",
    experiment: str = "J",
    stage: str = "S8-001",
    operation: str = "base_gold_production",
) -> ExecutionState:
    """Construct a record and bind it to its own computed digest."""
    rec = ExecutionState(
        schema_version=SCHEMA_VERSION,
        experiment=experiment,
        stage=stage,
        operation=operation,
        instance_id=instance_id,
        repository=repository,
        base_commit=base_commit,
        execution_state_identity=execution_state_identity,
        environment_identity=environment_identity,
        test_command=test_command,
        measurement_algorithm=tree.algorithm,
        canonicalization=CANONICALIZATION,
        tree_digest=tree.entries_digest,
        tree_file_count=tree.file_count,
        tree_total_bytes=tree.total_bytes,
        tree_symlink_count=tree.symlink_count,
        interpreter=interpreter,
        installed_distributions_digest=installed_distributions_digest,
        installed_distribution_count=int(installed_distribution_count),
        evaluator_implementation_digest=evaluator_implementation_digest,
        procedure_id=procedure_id,
        protocol_version=protocol_version,
        test_patch_digest=test_patch_digest,
        gold_patch_digest=gold_patch_digest,
        run_nonce=run_nonce,
        measured_at=measured_at,
        excluded_paths=tree.excluded,
    )
    with_state = ExecutionState(
        **{**rec.__dict__, "measured_state_digest": rec.compute_measured_state_digest()}
    )
    return ExecutionState(
        **{**with_state.__dict__, "execution_state_digest": with_state.compute_digest()}
    )


def build_execution_state_from_disk(
    root: Path | str,
    *,
    instance_id: str,
    repository: str,
    base_commit: str,
    execution_state_identity: str,
    environment_identity: str,
    test_command: str,
    evaluator_implementation_digest: str,
    procedure_id: str,
    protocol_version: str,
    test_patch_digest: str,
    run_nonce: str,
    gold_patch_digest: str = "",
    measured_at: str = "",
    label: str = "",
    interpreter: str | None = None,
    distributions: tuple[str, int] | None = None,
) -> ExecutionState:
    """Measure ``root`` from disk and build the bound record.

    Interpreter and distribution-set identities default to the *measuring*
    process; pass them explicitly when measuring a different environment (the
    proposal's Stage-1 arrangement is that the guest measures itself).
    """
    tree = measure_tree(root, label=label or Path(root).name)
    if interpreter is None:
        interpreter = measure_interpreter()
    if distributions is None:
        distributions = measure_installed_distributions()
    return build_execution_state(
        instance_id=instance_id,
        repository=repository,
        base_commit=base_commit,
        execution_state_identity=execution_state_identity,
        environment_identity=environment_identity,
        test_command=test_command,
        tree=tree,
        interpreter=interpreter,
        installed_distributions_digest=distributions[0],
        installed_distribution_count=distributions[1],
        evaluator_implementation_digest=evaluator_implementation_digest,
        procedure_id=procedure_id,
        protocol_version=protocol_version,
        test_patch_digest=test_patch_digest,
        run_nonce=run_nonce,
        gold_patch_digest=gold_patch_digest,
        measured_at=measured_at,
    )


def verify_execution_state(
    record: ExecutionState | Mapping[str, Any] | None,
    *,
    expected_instance_id: str | None = None,
    expected_repository: str | None = None,
    expected_base_commit: str | None = None,
    expected_side: str | None = None,
    expected_environment_identity: str | None = None,
    required_algorithm: str | None = MEASUREMENT_ALGORITHM,
    required_canonicalization: str | None = CANONICALIZATION,
    expected_run_nonce: str | None = None,
    expected_gold_state_digest: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    now: str | None = None,
    require_measurement: bool = True,
) -> ExecutionStateVerification:
    """Verify ONE execution-state record, fail-closed with a named state.

    A syntactically valid but fabricated 64-hex digest is **not** proof of a
    measurement: with ``require_measurement`` (the default) the record must carry
    a complete measurement (tree digest + counts, interpreter, distribution-set
    digest, evaluator digest, patches, nonce) whose canonical payload hashes to
    the recorded digest.  A bare digest without the measurement fails as
    ``ES_INCOMPLETE``.
    """
    if record is None:
        return _es(STATE_ES_MISSING, "no execution-state record supplied")
    if isinstance(record, Mapping):
        try:
            record = ExecutionState.from_dict(record)
        except Exception as exc:  # noqa: BLE001 - normalise to a reason
            return _es(STATE_ES_MALFORMED, f"record could not be parsed: {exc}")
    if not isinstance(record, ExecutionState):
        return _es(
            STATE_ES_MALFORMED, f"unsupported record type: {type(record).__name__}"
        )

    # ORDER: presence -> schema -> algorithm -> shape -> measurement -> identity.
    for name in _REQUIRED_FIELDS:
        if not str(getattr(record, name) or "").strip():
            return _es(STATE_ES_MALFORMED, f"missing required field: {name}")

    if record.schema_version != SCHEMA_VERSION:
        return _es(
            STATE_ES_UNKNOWN_SCHEMA,
            f"unknown schema_version {record.schema_version!r} "
            f"(expected {SCHEMA_VERSION!r})",
        )
    if required_algorithm is not None and record.measurement_algorithm != required_algorithm:
        return _es(
            STATE_ES_UNKNOWN_ALGORITHM,
            f"unknown measurement_algorithm {record.measurement_algorithm!r} "
            f"(expected {required_algorithm!r}); the digest cannot be reinterpreted",
        )
    if (
        required_canonicalization is not None
        and record.canonicalization != required_canonicalization
    ):
        return _es(
            STATE_ES_NONCANONICAL,
            f"unknown canonicalization {record.canonicalization!r} "
            f"(expected {required_canonicalization!r})",
        )
    if record.execution_state_identity not in VALID_SIDES:
        return _es(
            STATE_ES_MALFORMED,
            f"execution_state_identity must be one of {VALID_SIDES}, "
            f"got {record.execution_state_identity!r}",
        )
    for label, value in (
        ("tree_digest", record.tree_digest),
        ("evaluator_implementation_digest", record.evaluator_implementation_digest),
        ("test_patch_digest", record.test_patch_digest),
        ("installed_distributions_digest", record.installed_distributions_digest),
        ("measured_state_digest", record.measured_state_digest),
    ):
        if not _is_sha256(value):
            return _es(
                STATE_ES_MALFORMED, f"{label} is not a 64-hex SHA-256 digest"
            )
    for name in _INT_FIELDS:
        value = getattr(record, name)
        if not isinstance(value, int) or value < 0:
            return _es(STATE_ES_MALFORMED, f"{name} must be a non-negative integer")
    if record.gold_patch_digest and not _is_sha256(record.gold_patch_digest):
        return _es(
            STATE_ES_MALFORMED, "gold_patch_digest is not a 64-hex SHA-256 digest"
        )

    # ROLE: base carries no gold patch; gold carries one.  A swapped/mislabeled
    # pair is caught here rather than silently accepted.
    if record.execution_state_identity == SIDE_BASE and record.gold_patch_digest:
        return _es(
            STATE_ES_ROLE_MISMATCH,
            "a base-side record declares a gold_patch_digest",
        )
    if record.execution_state_identity == SIDE_GOLD and not record.gold_patch_digest:
        return _es(
            STATE_ES_ROLE_MISMATCH,
            "a gold-side record declares no gold_patch_digest",
        )

    if require_measurement:
        if record.tree_file_count <= 0 and record.tree_symlink_count <= 0:
            return _es(
                STATE_ES_INCOMPLETE,
                "the record carries no measured tree (file_count=0 and "
                "symlink_count=0); a bare digest is not a measurement",
            )
        if record.installed_distribution_count <= 0:
            return _es(
                STATE_ES_INCOMPLETE,
                "the record carries no measured distribution set; a bare digest is "
                "not a measurement",
            )

    # MEASUREMENT INTEGRITY: the digest must cover the measurement it claims.
    if not record.verify_digest():
        return _es(
            STATE_ES_DIGEST_MISMATCH,
            "record digest does not match its canonical payload "
            f"(recorded {record.execution_state_digest[:12]}…, computed "
            f"{record.compute_digest()[:12]}…)",
        )
    if record.measured_state_digest != record.compute_measured_state_digest():
        return _es(
            STATE_ES_DIGEST_MISMATCH,
            "measured_state_digest does not match the measurement it claims",
        )

    # IDENTITY / ENVIRONMENT / ROLE
    if expected_instance_id is not None and record.instance_id != expected_instance_id:
        return _es(
            STATE_ES_IDENTITY_MISMATCH,
            f"instance_id {record.instance_id!r} != {expected_instance_id!r}",
        )
    if expected_repository is not None and record.repository != expected_repository:
        return _es(
            STATE_ES_IDENTITY_MISMATCH,
            f"repository {record.repository!r} != {expected_repository!r}",
        )
    if expected_base_commit is not None and record.base_commit != expected_base_commit:
        return _es(
            STATE_ES_IDENTITY_MISMATCH,
            f"base_commit {record.base_commit[:12]}… != {expected_base_commit[:12]}…",
        )
    if expected_side is not None and record.execution_state_identity != expected_side:
        return _es(
            STATE_ES_ROLE_MISMATCH,
            f"record declares side {record.execution_state_identity!r}, "
            f"expected {expected_side!r}",
        )
    if (
        expected_environment_identity is not None
        and record.environment_identity != expected_environment_identity
    ):
        return _es(
            STATE_ES_ENVIRONMENT_MISMATCH,
            f"environment_identity {record.environment_identity!r} != "
            f"{expected_environment_identity!r}",
        )

    # PROVENANCE: the record must name the execution provenance inputs.
    if not record.procedure_id or not record.protocol_version:
        return _es(
            STATE_ES_PROVENANCE_NOT_ESTABLISHED,
            "procedure_id / protocol_version are required to establish provenance",
        )

    # REPLAY
    if expected_run_nonce is not None and record.run_nonce != expected_run_nonce:
        return _es(
            STATE_ES_REPLAY,
            f"run_nonce {record.run_nonce!r} != expected {expected_run_nonce!r} "
            "(a replayed or substituted measurement)",
        )

    # STALENESS: only enforced when a validity window is supplied.
    if valid_from or valid_until:
        if not _ISO_RE.match(record.measured_at or ""):
            return _es(
                STATE_ES_STALE,
                "a validity window was supplied but measured_at is not an "
                "ISO-8601 tz-aware timestamp",
            )
        if valid_from and record.measured_at < valid_from:
            return _es(
                STATE_ES_STALE,
                f"measured_at {record.measured_at} precedes valid_from {valid_from}",
            )
        if valid_until and record.measured_at > valid_until:
            return _es(
                STATE_ES_STALE,
                f"measured_at {record.measured_at} exceeds valid_until {valid_until}",
            )
    if now is not None and not _ISO_RE.match(record.measured_at or ""):
        return _es(STATE_ES_STALE, "measured_at is missing or not ISO-8601")

    # EXPECTED GOLD DIGEST (mirrors verify_task_evidence's parameter semantics)
    if (
        expected_gold_state_digest is not None
        and record.execution_state_identity == SIDE_GOLD
        and record.execution_state_digest != expected_gold_state_digest
    ):
        return _es(
            STATE_ES_IDENTITY_MISMATCH,
            "gold record digest does not match the expected reference state",
        )

    return _es(
        STATE_ES_VERIFIED,
        "measurement integrity, task/execution identity and provenance verified",
        measured_ok=True,
        identity_ok=True,
        provenance_ok=True,
    )


def verify_execution_state_pair(
    *,
    instance_id: str,
    repository: str,
    base_commit: str,
    base_record: ExecutionState | Mapping[str, Any] | None,
    gold_record: ExecutionState | Mapping[str, Any] | None,
    environment_identity: str | None = None,
    expected_gold_state_digest: str | None = None,
    **kw: Any,
) -> ExecutionStateVerification:
    """Verify a base/gold measurement PAIR, fail-closed.

    Requires both records to verify individually against the declared task
    identity, to declare the correct sides, and to describe **distinct** measured
    states.  If the two measured digests are equal the contract does **not**
    silently force them apart: the result is reported truthfully as
    ``ES_STATES_EQUAL`` (the S8 pair relationship requires distinct states).
    """
    if base_record is None or gold_record is None:
        missing = "base" if base_record is None else "gold"
        return _es(STATE_ES_MISSING, f"{missing} execution-state record missing")

    base_v = verify_execution_state(
        base_record,
        expected_instance_id=instance_id,
        expected_repository=repository,
        expected_base_commit=base_commit,
        expected_side=SIDE_BASE,
        expected_environment_identity=environment_identity,
        **kw,
    )
    if not base_v.ok:
        return _es(base_v.state, f"base: {base_v.detail}")
    gold_v = verify_execution_state(
        gold_record,
        expected_instance_id=instance_id,
        expected_repository=repository,
        expected_base_commit=base_commit,
        expected_side=SIDE_GOLD,
        expected_environment_identity=environment_identity,
        expected_gold_state_digest=expected_gold_state_digest,
        **kw,
    )
    if not gold_v.ok:
        return _es(gold_v.state, f"gold: {gold_v.detail}")

    base_rec = (
        base_record
        if isinstance(base_record, ExecutionState)
        else ExecutionState.from_dict(base_record)
    )
    gold_rec = (
        gold_record
        if isinstance(gold_record, ExecutionState)
        else ExecutionState.from_dict(gold_record)
    )

    if base_rec.measured_state_digest == gold_rec.measured_state_digest:
        return _es(
            STATE_ES_STATES_EQUAL,
            "base and gold measured the SAME state (equal measured_state_digest); "
            "the S8 pair relationship requires two distinct states",
        )

    return _es(
        STATE_ES_VERIFIED,
        "base/gold measurement pair verified: distinct measured states, bound "
        "identity and provenance",
        measured_ok=True,
        identity_ok=True,
        provenance_ok=True,
    )


# ---------------------------------------------------------------------------
# Operator-facing measurement producer
# ---------------------------------------------------------------------------
def _sha256_file(path: Path | str) -> str:
    p = Path(path)
    if not p.is_file():
        raise MeasurementError(f"not a file: {p}")
    return _hash_file_exact(p)[0]


def main(argv: Sequence[str] | None = None) -> int:
    """Read-only producer/verifier CLI.

    ``measure`` reads a source tree and emits the bound ``f2-execution-state/v1``
    record -- the value that feeds S8's opaque ``SWARM_S8_{BASE,GOLD}_STATE_DIGEST``
    is ``measured_state_digest``.  ``verify`` re-checks a produced record against a
    declared task/execution identity.  Neither writes anything except the file
    named by ``--out``, and neither consumes any authorization.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m qwen_train.f2_execution_state")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _common(p: "argparse.ArgumentParser") -> None:
        p.add_argument("--instance-id", required=True)
        p.add_argument("--repository", required=True)
        p.add_argument("--base-commit", required=True)
        p.add_argument("--environment-identity", required=True)
        p.add_argument("--side", required=True, choices=VALID_SIDES)

    m = sub.add_parser("measure", help="measure a source tree into a bound record")
    _common(m)
    m.add_argument("--root", required=True, help="source tree to measure (read-only)")
    m.add_argument("--test-command", required=True)
    m.add_argument("--evaluator-implementation", required=True, help="file to hash")
    m.add_argument("--procedure-id", required=True)
    m.add_argument("--protocol-version", required=True)
    m.add_argument("--test-patch", required=True, help="file whose SHA-256 is the patch digest")
    m.add_argument("--gold-patch", default="", help="gold patch file (gold side only)")
    m.add_argument("--run-nonce", required=True, help="unique execution nonce")
    m.add_argument("--measured-at", default="", help="ISO-8601 tz-aware timestamp")
    m.add_argument("--out", default="", help="write the record JSON here (default: stdout)")

    v = sub.add_parser("verify", help="verify a produced record fail-closed")
    v.add_argument("--record", required=True)
    _common(v)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "measure":
        gold_patch = (
            _sha256_file(args.gold_patch) if str(args.gold_patch).strip() else ""
        )
        record = build_execution_state_from_disk(
            args.root,
            instance_id=args.instance_id,
            repository=args.repository,
            base_commit=args.base_commit,
            execution_state_identity=args.side,
            environment_identity=args.environment_identity,
            test_command=args.test_command,
            evaluator_implementation_digest=_sha256_file(args.evaluator_implementation),
            procedure_id=args.procedure_id,
            protocol_version=args.protocol_version,
            test_patch_digest=_sha256_file(args.test_patch),
            run_nonce=args.run_nonce,
            gold_patch_digest=gold_patch,
            measured_at=args.measured_at,
            label=args.side,
        )
        payload = json.dumps(
            {"record": record.to_dict(), "measured_state_digest": record.measured_state_digest},
            indent=2,
            sort_keys=True,
        )
        if args.out:
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        return 0

    payload = json.loads(Path(args.record).read_text(encoding="utf-8"))
    record = payload.get("record", payload)
    verdict = verify_execution_state(
        record,
        expected_instance_id=args.instance_id,
        expected_repository=args.repository,
        expected_base_commit=args.base_commit,
        expected_side=args.side,
        expected_environment_identity=args.environment_identity,
    )
    print(json.dumps(verdict.to_dict(), indent=2, sort_keys=True))
    return 0 if verdict.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

