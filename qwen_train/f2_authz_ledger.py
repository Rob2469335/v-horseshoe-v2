"""Experiment J / F2 - durable single-use authorization consumption ledger.

CONTRACT: one signed grant (one authorization record) permits exactly ONE
authorized execution.  The unit of single-use is the GRANT itself - the
canonical record digest - NOT the (record, binding) pair.  A consumed grant is
refused for any further execution: replay with the identical binding AND
substitution of any binding value (rollout id, arm, experiment, ...) are both
denied, from any process and across restarts, for as long as the consumption
entry exists (see the threat-model boundary below for what that does NOT
cover).  The consumed entry records the full execution binding so the child
worker and the lowest adapter boundary must present the SAME binding to
corroborate the receipt; a different binding cannot be corroborated, and a
fresh consumption of the same grant is denied at the key level.

Mechanism: one JSON file per grant under a ledger directory, created with
``os.open(..., O_CREAT | O_EXCL | O_WRONLY)`` - an atomic create-if-absent at
the filesystem layer on POSIX and Windows (CreateFile CREATE_NEW).  Exactly one
concurrent creator can win a given grant key; every other attempt observes
``FileExistsError`` and is denied.  There is deliberately **no** update,
truncate, or delete method: a consumed grant never returns to an unused state
through this API, and a crash after the create leaves the consumption durable
(the fail-closed direction).  A crash BETWEEN the create and the write leaves
an unparseable entry, which every later reader treats as corrupt => fail
closed, never as unused.

THREAT-MODEL BOUNDARY (read before relying on this module):

* This ledger gives SOFTWARE-ENFORCED replay resistance and auditability
  against accidental double-execution, replayed worker invocations, and a
  principal that cannot write the ledger directory.
* It is NOT independent tamper resistance.  A hostile local administrator or a
  compromised execution principal that can write the ledger directory can
  delete or roll back consumption files; a consumed grant then becomes
  reusable again.  No claim of tamper-evident or tamper-proof storage is made;
  establishing that would require an anchor whose writer is independent of the
  executing principal (owner-held custody), which does not exist here.
* Ledger corruption, unparseable content, or an unavailable/unwritable store
  fail CLOSED: protected execution is denied rather than proceeding without a
  verifiable consumption.

Test isolation: tests MUST inject a temporary directory - either
``AuthorizationLedger(root=...)`` / ``verify_and_consume(ledger_root=...)`` or
by monkeypatching ``DEFAULT_LEDGER_ROOT`` (see ``tests/f2_authz_testkit.py::
use_test_ledger``). The production default is a code constant under the
gitignored ``data/`` tree and must never be written by a test run; it is not
selectable by any environment variable.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from qwen_train.f2_authorization import AuthorizationError, canonical_bytes

SCHEMA = "f2-authz-consumption/v1"

#: Production ledger location: a CODE CONSTANT under the gitignored ``data/``
#: tree. It is deliberately NOT environment-selectable: a process that can
#: set ``F2_AUTHZ_LEDGER_DIR``-style variables could otherwise redirect a
#: protected operation to an empty ledger and make a consumed grant appear
#: unused. Tests use the constructor/parameter injection
#: (``AuthorizationLedger(root=...)`` / ``verify_and_consume(ledger_root=...)``)
#: or monkeypatch this constant - never a production environment variable.
#: A checkout has no ledger until a real (owner-authorized) consumption
#: happens.
DEFAULT_LEDGER_ROOT = Path(__file__).resolve().parents[1] / "data" / "f2_authz_ledger"


class LedgerError(AuthorizationError):
    """Durable consumption state is unavailable, corrupt, or unusable."""


def ledger_root(explicit: Path | str | None = None) -> Path:
    """Resolve the ledger directory. ``explicit`` is dependency injection
    (constructor/tests); the production default is the code constant above,
    never an environment variable."""
    if explicit is not None:
        return Path(explicit)
    return Path(DEFAULT_LEDGER_ROOT)


def consumption_key(record_digest: str) -> str:
    """Grant-level single-use key: the canonical record digest itself.

    Keying on the grant (not the grant+binding pair) is what enforces
    "one grant, one execution": any second consumption of the same record -
    identical or substituted binding - collides on this key and is denied.
    """
    if not record_digest or not isinstance(record_digest, str):
        raise LedgerError("record_digest is required")
    return record_digest


def execution_binding_digest(binding: Mapping[str, Any]) -> str:
    """Canonical digest of a normalized execution binding.

    Unknown keys and non-string values are rejected so a binding can never be
    smuggled through with an ambiguous or misspelled field name.
    """
    from qwen_train.f2_authorization import BINDING_FIELDS, BINDING_WITH_STAGE

    unknown = sorted(set(binding) - set(BINDING_WITH_STAGE))
    if unknown:
        raise AuthorizationError(f"unknown execution-binding fields: {unknown!r}")
    for name in BINDING_FIELDS:
        if name in binding and not isinstance(binding[name], str):
            raise AuthorizationError(f"execution-binding field {name!r} must be a string")
    for name in ("stage", "operation"):
        if not isinstance(binding.get(name), str) or not binding.get(name):
            raise AuthorizationError(f"execution binding requires {name!r}")
    return hashlib.sha256(canonical_bytes(dict(binding))).hexdigest()


class AuthorizationLedger:
    """Durable, concurrency-safe, single-use consumption store (append-only)."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = ledger_root(root)

    # ---- write --------------------------------------------------------- #
    def consume(
        self,
        *,
        record_digest: str,
        binding: Mapping[str, Any],
        binding_digest: str,
        stage: str,
        operation: str,
    ) -> dict[str, Any]:
        """Atomically consume ONE grant. Exactly one caller can ever succeed.

        Raises ``AuthorizationError`` when the grant was already consumed (for
        any binding - same or different), and ``LedgerError`` when the store is
        unavailable or corrupt. A partial write by a crashed creator leaves an
        unparseable file, which every later reader treats as corrupt => fail
        closed (never as unused).
        """
        key = consumption_key(record_digest)
        entry_path = self.root / f"{key}.json"
        entry: dict[str, Any] = {
            "schema": SCHEMA,
            "consumption_id": hashlib.sha256(
                f"{key}|{_dt.datetime.now(_dt.timezone.utc).isoformat()}|{os.getpid()}".encode(
                    "utf-8"
                )
            ).hexdigest(),
            "record_digest": record_digest,
            "binding_digest": binding_digest,
            "binding": dict(binding),
            "stage": stage,
            "operation": operation,
            "consumed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "consumed_by_pid": os.getpid(),
        }
        payload = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(entry_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Distinguish "valid prior consumption" from "corrupt leftover by a
            # crashed creator". Both deny, but a corrupt entry fails closed as
            # corruption - it must never read as "unused".
            try:
                prior = json.loads(entry_path.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise LedgerError(
                    f"authorization ledger entry corrupt ({entry_path.name!r}, "
                    f"possibly a crashed creator): {exc} - fail-closed"
                ) from exc
            if not isinstance(prior, dict) or prior.get("schema") != SCHEMA:
                raise LedgerError(
                    f"authorization ledger entry invalid schema "
                    f"({entry_path.name!r}) - fail-closed"
                )
            if prior.get("binding_digest") == binding_digest:
                raise AuthorizationError(
                    f"grant already consumed for this execution binding "
                    f"(record {key[:12]}...); replay denied"
                ) from None
            raise AuthorizationError(
                f"grant already consumed; one grant authorizes exactly one "
                f"execution (record {key[:12]}... consumed with a DIFFERENT "
                f"binding - binding substitution denied)"
            ) from None
        except OSError as exc:
            raise LedgerError(
                f"authorization consumption ledger unavailable at {self.root} ({exc})"
            ) from exc
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        return entry

    # ---- read ---------------------------------------------------------- #
    def _entries(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        if not self.root.is_dir():
            raise LedgerError(f"authorization ledger path is not a directory: {self.root}")
        out: list[dict[str, Any]] = []
        try:
            children = sorted(self.root.iterdir())
        except OSError as exc:
            raise LedgerError(f"authorization ledger unreadable: {self.root} ({exc})") from exc
        for child in children:
            if child.suffix != ".json":
                raise LedgerError(f"unexpected file in authorization ledger: {child.name!r}")
            try:
                doc = json.loads(child.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise LedgerError(
                    f"authorization ledger entry corrupt ({child.name!r}): {exc}"
                ) from exc
            if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
                raise LedgerError(f"authorization ledger entry invalid schema: {child.name!r}")
            out.append(doc)
        return out

    def find(self, *, record_digest: str) -> dict[str, Any] | None:
        """Return the consumption entry for this grant, if any (grant-level key)."""
        key = consumption_key(record_digest)
        path = self.root / f"{key}.json"
        if not path.exists():
            return None
        entries = self._entries()
        for doc in entries:
            if doc.get("record_digest") == record_digest:
                # Integrity: the filename key must match the content digest.
                if consumption_key(str(doc.get("record_digest"))) != key:
                    raise LedgerError(
                        f"authorization ledger entry key mismatch: {path.name!r}"
                    )
                return doc
        return None

    def verify_consumption(
        self, *, consumption_id: str, record_digest: str, binding_digest: str
    ) -> dict[str, Any]:
        """Confirm an existing consumption matches all three identities. Fail-closed."""
        if not consumption_id:
            raise AuthorizationError("missing authorization consumption id (fail-closed)")
        entries = self._entries()
        match = None
        for doc in entries:
            if doc.get("consumption_id") == consumption_id:
                if match is not None:
                    raise LedgerError("duplicate authorization consumption id in ledger")
                match = doc
        if match is None:
            raise AuthorizationError(
                "no durable authorization consumption found for this execution "
                "(fail-closed: replay/uncorroborated execution denied)"
            )
        if match.get("record_digest") != record_digest:
            raise AuthorizationError(
                "authorization consumption record digest mismatch (fail-closed)"
            )
        if match.get("binding_digest") != binding_digest:
            raise AuthorizationError(
                "authorization consumption binding digest mismatch (fail-closed)"
            )
        return match
