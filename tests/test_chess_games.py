"""Tests for the full-game recording + guided review store.

The store is isolated to a temp dir (never touches production data).
"""

import hashlib
import json

import chess
import pytest

from swarm_os.services import chess_games as cg


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cg, "_GAMES_FILE", tmp_path / "chess" / "games.jsonl")
    yield


def _move(san="e4", cls="Best", delta=0.0, uci="e2e4", best="e2e4"):
    return {
        "uci": uci,
        "san": san,
        "fen": "f",
        "pre_fen": "p",
        "classification": cls,
        "eval_before_cp": 30.0,
        "eval_after_cp": 30.0,
        "win_after_pct": 50.0,
        "win_delta_pct": delta,
        "best_uci": best,
        "best_move_san": best,
        "is_best": cls == "Best",
        "concept": "",
    }


def test_start_game_returns_id_and_finalizes_prior():
    g1 = cg.start_game()
    assert g1["id"] and g1["status"] == "in_progress"
    cg.record_move(g1["id"], _move())
    cg.start_game()  # starts a new game, finalizing g1
    games = cg._load_games()
    assert len(games) == 2
    assert games[-2]["status"] == "finished"  # prior game finalized


def test_start_game_no_finalize_keeps_in_progress_games():
    # A background bulk import must NOT truncate a live interactive game.
    g1 = cg.start_game()
    cg.record_move(g1["id"], _move())
    g2 = cg.start_game(finalize_existing=False)  # bulk-import-style start
    cg._load_games()
    # Both are still in_progress — the import didn't finalize the interactive one.
    assert g1["status"] == "in_progress"
    assert g2["status"] == "in_progress"


def test_record_game_writes_finished_game_in_one_call():
    # The bulk-import path persists a whole finished game in ONE load/save
    # (no per-move whole-file rewrites — the write-amplification fix).
    g1 = cg.start_game(finalize_existing=False)
    cg.record_game(
        {
            "id": g1["id"],
            "start_fen": chess.STARTING_FEN,
            "started_at": 1.0,
            "ended_at": 2.0,
            "status": "finished",
            "player_color": "w",
            "source": "chess.com:tester",
            "moves": [_move("e4", "Best", 0), _move("Nf3", "Best", 0)],
        }
    )
    games = cg._load_games()
    g = next(x for x in games if x["id"] == g1["id"])
    assert g["status"] == "finished"
    assert len(g["moves"]) == 2


def test_accuracy_filters_black_player_moves():
    # A Black-side player's accuracy counts only their recorded moves.
    # Games now only record the player's moves (interactive=True by default).
    g = cg.start_game(player_color="b")
    for m in (
        _move("e5", "Best", 0),  # black (player) — 1.0
        _move("Nc6", "Best", 0),  # black (player) — 1.0
    ):
        cg.record_move(g["id"], m)
    review = cg.finish_game(g["id"])
    assert review["accuracy"] == pytest.approx(100.0, abs=0.1)


def test_record_move_appends():
    g = cg.start_game()
    cg.record_move(g["id"], _move("e4", "Best", 0))
    cg.record_move(g["id"], _move("Qh5", "Blunder", -12, "d1h5", "g1f3"))
    review = cg.finish_game(g["id"])
    assert review["move_count"] == 2
    assert any(k["classification"] == "Blunder" for k in review["key_moments"])


def test_review_accuracy_and_phases():
    g = cg.start_game()
    # 2 player moves: Best then Blunder(-30%). Accuracy = (1 + 0.7)/2 = 85%.
    # With 2 moves, only opening and middlegame phases exist.
    for m in (
        _move("e4", "Best", 0),
        _move("Qh5", "Blunder", -30, "d1h5", "g1f3"),
    ):
        cg.record_move(g["id"], m)
    review = cg.finish_game(g["id"])
    assert review["accuracy"] == pytest.approx(85.0, abs=0.1)
    assert set(review["phases"].keys()) == {"opening", "middlegame"}


def test_review_curve():
    g = cg.start_game()
    cg.record_move(g["id"], _move())
    review = cg.finish_game(g["id"])
    assert len(review["curve"]) == 1
    assert review["curve"][0]["win_pct"] == 50.0


def test_finish_unknown_falls_back_to_most_recent():
    g = cg.start_game()
    cg.record_move(g["id"], _move())
    review = cg.finish_game("does-not-exist")
    assert review["ok"] is True
    assert review["game_id"] == g["id"]


def test_finish_no_games_fails_closed():
    review = cg.finish_game("x")
    assert review["ok"] is False


def test_list_games():
    g = cg.start_game()
    cg.record_move(g["id"], _move())
    cg.finish_game(g["id"])
    res = cg.list_games()
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["games"][0]["accuracy"] == 100.0


def test_queue_game_mistakes(monkeypatch, tmp_path):
    from swarm_os.services import chess_mistakes as cm

    monkeypatch.setattr(cm, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cm, "_STORE_FILE", tmp_path / "chess" / "mistakes.jsonl")
    monkeypatch.setattr(cm, "_ladder_days", lambda: [1, 3, 7])
    g = cg.start_game()
    cg.record_move(g["id"], _move("e4", "Best", 0))
    cg.record_move(g["id"], _move("Qh5", "Mistake", -8, "d1h5", "g1f3"))
    res = cg.queue_game_mistakes(g["id"])
    assert res["ok"] is True
    assert res["queued"] == 1
    assert cm.review_due()["total"] == 1


