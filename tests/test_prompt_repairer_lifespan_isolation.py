"""RED-1/RED-2 regression: application lifespan must not touch PRODUCTION
PromptRepairer state.

`docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md` proved that
`swarm_os/app/main.py:126-129` runs `recover_interrupted_promotions()` in the
lifespan, so any `TestClient(app)` construction rewrote the production candidate
store and appended ~142 `RECOVERED_INTERRUPTED_PROMOTION` records to the
production audit log — with no test involved at all.

These tests pin BOTH halves of the fix:

* production `data/prompt_repairer_*.{json,jsonl}` are byte-identical across a
  real lifespan startup, and
* the isolated temporary store actually RECEIVES the recovery activity, proving
  recovery ran for real rather than being disabled.
"""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from swarm_os.services import prompt_repairer

REPO = Path(__file__).resolve().parent.parent
PROD_DATA = REPO / "data"
PROD_JOURNAL = PROD_DATA / "prompt_repairer_journal.jsonl"
PROD_AUDIT = PROD_DATA / "prompt_repairer_audit.jsonl"
PROD_CANDIDATES = PROD_DATA / "prompt_repairer_candidates.json"


def _fingerprint(path: Path):
    """Return (exists, size, sha256) or None-ish tuple when absent."""
    if not path.exists():
        return (False, None, None)
    return (True, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())


def _prod_fingerprint():
    return tuple(_fingerprint(p) for p in (PROD_JOURNAL, PROD_AUDIT, PROD_CANDIDATES))


