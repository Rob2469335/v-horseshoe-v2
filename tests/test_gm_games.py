"""Tests for the GM games (famous games + guess-the-move) service.

The databases are NOT bundled in tests — the fixture writes a tiny synthetic
PGN so the parsing/guess logic is exercised deterministically.
"""

import pytest

from swarm_os.services import gm_games as gg

SAMPLE_PGN = """[Event "Test Game"]
[Site "?"]
[Date "1956.01.01"]
[Round "?"]
[White "Byrne"]
[Black "Fischer"]
[Result "0-1"]

1. Nf3 Nf6 2. c4 g6 3. Nc3 Bg7 4. d4 O-O 0-1
"""


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # Point the DB path at a temp dir and write a tiny synthetic Fischer DB.
    db = tmp_path / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "Fischer.pgn").write_text(SAMPLE_PGN, encoding="utf-8")
    (db / "Carlsen.pgn").write_text(SAMPLE_PGN, encoding="utf-8")
    monkeypatch.setattr(gg, "_DB_FISCHER", db / "Fischer.pgn")
    monkeypatch.setattr(gg, "_DB_CARLSEN", db / "Carlsen.pgn")
    # Only the first curated game matches our synthetic DB.
    monkeypatch.setattr(gg, "CURATED", [gg.CURATED[0]])
    yield


def test_load_game_parses_moves():
    summary, board = gg._load_game("fischer", "Byrne", "Fischer")
    assert summary is not None
    assert summary["result"] == "0-1"
    assert len(summary["moves"]) == 8  # 1.Nf3 Nf6 2.c4 g6 3.Nc3 Bg7 4.d4 O-O
    assert summary["moves"][0] == "Nf3"


def test_load_game_not_found():
    summary, board = gg._load_game("fischer", "Nobody", "NobodyElse")
    assert summary is None


def test_list_gm_games():
    res = gg.list_gm_games()
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["games"][0]["move_count"] == 8


def test_play_game_returns_position_without_answer():
    res = gg.play_game("fischer-game-of-the-century", ply=0)
    assert res["ok"] is True
    assert res["finished"] is False
    assert res["fen"].startswith("rnbqkbnr/pppppppp")
    assert res["side_to_move"] == "white"


def test_play_game_past_end_finishes():
    res = gg.play_game("fischer-game-of-the-century", ply=100)
    assert res["ok"] is True
    assert res["finished"] is True


def test_play_unknown_game():
    res = gg.play_game("nope", 0)
    assert res["ok"] is False


def test_guess_move_correct_and_wrong():
    # At ply 0, White's actual move is Nf3 (g1f3).
    ok = gg.guess_move("fischer-game-of-the-century", 0, "g1f3")
    assert ok["ok"] is True
    assert ok["correct"] is True
    assert ok["gm_move_san"] == "Nf3"
    bad = gg.guess_move("fischer-game-of-the-century", 0, "e2e4")
    assert bad["correct"] is False


def test_guess_move_invalid_ply():
    res = gg.guess_move("fischer-game-of-the-century", 999, "e2e4")
    assert res["ok"] is False


def test_api_gm_endpoints(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.get("/chess/trainer/gm-games")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.post(
            "/chess/trainer/gm-games/play",
            json={"game_id": "fischer-game-of-the-century", "ply": 0, "guess_uci": ""},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.post(
            "/chess/trainer/gm-games/guess",
            json={
                "game_id": "fischer-game-of-the-century",
                "ply": 0,
                "guess_uci": "g1f3",
            },
        )
        assert r.status_code == 200
        assert r.json()["correct"] is True


def test_explain_move_deterministic(monkeypatch):
    """explain_move returns an engine-grounded narrative even without the cloud
    (deterministic path always present)."""
    import asyncio

    from swarm_os.services import chess_trainer as ct

    def fake_bmcp(board):
        return ("g1f3", 30.0, ["g1f3"])

    monkeypatch.setattr(ct, "_best_move_and_cp", fake_bmcp)
    monkeypatch.setenv("OPENAI_API_KEY", "")

    async def fake_retrieve(q, top_k=2):
        return []

    monkeypatch.setattr("swarm_os.services.chess_book_memory.retrieve", fake_retrieve)

    res = asyncio.run(gg.explain_move("fischer-game-of-the-century", 1))
    assert res["ok"] is True
    assert res["gm_move_san"] == "Nf6"
    assert res["explanation"]
    assert "Nf6" in res["explanation"]
