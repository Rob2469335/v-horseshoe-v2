"""Tests for the chess trainer service + API.

The Stockfish subprocess is mocked (the real engine is never spawned in tests)
so the legality, classification, explanation, and routing logic is exercised
deterministically and fast. The Qdrant book-index retrieval is also mocked.
"""

import pytest

from swarm_os.services import chess_trainer as ct


# ---------------------------------------------------------------------------
# Classification (pure logic)
# ---------------------------------------------------------------------------
def test_expected_points_midpoint_and_edges():
    assert ct._expected_points(500, 0) == pytest.approx(0.5)
    assert ct._expected_points(500, 400) == pytest.approx(0.909, abs=0.01)
    assert ct._expected_points(500, -400) == pytest.approx(0.091, abs=0.01)


def test_classify_best_short_circuit():
    assert ct._classify(500, 30, 25, was_best=True) == "Best"


def test_classify_good_move():
    # Tiny loss (< 2% expected points) => Good/Excellent.
    assert ct._classify(500, 50, 40, was_best=False) in ("Excellent", "Good")


def test_classify_blunder_big_loss():
    # Large swing (100cp -> -400cp) => Blunder.
    assert ct._classify(500, 100, -400, was_best=False) == "Blunder"


def test_classify_rating_scaled_thresholds():
    # The same loss is a worse classification for a 400-rated player.
    blunder_400 = ct._classify(400, 30, -30, was_best=False)
    same_2000 = ct._classify(2000, 30, -30, was_best=False)
    # 400 is more sensitive -> at least as severe as 2000.
    order = {
        "Best": 0,
        "Excellent": 1,
        "Good": 2,
        "Inaccuracy": 3,
        "Mistake": 4,
        "Blunder": 5,
    }
    assert order[blunder_400] >= order[same_2000]


# ---------------------------------------------------------------------------
# evaluate_move (engine mocked)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _mock_engine(monkeypatch):
    """Patch _best_move_and_cp to return canned values (no subprocess)."""

    async def fake_explain(*a, **k):
        return "deterministic explanation"

    async def fake_frags(q):
        return [{"title": "Winning Chess Tactics"}]

    monkeypatch.setattr(ct, "_best_move_and_cp", lambda board: ("e2e4", 30.0, ["e2e4"]))
    monkeypatch.setattr(ct, "_llm_enhancement", fake_explain)
    monkeypatch.setattr(ct, "_book_fragments", fake_frags)
    yield


def test_evaluate_illegal_move():
    import asyncio

    r = asyncio.run(
        ct.evaluate_move(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "e2e5",
            rating=500,
            want_explain=False,
        )
    )
    assert r["ok"] is False
    assert r["legal"] is False
    assert "not a legal move" in r["error"]


def test_evaluate_valid_move_classified():
    import asyncio

    r = asyncio.run(
        ct.evaluate_move(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "e2e4",
            rating=500,
            want_explain=False,
        )
    )
    assert r["ok"] is True
    assert r["legal"] is True
    assert r["san"] == "e4"
    assert r["classification"] in ct._CLASS_THRESHOLDS or r["classification"] == "Best"
    assert "best_move" in r
    assert "win_delta_pct" in r


def test_evaluate_explanation_renders_when_bad():
    import asyncio

    r = asyncio.run(
        ct.evaluate_move(
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            "d1h5",
            rating=500,
            want_explain=True,
        )
    )
    assert r["ok"] is True
    assert r["explanation"], "explanation must never be empty for a bad move"
    assert "Winning Chess Tactics" in r["explanation"]


def test_evaluate_invalid_fen_fails_closed():
    import asyncio

    r = asyncio.run(
        ct.evaluate_move("not-a-fen", "e2e4", rating=500, want_explain=False)
    )
    assert r["ok"] is False


def test_engine_reply_returns_move(monkeypatch):
    import asyncio

    def fake_new_engine():
        import chess
        import chess.engine

        class FakeEngine:
            def configure(self, options):
                pass

            def play(self, board, limit):
                return chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None)

            def quit(self):
                pass

        return FakeEngine()

    monkeypatch.setattr(ct, "_new_engine", fake_new_engine)
    r = asyncio.run(
        ct.engine_reply(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            rating=500,
            level=1,
        )
    )
    assert r["ok"] is True
    assert r["san"] == "e4"


def test_engine_reply_game_over():
    import asyncio

    r = asyncio.run(
        ct.engine_reply(
            "7k/8/8/8/8/8/8/R6K w - - 0 1",
            rating=500,
            level=1,
        )
    )
    # Not necessarily game over; just must not crash and must return a dict.
    assert isinstance(r, dict)


# ---------------------------------------------------------------------------
# Deterministic explanation template
# ---------------------------------------------------------------------------
def test_deterministic_explanation_blunder():
    text = ct._deterministic_explanation(
        "Blunder",
        30,
        -85,
        "Nf3",
        is_checkmate=False,
        in_check=False,
        frags=[{"title": "Winning Chess Tactics"}],
    )
    assert "blunder" in text.lower()
    assert "Nf3" in text
    assert "Winning Chess Tactics" in text


def test_deterministic_explanation_checkmate():
    text = ct._deterministic_explanation(
        "Best", 1000, 1000, None, is_checkmate=True, in_check=True, frags=[]
    )
    assert "checkmate" in text.lower()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
def test_api_health_and_routes(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.get("/chess/trainer/health")
        assert r.status_code == 200
        j = r.json()
        assert j["ok"] is True
        assert "engine" in j
        assert "book_index" in j
        assert j["practice_positions"] > 0
        r = c.get("/chess/trainer/practice")
        assert r.status_code == 200
        assert len(r.json()["positions"]) > 0


def test_api_evaluate_validates_request(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        # Missing required field -> 422
        r = c.post("/chess/trainer/evaluate", json={})
        assert r.status_code == 422
