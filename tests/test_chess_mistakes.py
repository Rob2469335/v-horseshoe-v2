"""Tests for the learn-from-mistakes + spaced-repetition store.

The store is isolated to a temp dir (never touches production data). The
ladder is pinned via env so scheduling is deterministic.
"""

import pytest

from swarm_os.services import chess_mistakes as cm


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cm, "_STORE_FILE", tmp_path / "chess" / "mistakes.jsonl")
    monkeypatch.setenv("CHESS_SR_LADDER", "1,3,7")
    monkeypatch.setattr(cm, "_ladder_days", lambda: [1, 3, 7])
    yield


def test_record_mistake_persists_and_dedupes():
    a = cm.record_mistake(
        "fen1", "e2e5", "e5", "g1f3", "Nf3", "Blunder", concept="hanging"
    )
    b = cm.record_mistake(
        "fen1", "e2e5", "e5", "g1f3", "Nf3", "Blunder", concept="hanging"
    )
    assert a["id"] == b["id"]  # same (fen, move) deduped
    entries = cm._load()
    assert len(entries) == 1
    assert entries[0]["box"] == 0
    assert entries[0]["pre_fen"] == "fen1"
    assert entries[0]["best_uci"] == "g1f3"


def test_review_due_returns_only_due():
    now = cm._now()
    due = cm.record_mistake("f1", "a2a4", "a4", "b2b4", "b4", "Mistake")
    due["due_at"] = now - 1  # make due
    not_due = cm.record_mistake("f2", "c2c4", "c4", "d2d4", "d4", "Mistake")
    not_due["due_at"] = now + 99999  # future
    cm._save([due, not_due])
    res = cm.review_due()
    assert res["ok"] is True
    assert res["due_count"] == 1
    assert res["due"][0]["id"] == due["id"]
    assert res["total"] == 2


def test_mark_solved_advances_box_and_retires():
    entry = cm.record_mistake("f", "e2e5", "e5", "g1f3", "Nf3", "Blunder")
    r1 = cm.mark_solved(entry["id"])
    assert r1["ok"] is True and r1["box"] == 1 and r1["retired"] is False
    r2 = cm.mark_solved(entry["id"])
    assert r2["box"] == 2
    r3 = cm.mark_solved(entry["id"])
    assert r3["box"] == 3 and r3["retired"] is True
    # Retired -> removed from the store.
    assert cm._load() == []
    assert cm.mark_solved(entry["id"])["ok"] is False


def test_mark_failed_resets_to_box_zero():
    entry = cm.record_mistake("f", "e2e5", "e5", "g1f3", "Nf3", "Blunder")
    cm.mark_solved(entry["id"])
    res = cm.mark_failed(entry["id"])
    assert res["ok"] is True and res["box"] == 0
    reloaded = cm._load()[0]
    assert reloaded["fails"] == 1
    assert reloaded["box"] == 0


def test_mark_unknown_entry_fails_closed():
    assert cm.mark_solved("nope")["ok"] is False
    assert cm.mark_failed("nope")["ok"] is False


def test_stats_boxes():
    cm.record_mistake("f1", "e2e5", "e5", "g1f3", "Nf3", "Blunder")
    e2 = cm.record_mistake("f2", "c2c4", "c4", "d2d4", "d4", "Mistake")
    cm.mark_solved(e2["id"])
    s = cm.stats()
    assert s["ok"] is True
    assert s["total"] == 2
    assert s["boxes"]["0"] == 1
    assert s["boxes"]["1"] == 1
    assert s["ladder_days"] == [1, 3, 7]


def test_unreadable_store_fails_closed(monkeypatch, tmp_path):
    bad = tmp_path / "chess" / "mistakes.jsonl"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json\n", encoding="utf-8")
    monkeypatch.setattr(cm, "_STORE_FILE", bad)
    res = cm.review_due()
    assert res["ok"] is True
    assert res["due"] == []
    assert res["total"] == 0


def test_api_review_endpoints(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        entry = cm.record_mistake("f", "e2e5", "e5", "g1f3", "Nf3", "Blunder")
        entry["due_at"] = cm._now() - 1
        cm._save(cm._load())
        r = c.get("/chess/trainer/review")
        assert r.status_code == 200
        assert len(r.json()["due"]) >= 1
        r = c.post("/chess/trainer/review/solved", json={"entry_id": entry["id"]})
        assert r.json()["ok"] is True
        r = c.post("/chess/trainer/review/failed", json={"entry_id": entry["id"]})
        assert r.json()["ok"] is True


def test_missed_sacrifice_records_as_review_item():
    # A missed sacrifice is queued with the best move as the answer — the
    # review asks the learner to FIND the sacrifice.
    entry = cm.record_mistake(
        pre_fen="f1",
        played_uci="d1h5",
        played_san="Qh5 (missed a sacrifice)",
        best_uci="c4h7",
        best_san="Bxh7+",
        classification="Missed sacrifice",
        concept="sacrifice",
        book_titles=["Winning Chess Tactics"],
    )
    assert entry["best_uci"] == "c4h7"
    assert entry["classification"] == "Missed sacrifice"
    assert "missed a sacrifice" in entry["played_san"]
    assert cm.review_due()["total"] == 1


def test_recurring_mistakes_aggregates_by_concept():
    """The 'top recurring mistakes' summary must group by concept (or a
    derived signature for generic 'imported' concepts) and rank by frequency."""
    cm.record_mistake("f1", "e2e4", "e4", "g1f3", "Nf3", "Blunder", concept="hanging")
    cm.record_mistake("f2", "d2d4", "d4", "c2c4", "c4", "Blunder", concept="hanging")
    cm.record_mistake("f3", "g2g4", "g4", "h2h4", "h4", "Blunder", concept="hanging")
    cm.record_mistake("f4", "c2c3", "c3", "d2d4", "d4", "Mistake", concept="imported")
    cm.record_mistake("f5", "b2b3", "b3", "c2c4", "c4", "Inaccuracy", concept="imported")
    cm.record_mistake("f6", "a2a3", "a3", "b2b4", "b4", "Blunder", concept="imported")

    r = cm.get_recurring_mistakes(limit=10)
    assert r["ok"] is True
    assert r["total"] == 6
    top = r["top"][0]
    assert top["count"] == 3
    assert top["concept"] == "hanging"
    assert top["severity"].get("Blunder") == 3
    # derived signature for 'imported' blunder/mistake/inaccuracy buckets
    concepts = {t["concept"] for t in r["top"]}
    assert "hanging piece / losing blunder" in concepts
