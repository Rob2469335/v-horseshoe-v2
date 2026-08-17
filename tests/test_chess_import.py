"""Tests for the chess.com game importer.

HTTP (public API) and the engine are mocked — the PGN parsing, move analysis,
color filter, and store-feeding logic are exercised deterministically.
"""

import pytest

from swarm_os.services import chess_import as ci

SAMPLE_PGN = """[Event "Live Chess"]
[Site "chess.com"]
[Date "2026.08.01"]
[White "tester"]
[Black "opponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 1-0
"""


@pytest.fixture(autouse=True)
def _mock_http(monkeypatch):
    """Stub the public-API calls with canned responses."""

    async def fake_archives(username, n=3):
        return ["https://api.chess.com/pub/player/tester/games/2026/08"]

    async def fake_fetch(url, timeout=20.0):
        return {
            "games": [
                {
                    "pgn": SAMPLE_PGN,
                    "url": "https://www.chess.com/game/live/1",
                }
            ]
        }

    monkeypatch.setattr(ci, "_last_archives", fake_archives)
    monkeypatch.setattr(ci, "_fetch", fake_fetch)
    yield


@pytest.fixture(autouse=True)
def _mock_engine(monkeypatch):
    """Deterministic engine: always 'best' is the move's from+to, eval 0."""

    def fake_bmcp(board):
        if board.move_stack:
            last = board.move_stack[-1]
            return (last.uci(), 0.0, [last.uci()])
        return ("e2e4", 0.0, ["e2e4"])

    monkeypatch.setattr(ci, "_best_move_and_cp", fake_bmcp)
    yield


def test_parse_game_pgn():
    parsed = ci._parse_game_pgn(SAMPLE_PGN)
    assert parsed is not None
    headers, board = parsed
    assert headers["White"] == "tester"
    assert len(board.move_stack) == 14


def test_parse_game_pgn_bad():
    assert ci._parse_game_pgn("not a pgn") is None


def test_analyze_game_produces_move_records():
    parsed = ci._parse_game_pgn(SAMPLE_PGN)
    headers, board = parsed
    records = ci._analyze_game(board)
    assert len(records) == 14
    assert records[0]["uci"] == "e2e4"
    assert "classification" in records[0]
    assert "pre_fen" in records[0]


def test_import_games_feeds_stores(monkeypatch, tmp_path):
    import asyncio

    from swarm_os.services import chess_games as cg
    from swarm_os.services import chess_mistakes as cm

    monkeypatch.setattr(cg, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cg, "_GAMES_FILE", tmp_path / "chess" / "games.jsonl")
    monkeypatch.setattr(cm, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cm, "_STORE_FILE", tmp_path / "chess" / "mistakes.jsonl")
    monkeypatch.setattr(cm, "_ladder_days", lambda: [1, 3, 7])

    res = asyncio.run(ci.import_games("tester", months=1, max_games=10))
    assert res["ok"] is True
    assert res["games_fetched"] == 1
    assert res["games_analyzed"] == 1
    assert res["moves_analyzed"] == 14
    assert len(cg._load_games()) >= 1


def test_import_games_empty_username():
    import asyncio

    res = asyncio.run(ci.import_games("  "))
    assert res["ok"] is False
    assert "username" in res["error"]


def test_import_games_no_archives(monkeypatch):
    import asyncio

    async def no_archives(username, n=3):
        return []

    monkeypatch.setattr(ci, "_last_archives", no_archives)
    res = asyncio.run(ci.import_games("tester"))
    assert res["ok"] is False
    assert "no game archives" in res["error"]


def test_import_games_color_filter(monkeypatch, tmp_path):
    import asyncio

    from swarm_os.services import chess_games as cg
    from swarm_os.services import chess_mistakes as cm

    monkeypatch.setattr(cg, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cg, "_GAMES_FILE", tmp_path / "chess" / "games.jsonl")
    monkeypatch.setattr(cm, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cm, "_STORE_FILE", tmp_path / "chess" / "mistakes.jsonl")
    # With 'black' color and the player only being White in the sample, no games
    # should be analyzed.
    res = asyncio.run(ci.import_games("tester", months=1, max_games=10, color="black"))
    assert res["games_analyzed"] == 0


def test_api_import_endpoint(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.post(
            "/chess/trainer/import/chesscom",
            json={"username": "tester", "months": 1, "max_games": 5},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.post("/chess/trainer/import/chesscom", json={"username": ""})
        assert r.status_code == 422


def test_username_persistence(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, "_USERNAME_FILE", tmp_path / "last_username.json")
    res = ci.save_last_username("Lilrob2")
    assert res["ok"] is True
    assert ci.get_last_username()["username"] == "lilrob2"
    # Empty username refused.
    assert ci.save_last_username("  ")["ok"] is False


def test_api_username_endpoints(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ci, "_USERNAME_FILE", tmp_path / "last_username.json")
    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.get("/chess/trainer/import/chesscom/username")
        assert r.status_code == 200
        assert r.json()["username"] is None
        r = c.post(
            "/chess/trainer/import/chesscom/username", json={"username": "Lilrob2"}
        )
        assert r.status_code == 200
        assert r.json()["username"] == "lilrob2"
        r = c.get("/chess/trainer/import/chesscom/username")
        assert r.json()["username"] == "lilrob2"