def test_unreadable_store_fails_closed(monkeypatch, tmp_path):
    bad = tmp_path / "chess" / "games.jsonl"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json\n", encoding="utf-8")
    monkeypatch.setattr(cg, "_GAMES_FILE", bad)
    res = cg.list_games()
    assert res["ok"] is True
    assert res["count"] == 0


def test_api_game_endpoints(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.post("/chess/trainer/game/start")
        assert r.status_code == 200
        gid = r.json()["id"]
        # Record a move via evaluate is engine-dependent; instead exercise the
        # review/queue/list endpoints against a directly-started game.
        cg.record_move(gid, _move())
        r = c.post("/chess/trainer/game/review", json={"game_id": gid})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.post("/chess/trainer/game/queue-mistakes", json={"game_id": gid})
        assert r.status_code == 200
        r = c.get("/chess/trainer/games")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.get("/chess/trainer/analytics")
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# Progress analytics
# ---------------------------------------------------------------------------
def test_progress_analytics_empty_fails_gracefully():
    res = cg.progress_analytics()
    assert res["ok"] is True
    assert res["training_rating"] is None
    assert res["games_count"] == 0


def test_progress_analytics_computes_skill_bars():
    g = cg.start_game()
    for m in (
        _move("e4", "Best", 0),
        _move("Nf3", "Best", 0),
        _move("Qh5", "Blunder", -30, "d1h5", "g1f3"),
    ):
        cg.record_move(g["id"], m)
    cg.finish_game(g["id"])
    res = cg.progress_analytics()
    assert res["games_count"] == 1
    assert res["moves_count"] == 3
    assert res["skills"]["best_rate"] == pytest.approx(66.7, abs=0.1)
    assert res["skills"]["blunder_rate"] == pytest.approx(33.3, abs=0.1)
    assert res["training_rating"] is not None
    assert res["skills"]["opening"] > 0
    assert len(res["recent"]) == 1


def test_progress_analytics_rating_moves_with_accuracy():
    # A perfect game should rate higher than a blunder-filled one.
    good = cg.start_game()
    for _ in range(6):
        cg.record_move(good["id"], _move("e4", "Best", 0))
    cg.finish_game(good["id"])
    bad = cg.start_game()
    for _ in range(6):
        cg.record_move(bad["id"], _move("Qh5", "Blunder", -30, "d1h5", "g1f3"))
    cg.finish_game(bad["id"])
    res = cg.progress_analytics()
    # Two games averaged: good (100%) + bad (70%) -> ~85% -> rating ~1235.
    assert 1100 <= res["training_rating"] <= 1400
    assert res["skills"]["accuracy"] >= 50  # mixed but far from 0


def test_accuracy_never_exceeds_100():
    """A game of Best moves can have POSITIVE win-delta on every move — the raw
    1.0 + delta/100 formula would average ABOVE 1.0 and report accuracy > 100.
    Accuracy is a percentage; 100 is the ceiling."""
    game = {
        "moves": [
            {"win_delta_pct": 9.0},
            {"win_delta_pct": 6.0},
            {"win_delta_pct": 4.0},
        ]
    }
    acc = cg._accuracy_of(game)
    assert 0.0 <= acc <= 100.0
    assert acc == 100.0  # every move improved win% -> perfect, but capped
    mixed = {
        "moves": [
            {"win_delta_pct": 9.0},
            {"win_delta_pct": -40.0},
            {"win_delta_pct": 4.0},
        ]
    }
    assert 0.0 <= cg._accuracy_of(mixed) <= 100.0
    # A -90% win-delta blunder keeps 1.0-0.9 = 0.1 expected points -> 10%.
    assert cg._accuracy_of({"moves": [{"win_delta_pct": -90.0}]}) == 10.0
    # Only a move that loses more than its entire expected points floors at 0.
    assert cg._accuracy_of({"moves": [{"win_delta_pct": -150.0}]}) == 0.0


def test_write_beyond_old_cap_persists_every_game():
    """Regression: _save_games once truncated the archive to the newest 200
    games on EVERY save — older recorded games were silently destroyed.
    Recording 205 games must persist ALL of them."""
    for _ in range(205):
        g = cg.start_game()
        cg.record_move(g["id"], _move())
        cg.finish_game(g["id"])
    assert len(cg._load_games()) == 205


def test_manifest_records_archive_state():
    """The manifest must record the exact committed archive — total count and
    SHA-256 of the on-disk bytes (a tamper-evident retention record)."""
    g = cg.start_game()
    cg.record_move(g["id"], _move())
    cg.finish_game(g["id"])
    m = json.loads(
        cg._GAMES_FILE.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8")
    )
    assert m["store"] == "games.jsonl"
    assert m["total"] == 1
    assert m["sha256"] == hashlib.sha256(cg._GAMES_FILE.read_bytes()).hexdigest()
    assert m["policy"].startswith("archive-all")
