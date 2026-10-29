"""Registration file digest binding (DEFECT C).

The operational launcher passes ``--registration-sha256`` a constant that is the
CANONICAL CONTENT digest of the registration payload
(``registration_content_sha256`` in the sibling provenance record).  The precheck
compared that declared label against ``--expected-registration-sha256`` -- another
declared label -- so the comparison could only ever prove that the caller passed
the same constant twice.  It could not detect a substituted or regenerated
registration file, which is the actual trust question.

These tests pin the replacement contract: the registration file's RAW BYTES are
bound to ``tasks_sha256`` from the sibling provenance record (the digest that
describes those bytes), an operator-supplied expectation overrides it, and a
mismatch or a malformed provenance record fails closed.

They are pure-function tests over temporary files: no build, no collection, no
repository-controlled code is executed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train.f2_s8_precheck import (
    PrecheckError,
    _check_registration_file_digest,
    _declared_registration_file_digest,
)


REGISTRATION_BYTES = b'[{"instance_id": "acme__widget-1"}]\n'
REGISTRATION_DIGEST = hashlib.sha256(REGISTRATION_BYTES).hexdigest()


def _write_registration(tmp_path: Path, payload: bytes = REGISTRATION_BYTES) -> Path:
    registration = tmp_path / "registered_tasks.json"
    registration.write_bytes(payload)
    return registration


def _write_provenance(tmp_path: Path, record: object) -> Path:
    provenance = tmp_path / "registered_tasks.provenance.json"
    if isinstance(record, bytes):
        provenance.write_bytes(record)
    else:
        provenance.write_text(json.dumps(record), encoding="utf-8")
    return provenance


# ---------------------------------------------------------------------------
# _declared_registration_file_digest
# ---------------------------------------------------------------------------


def test_absent_provenance_yields_no_expectation(tmp_path: Path) -> None:
    """No sibling record means the binding is unenforceable, not satisfied."""
    registration = _write_registration(tmp_path)

    assert _declared_registration_file_digest(registration) is None


def test_provenance_supplies_the_raw_bytes_digest(tmp_path: Path) -> None:
    registration = _write_registration(tmp_path)
    _write_provenance(
        tmp_path,
        {
            "tasks_sha256": REGISTRATION_DIGEST,
            # The content digest is a DIFFERENT domain and must not be used.
            "registration_content_sha256": "0" * 64,
        },
    )

    assert _declared_registration_file_digest(registration) == REGISTRATION_DIGEST


def test_unreadable_provenance_fails_closed(tmp_path: Path) -> None:
    registration = _write_registration(tmp_path)
    _write_provenance(tmp_path, b"{not json")

    with pytest.raises(PrecheckError, match="unreadable"):
        _declared_registration_file_digest(registration)


def test_provenance_that_is_not_an_object_fails_closed(tmp_path: Path) -> None:
    registration = _write_registration(tmp_path)
    _write_provenance(tmp_path, [{"tasks_sha256": REGISTRATION_DIGEST}])

    with pytest.raises(PrecheckError, match="not a JSON object"):
        _declared_registration_file_digest(registration)


@pytest.mark.parametrize("declared", [None, "", 12345])
def test_provenance_without_a_usable_digest_fails_closed(
    tmp_path: Path, declared: object
) -> None:
    registration = _write_registration(tmp_path)
    record = {} if declared is None else {"tasks_sha256": declared}
    _write_provenance(tmp_path, record)

    with pytest.raises(PrecheckError, match="declares no tasks_sha256"):
        _declared_registration_file_digest(registration)


# ---------------------------------------------------------------------------
# _check_registration_file_digest
# ---------------------------------------------------------------------------


def test_matching_bytes_are_bound_to_the_declared_digest(tmp_path: Path) -> None:
    registration = _write_registration(tmp_path)
    _write_provenance(tmp_path, {"tasks_sha256": REGISTRATION_DIGEST})

    assert (
        _check_registration_file_digest(registration, REGISTRATION_DIGEST, None)
        == REGISTRATION_DIGEST
    )


def test_substituted_registration_file_is_rejected(tmp_path: Path) -> None:
    """The whole point: different bytes, same declared content label -> fail."""
    substituted_bytes = b'[{"instance_id": "evil__x-9"}]\n'
    registration = _write_registration(tmp_path, substituted_bytes)
    _write_provenance(tmp_path, {"tasks_sha256": REGISTRATION_DIGEST})

    with pytest.raises(PrecheckError, match="does not match its declared provenance"):
        _check_registration_file_digest(
            registration, hashlib.sha256(substituted_bytes).hexdigest(), None
        )


def test_operator_expectation_overrides_the_provenance_record(
    tmp_path: Path,
) -> None:
    """An explicit expectation is authoritative over the on-disk record."""
    registration = _write_registration(tmp_path)
    _write_provenance(tmp_path, {"tasks_sha256": "f" * 64})

    assert (
        _check_registration_file_digest(registration, REGISTRATION_DIGEST, REGISTRATION_DIGEST)
        == REGISTRATION_DIGEST
    )


def test_operator_expectation_mismatch_fails_closed(tmp_path: Path) -> None:
    registration = _write_registration(tmp_path)

    with pytest.raises(PrecheckError, match="does not match its declared provenance"):
        _check_registration_file_digest(registration, REGISTRATION_DIGEST, "f" * 64)


def test_no_expectation_anywhere_records_absence_without_failing(
    tmp_path: Path,
) -> None:
    """Absence is recorded, never silently treated as a match."""
    registration = _write_registration(tmp_path)

    assert _check_registration_file_digest(registration, REGISTRATION_DIGEST, None) is None
