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
    # Point the DB path + the durable cache at a temp dir and write a tiny
    # synthetic Fischer DB, so tests never read the real 5.7MB database or the
    # production curated-games cache.
    db = tmp_path / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "Fischer.pgn").write_text(SAMPLE_PGN, encoding="utf-8")
    (db / "Carlsen.pgn").write_text(SAMPLE_PGN, encoding="utf-8")
    monkeypatch.setattr(gg, "_DB_FISCHER", db / "Fischer.pgn")
    monkeypatch.setattr(gg, "_DB_CARLSEN", db / "Carlsen.pgn")
    monkeypatch.setattr(gg, "_GAMES_CACHE", tmp_path / "curated_games.json")
    monkeypatch.setattr(gg, "_games_store", None)
    # Only the first curated game matches our synthetic DB.
    monkeypatch.setattr(gg, "CURATED", [gg.CURATED[0]])
    yield
    monkeypatch.setattr(gg, "_games_store", None)


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
    import asyncio

    res = asyncio.run(gg.list_gm_games())
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


def test_study_mode_reveals_and_explains(monkeypatch):
    """STUDY MODE: the GM's move is REVEALED with a full explanation, not a
    guess prompt — and stepping past the end reports finished."""
    import asyncio

    from swarm_os.services import chess_trainer as ct

    def fake_bmcp(board):
        return ("g1f3", 30.0, ["g1f3"])

    monkeypatch.setattr(ct, "_best_move_and_cp", fake_bmcp)
    monkeypatch.setenv("OPENAI_API_KEY", "")

    async def fake_retrieve(q, top_k=2):
        return []

    monkeypatch.setattr("swarm_os.services.chess_book_memory.retrieve", fake_retrieve)

    res = asyncio.run(gg.study_game("fischer-game-of-the-century", 0))
    assert res["ok"] is True
    assert res["finished"] is False
    assert res["gm_move_san"] == "Nf3"  # the GM's first move is shown, not guessed
    assert res["explanation"]
    assert res["fen_before"].startswith("rnbqkbnr/pppppppp")

    # Stepping past the final ply reports finished.
    res_end = asyncio.run(gg.study_game("fischer-game-of-the-century", 1000))
    assert res_end["ok"] is True
    assert res_end["finished"] is True


def test_critical_moment_detection():
    """A routine recapture is NOT a think position; a material-winning tactic
    IS — and carries structured type/difficulty metadata."""
    import chess

    # Routine mutual recapture (net material unchanged): 1.d4 d5 2.c4 dxc4
    # 3.e3 b5 4.a4 c6 5.axb5 cxb5 (both sides trade pawns, swing 0)
    b = chess.Board()
    for san in ("d4", "d5", "c4", "dxc4", "e3", "b5", "a4", "c6", "axb5", "cxb5"):
        b.push_san(san)
    cm = gg._critical_moment(b, "cxb5", ply=9)
    assert cm["think_required"] is False  # an equal trade is not a decision

    # A tactic that wins material (hangs-then-captures a queen's defender):
    # 1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Ba4 Nf6 5.O-O Nxe4 (wins a pawn, tactical)
    b2 = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O"):
        b2.push_san(san)
    cm2 = gg._critical_moment(b2, "Nxe4", ply=9)
    assert cm2["think_required"] is True
    assert cm2["difficulty"] >= 1
    assert isinstance(cm2["critical_type"], list)
    assert cm2["reason"]


def test_study_includes_critical_moment_metadata(monkeypatch):
    """Study-mode responses carry structured critical-moment fields so the
    frontend can decide how to present each move (pause vs pass-through)."""
    import asyncio

    from swarm_os.services import chess_trainer as ct

    def fake_bmcp(board):
        return ("g1f3", 30.0, ["g1f3"])

    monkeypatch.setattr(ct, "_best_move_and_cp", fake_bmcp)
    monkeypatch.setenv("OPENAI_API_KEY", "")

    async def fake_retrieve(q, top_k=2):
        return []

    monkeypatch.setattr("swarm_os.services.chess_book_memory.retrieve", fake_retrieve)

    res = asyncio.run(gg.study_game("fischer-game-of-the-century", 0))
    for field in ("is_key_moment", "critical_type", "difficulty", "think_required"):
        assert field in res, f"missing {field}"
    assert isinstance(res["critical_type"], list)
