"""Crash-recovery + legal-transition tests for the durable repair orchestrator.

The killer property under test: a repair that crashes in ANY externally
interruptible phase can never be recovered into ACCEPTED. Recovery re-derives
the next action from the durable record, and only an explicit
EVIDENCE_CAPTURE transition (after validation + security both recorded
success) can ever reach ACCEPTED.
"""
from __future__ import annotations

import pytest

import organism_console.core.repair_state as rs
from organism_console.core.repair_state import (
    IllegalTransitionError,
    RepairRecord,
    create_record,
    load,
    recover,
    transition,
)


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "_REPAIR_STATE_DIR", tmp_path / "states")
    yield


def _make_record(tmp_path, phase: str, **kw) -> RepairRecord:
    rec = create_record(
        file_path=tmp_path / "bug.py",
        error_text="File not found: app.py",
        failure_type="import_resolution",
        baseline_revision="abc",
    )
    # Move through a legal path TO the desired phase (inclusive).
    chain = ["INSPECTING", "DIAGNOSING", "PATCHING", "VALIDATING",
             "REVALIDATING", "SECURITY_CHECK", "EVIDENCE_CAPTURE"]
    idx = chain.index(phase)
    for p in chain[: idx + 1]:
        rec = transition(rec, p)
    return rec


def test_legal_positive_path_reaches_accepted(tmp_path):
    rec = create_record(tmp_path / "bug.py", "err", "import_resolution", "abc")
    for p in ("INSPECTING", "DIAGNOSING", "PATCHING", "VALIDATING",
              "SECURITY_CHECK", "EVIDENCE_CAPTURE"):
        rec = transition(rec, p)
    rec.validation_result = {"initial_result": "fail", "retry_result": "pass",
                             "flaky": True, "outcome": "accepted"}
    rec.security_result = {"ok": True, "reason": "clean"}
    rec = transition(rec, "ACCEPTED")
    assert rec.phase == "ACCEPTED"
    # Durable: reload gives the same terminal state.
    reloaded = load(rec.repair_id)
    assert reloaded.phase == "ACCEPTED"


def test_patching_to_accepted_is_illegal(tmp_path):
    """The report's headline invariant: PATCHING -> ACCEPTED must be impossible.
    It must go through validation and security evidence."""
    rec = _make_record(tmp_path, "PATCHING")
    with pytest.raises(IllegalTransitionError):
        transition(rec, "ACCEPTED")


def test_security_check_to_accepted_requires_evidence(tmp_path):
    """SECURITY_CHECK -> ACCEPTED is not a legal transition — it must go through
    EVIDENCE_CAPTURE first."""
    rec = _make_record(tmp_path, "SECURITY_CHECK")
    with pytest.raises(IllegalTransitionError):
        transition(rec, "ACCEPTED")


def test_crash_during_patching_recovers_to_failed_never_accepted(tmp_path):
    """Kill during PATCHING (candidate may be on disk, never accepted)."""
    rec = _make_record(tmp_path, "PATCHING")
    rec.candidate_revision = "candidate-hash"
    rs.persist(rec)
    recovered = recover(rec.repair_id)
    assert recovered.phase == "REPAIR_FAILED"
    assert recovered.next_action == "revert"  # candidate touched disk
    assert recovered.phase != "ACCEPTED"


def test_crash_during_validation_recovers_to_failed(tmp_path):
    rec = _make_record(tmp_path, "VALIDATING")
    rs.persist(rec)
    recovered = recover(rec.repair_id)
    assert recovered.phase == "REPAIR_FAILED"
    assert recovered.next_action == "revert"


def test_crash_during_security_check_recovers_to_failed(tmp_path):
    rec = _make_record(tmp_path, "SECURITY_CHECK")
    rec.security_result = {"ok": True, "reason": "clean"}
    rs.persist(rec)
    recovered = recover(rec.repair_id)
    # Even with a PASSED security result, a crash before EVIDENCE_CAPTURE means
    # the repair was never accepted.
    assert recovered.phase == "REPAIR_FAILED"
    assert recovered.next_action == "revert"


def test_crash_during_diagnosing_recovers_abort_nothing_patched(tmp_path):
    """Crash before any patch touched disk -> abort, no revert needed."""
    rec = _make_record(tmp_path, "DIAGNOSING")
    rs.persist(rec)
    recovered = recover(rec.repair_id)
    assert recovered.phase == "REPAIR_FAILED"
    assert recovered.next_action == "abort"


def test_accepted_survives_recovery_unchanged(tmp_path):
    """A properly ACCEPTED repair (evidence recorded) is terminal — recovery
    must NOT touch it."""
    rec = _make_record(tmp_path, "EVIDENCE_CAPTURE")
    rec.validation_result = {"outcome": "accepted"}
    rec.security_result = {"ok": True}
    rec = transition(rec, "ACCEPTED")
    recovered = recover(rec.repair_id)
    assert recovered.phase == "ACCEPTED"
    assert recovered.next_action == "none"


def test_repair_failed_is_terminal(tmp_path):
    rec = _make_record(tmp_path, "PATCHING")
    rs.persist(rec)
    rec = recover(rec.repair_id)
    again = recover(rec.repair_id)
    assert again.phase == "REPAIR_FAILED"
    assert again.next_action == "none"


def test_recover_missing_record_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        recover("does-not-exist")


def test_every_phase_has_table_entry():
    used = set(rs.LEGAL_TRANSITIONS.keys())
    declared = used | {t for ts in rs.LEGAL_TRANSITIONS.values() for t in ts}
    assert "CREATED" in used and "ACCEPTED" in used and "REPAIR_FAILED" in used
    # No dangling targets.
    assert declared <= used, f"dangling transition targets: {declared - used}"
