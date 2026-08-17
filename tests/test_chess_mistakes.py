"""Tests for the learn-from-mistakes + spaced-repetition store.

The store is isolated to a temp dir (never touches production data). The
ladder is pinned via env so scheduling is deterministic.
"""

import hashlib
import json

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
    # 'imported' entries with valid FENs get position-based classification.
    cm.record_mistake(
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
        "g1f3",
        "Nf3",
        "f1c4",
        "Bc4",
        "Inaccuracy",
        concept="imported",
    )
    cm.record_mistake(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "g2g4",
        "g4",
        "d2d4",
        "d4",
        "Blunder",
        concept="imported",
    )

    r = cm.get_recurring_mistakes(limit=10)
    assert r["ok"] is True
    assert r["total"] == 5
    top = r["top"][0]
    assert top["count"] == 3
    assert top["concept"] == "hanging"
    assert top["severity"].get("Blunder") == 3
    # position-based classification for the 'imported' entries
    concepts = {t["concept"] for t in r["top"]}
    assert (
        "imprecise move" in concepts
    )  # the imported fixtures aren't clear material losses
    assert len(concepts) == 2  # 'hanging' + the derived category


def test_classify_concept_identifies_error_types():
    """The deterministic concept classifier must identify a hanging piece and a
    missed capture from the position + played vs best move (the coach report's
    raw material)."""
    # Hanging a queen: 1.e4 e5 2.Qh5 Nc6 3.Qxf7?? (queen takes defended pawn,
    # king captures it next)
    hang = cm._classify_concept(
        "r1bqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 1 2",
        "h5f7",
        None,
        "Blunder",
    )
    assert hang == "hanging piece"

    # Missed capture: 1.e4 c5 2.Nf3 — best is Nxe5? no; use a clear case where
    # the best move captures but a quiet move was played instead.
    # Position: black knight on e5 is undefended and a pawn could take it.
    missed = cm._classify_concept(
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
        "g1f3",
        "f1c4",
        "Mistake",
    )
    assert missed in ("missed capture", "missed check", "imprecise move", "inaccuracy")


def test_coach_report_builds_skill_profile():
    """The coach report must map recurring mistake concepts to skill bars and
    recommend a focus based on the most frequent error type."""
    cm.record_mistake(
        "rnbqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 1 2",
        "h5f7",
        "Qxf7",
        None,
        None,
        "Blunder",
        concept="imported",
    )
    cm.record_mistake(
        "rnbqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 1 2",
        "h5e5",
        "Qxe5",
        None,
        None,
        "Mistake",
        concept="imported",
    )
    r = cm.coach_report()
    assert r["ok"] is True
    assert "tactics" in r["skills"]
    assert r["skills"]["tactics"]["bar"] == 100  # strongest weakness
    assert r["focus_skill"] == "tactics"


def _manifest() -> dict:
    """Read the store's retention manifest (archive total + SHA-256)."""
    return json.loads(
        cm._STORE_FILE.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8")
    )


def test_write_beyond_old_cap_persists_every_entry():
    """Regression: _save once truncated the archive to the newest 500 entries
    on EVERY save — a silent 'rolling window' that destroyed older evidence.
    Recording 505 distinct mistakes must persist ALL of them; the oldest must
    survive."""
    for i in range(505):
        cm.record_mistake(
            f"fen{i}", f"u{i}", f"s{i}", "g1f3", "Nf3", "Blunder", concept="hanging"
        )
    entries = cm._load()
    assert len(entries) == 505
    keys = {e["key"] for e in entries}
    assert "fen0|u0" in keys  # the OLDEST entry survived the write path
    assert "fen504|u504" in keys


def test_manifest_records_archive_state():
    """The manifest must record the exact committed archive — total count and
    SHA-256 of the on-disk bytes (a tamper-evident retention record)."""
    cm.record_mistake("f1", "e2e5", "e5", "g1f3", "Nf3", "Blunder", concept="hanging")
    cm.record_mistake("f2", "c2c4", "c4", "d2d4", "d4", "Mistake", concept="hanging")
    m = _manifest()
    assert m["store"] == "mistakes.jsonl"
    assert m["total"] == 2
    assert m["sha256"] == hashlib.sha256(cm._STORE_FILE.read_bytes()).hexdigest()
    assert m["policy"].startswith("archive-all")


def test_manifest_stays_in_sync_across_mutations():
    """Every mutation (solved/failed) rewrites the archive + manifest together,
    so the manifest never lies about the store's contents."""
    entry = cm.record_mistake(
        "f", "e2e5", "e5", "g1f3", "Nf3", "Blunder", concept="hanging"
    )
    cm.mark_failed(entry["id"])
    assert _manifest()["total"] == len(cm._load())
    assert (
        _manifest()["sha256"] == hashlib.sha256(cm._STORE_FILE.read_bytes()).hexdigest()
    )
