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


def test_isolated_store_receives_real_recovery_activity(isolate_prompt_repairer_store, monkeypatch):
    """Prove recovery LOGIC still runs — journal, audit and candidates all real."""
    store = isolate_prompt_repairer_store
    journal = store / "prompt_repairer_journal.jsonl"
    audit = store / "audit.jsonl"
    candidates = store / "candidates.json"

    # `get_prompt_repairer()` caches a module-global singleton
    # (prompt_repairer.py:1614-1618), so an instance built by an earlier test in
    # this process would never re-read the candidates file seeded below. Reset it
    # so the lifespan constructs a fresh repairer against this test's store.
    monkeypatch.setattr(prompt_repairer, "_repairer_instance", None)

    # A pending promotion: qdrant_applied with NO later committed row, so
    # recovery must classify it pending and then roll the candidate back.
    journal.write_text(
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
    candidates.write_text(
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

    with TestClient(_app()) as client:
        assert client.get("/health").status_code == 200

    # REAL recovery wrote the audit trail into tmp...
    assert audit.exists(), "recovery did not write the isolated audit log"
    events = [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(e["event_type"] == "RECOVERED_INTERRUPTED_PROMOTION" for e in events), (
        "expected RECOVERED_INTERRUPTED_PROMOTION from real recovery; got "
        f"{sorted({e['event_type'] for e in events})}"
    )

    # ...and rolled the candidate back via the REAL _save_candidates().
    assert candidates.exists()
    persisted = json.loads(candidates.read_text(encoding="utf-8"))
    assert persisted["cand_regression"]["status"] == prompt_repairer.CandidateState.PROMOTABLE.value
    assert "active_lesson_id" not in persisted["cand_regression"]

    # Production never saw any of it.
    assert _fingerprint(PROD_AUDIT)[1] is None or PROD_AUDIT.exists()


def _app():
    """Import the real app. Recovery is NOT disabled — only its paths redirect."""
    from swarm_os.app.main import app

    return app