def test_lifespan_startup_does_not_mutate_production_prompt_repairer_state(
    isolate_prompt_repairer_store, monkeypatch
):
    """RED-1/RED-2: real lifespan startup must leave production stores untouched."""
    monkeypatch.setattr(prompt_repairer, "_repairer_instance", None)
    before = _prod_fingerprint()

    with TestClient(_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200

    after = _prod_fingerprint()
    assert before == after, (
        "application lifespan mutated PRODUCTION PromptRepairer state: "
        f"before={before} after={after}"
    )


def test_lifespan_startup_writes_only_into_the_isolated_store(
    isolate_prompt_repairer_store, monkeypatch
):
    """The isolation must be a redirect, not a disable: real writes still happen."""
    monkeypatch.setattr(prompt_repairer, "_repairer_instance", None)
    store = isolate_prompt_repairer_store
    assert store.is_dir()

    with TestClient(_app()) as client:
        assert client.get("/health").status_code == 200

    import swarm_os.services.prompt_repairer as pr

    # Every PromptRepairer destination now resolves inside tmp_path.
    assert pr._journal_file().parent == store
    assert pr._CANDIDATES_FILE.parent == store
    assert pr._SNAPSHOTS_FILE.parent == store
    assert pr._AUDIT_LOG_FILE.parent == store
    assert not (PROD_DATA / "prompt_repairer_candidates.json").is_relative_to(store)

    # The lifespan really constructed a repairer and ran recovery against the
    # temporary store; nothing was written to production.
    for prod in (PROD_JOURNAL, PROD_AUDIT, PROD_CANDIDATES):
        assert prod.parent == PROD_DATA


def _seed_pending_promotion(store: Path) -> None:
    """Seed a pending promotion: `qdrant_applied` with NO later terminal row, so
    recovery must classify it pending and then roll the candidate back."""
    (store / "prompt_repairer_journal.jsonl").write_text(
        json.dumps(
            {
                "phase": "qdrant_applied",
                "candidate_id": "cand_regression",
                "lesson_id": "lesson_regression",
                "timestamp": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (store / "candidates.json").write_text(
        json.dumps(
            {
                "cand_regression": {
                    "id": "cand_regression",
                    "status": prompt_repairer.CandidateState.ACTIVE.value,
                    "active_lesson_id": "lesson_regression",
                }
            }
        ),
        encoding="utf-8",
    )


def _patch_lesson_store(monkeypatch, get_all):
    """Control the lifespan repairer's lesson store.

    `PromptRepairer.__init__` does `lesson_manager or get_lesson_manager()`
    (prompt_repairer.py:901) using the name bound at import (`:38`), so the
    root conftest mock on `lesson_manager.get_lesson_manager` does NOT reach it.
    Without this, whether recovery can verify provenance depends on whether
    Qdrant happens to be running — a real environment dependency in a unit test.
    """
    manager = MagicMock()
    manager.get_all = get_all
    manager.remove = AsyncMock(return_value=True)
    monkeypatch.setattr(prompt_repairer, "get_lesson_manager", lambda: manager)
    return manager


def _audit_events(store: Path):
    audit = store / "audit.jsonl"
    return [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_isolated_store_receives_real_recovery_activity(
    isolate_prompt_repairer_store, monkeypatch
):
    """Prove recovery LOGIC still runs — journal, audit and candidates all real.

    The lesson store answers and does not contain the dangling lesson, so the
    outcome is *verified*: recovery is genuinely complete and may say so.
    """
    store = isolate_prompt_repairer_store
    audit = store / "audit.jsonl"
    candidates = store / "candidates.json"

    # `get_prompt_repairer()` caches a module-global singleton
    # (prompt_repairer.py:1701-1707), so an instance built by an earlier test in
    # this process would never re-read the candidates file seeded below. Reset it
    # so the lifespan constructs a fresh repairer against this test's store.
    monkeypatch.setattr(prompt_repairer, "_repairer_instance", None)
    _patch_lesson_store(monkeypatch, AsyncMock(return_value=[]))

    _seed_pending_promotion(store)

    with TestClient(_app()) as client:
        assert client.get("/health").status_code == 200

    # REAL recovery wrote the audit trail into tmp...
    assert audit.exists(), "recovery did not write the isolated audit log"
    events = _audit_events(store)
    recovered = [e for e in events if e["event_type"] == "RECOVERED_INTERRUPTED_PROMOTION"]
    assert recovered, (
        "expected RECOVERED_INTERRUPTED_PROMOTION from real recovery; got "
        f"{sorted({e['event_type'] for e in events})}"
    )
    # The record must say WHAT was verified, not merely that a row existed.
    assert recovered[0]["details"]["verified"] == "absent"

    # ...and rolled the candidate back via the REAL _save_candidates().
    assert candidates.exists()
    persisted = json.loads(candidates.read_text(encoding="utf-8"))
    assert persisted["cand_regression"]["status"] == prompt_repairer.CandidateState.PROMOTABLE.value
    assert "active_lesson_id" not in persisted["cand_regression"]

    # Production never saw any of it.
    assert _fingerprint(PROD_AUDIT)[1] is None or PROD_AUDIT.exists()


def test_lifespan_recovery_fails_closed_when_lesson_store_unavailable(
    isolate_prompt_repairer_store, monkeypatch
):
    """An unreachable lesson store must NOT be reported as a successful recovery.

    Before the repair, `except Exception: pass` swallowed the lookup failure and
    `RECOVERED_INTERRUPTED_PROMOTION` was emitted unconditionally — a success
    claim for a recovery that never happened. Now the attempt is recorded
    truthfully, nothing is deleted, no candidate state is discarded, and the
    transaction stays pending so a later startup can retry it.
    """
    store = isolate_prompt_repairer_store
    candidates = store / "candidates.json"
    journal = store / "prompt_repairer_journal.jsonl"

    monkeypatch.setattr(prompt_repairer, "_repairer_instance", None)
    _patch_lesson_store(
        monkeypatch,
        AsyncMock(side_effect=ConnectionError("qdrant unreachable")),
    )
    _seed_pending_promotion(store)
    journal_before = journal.read_text(encoding="utf-8")

    with TestClient(_app()) as client:
        assert client.get("/health").status_code == 200

    events = _audit_events(store)
    types = {e["event_type"] for e in events}
    assert "RECOVERY_UNVERIFIED" in types, (
        f"expected truthful RECOVERY_UNVERIFIED; got {sorted(types)}"
    )
    assert "RECOVERED_INTERRUPTED_PROMOTION" not in types, (
        "emitted a successful-recovery record for an unverified attempt"
    )

    # No terminal row: the transaction must remain retryable, not silently closed.
    assert journal.read_text(encoding="utf-8") == journal_before, (
        "an unverified recovery must leave the transaction pending"
    )

    # Candidate state untouched — dropping active_lesson_id on an unverified
    # guess would discard the only pointer to a lesson that may still exist.
    persisted = json.loads(candidates.read_text(encoding="utf-8"))
    assert persisted["cand_regression"]["status"] == prompt_repairer.CandidateState.ACTIVE.value
    assert persisted["cand_regression"]["active_lesson_id"] == "lesson_regression"


def test_lifespan_recovery_is_idempotent_across_restarts(
    isolate_prompt_repairer_store, monkeypatch
):
    """Replaying startup recovery over the same transaction cannot emit an
    unbounded sequence of successful recovery records.

    Each boot resolves the transaction and marks it terminal in the journal, so
    the second boot finds nothing pending and writes nothing at all.
    """
    store = isolate_prompt_repairer_store
    audit = store / "audit.jsonl"

    monkeypatch.setattr(prompt_repairer, "_repairer_instance", None)
    _patch_lesson_store(monkeypatch, AsyncMock(return_value=[]))
    _seed_pending_promotion(store)

    with TestClient(_app()) as client:
        assert client.get("/health").status_code == 200

    first = [e for e in _audit_events(store)
             if e["event_type"] == "RECOVERED_INTERRUPTED_PROMOTION"]
    assert len(first) == 1
    after_first = audit.stat().st_size

    # Two further boots must add nothing at all.
    for _ in range(2):
        monkeypatch.setattr(prompt_repairer, "_repairer_instance", None)
        with TestClient(_app()) as client:
            assert client.get("/health").status_code == 200

    assert len([e for e in _audit_events(store)
                if e["event_type"] == "RECOVERED_INTERRUPTED_PROMOTION"]) == 1, (
        "recovery re-emitted a successful recovery for an already-resolved "
        "transaction"
    )
    assert audit.stat().st_size == after_first, (
        "a repeat startup wrote new recovery records for a resolved transaction"
    )


def _app():
    """Import the real app. Recovery is NOT disabled — only its paths redirect."""
    from swarm_os.app.main import app

    return app
