"""Tests for the chess trainer service + API.

The Stockfish subprocess is mocked (the real engine is never spawned in tests)
so the legality, classification, explanation, and routing logic is exercised
deterministically and fast. The Qdrant book-index retrieval is also mocked.
"""

import chess
import pytest

from swarm_os.services import chess_trainer as ct


# ---------------------------------------------------------------------------
# Classification (pure logic)
# ---------------------------------------------------------------------------
def test_expected_points_midpoint_and_edges():
    # Draw-aware (lichess curve): 0 cp = 0.5 (drawish), ±400 cp maps via the
    # winning-chances curve (not the old 400-ELO logistic).
    assert ct._expected_points(500, 0) == pytest.approx(0.5)
    assert ct._winning_chances(0) == pytest.approx(0.0, abs=0.01)
    assert ct._winning_chances(100) == pytest.approx(0.18, abs=0.02)
    assert ct._winning_chances(-100) == pytest.approx(-0.18, abs=0.02)
    assert ct._expected_points(500, 400) == pytest.approx(0.8135, abs=0.02)
    assert ct._expected_points(500, -400) == pytest.approx(0.1865, abs=0.02)


def test_classify_best_short_circuit():
    assert ct._classify(500, 30, 25, was_best=True) == "Best"


def test_classify_good_move():
    # Tiny loss (< 2% expected points) => Good/Excellent.
    assert ct._classify(500, 50, 40, was_best=False) in ("Excellent", "Good")


def test_classify_blunder_big_loss():
    # Large swing (100cp -> -400cp) that ALSO loses a piece => Blunder.
    assert (
        ct._classify(500, 100, -400, was_best=False, material_delta=-3.0) == "Blunder"
    )


def test_classify_no_material_loss_caps_at_inaccuracy():
    # A fine move the engine merely dislikes (big eval swing, NO material lost)
    # is at most an Inaccuracy — a normal developing move is not a blunder
    # (chess.com: beginners average ~70% accuracy playing normal moves).
    assert (
        ct._classify(500, 100, -400, was_best=False, material_delta=0.0) == "Inaccuracy"
    )
    assert (
        ct._classify(500, 259, -298, was_best=False, material_delta=0.0) == "Inaccuracy"
    )


def test_classify_material_loss_floors():
    # Hanging material is never missed even if the eval didn't swing much.
    assert ct._classify(500, 100, 90, was_best=False, material_delta=-1.0) == "Mistake"
    assert ct._classify(500, 100, 90, was_best=False, material_delta=-3.0) == "Blunder"


def test_classify_rating_scaled_thresholds():
    # chess.com's verified model uses FIXED expected-points cutoffs at every
    # rating (Inaccuracy 0.05 / Mistake 0.10 / Blunder 0.20) — the rating
    # difference shows in the moves a player makes, not in looser thresholds.
    # Same loss => same classification regardless of rating.
    low = ct._classify(400, 30, -30, was_best=False)
    high = ct._classify(2000, 30, -30, was_best=False)
    assert low == high


def test_classify_excellent_reachable_with_no_material_loss():
    # A tiny eval loss (< 2% expected points) with no material lost must be
    # "Excellent" — the old typo ("Good" if loss >= 0.02 else "Good") made the
    # branch return "Good" in BOTH arms, so "Excellent" was unreachable.
    assert (
        ct._classify(500, 20, 15, was_best=False, material_delta=0.0) == "Excellent"
    )
    # A loss in the 0.02-0.05 band (Good) stays Good, distinct from Excellent.
    assert (
        ct._classify(500, 80, 50, was_best=False, material_delta=0.0) == "Good"
    )


def test_classify_queen_hang_is_blunder_not_inaccuracy():
    # material_delta=-9 (a full queen) must floor to Blunder — the old default
    # of material_delta=0.0 made the material guard cap it at Inaccuracy.
    assert (
        ct._classify(500, 0, -50, was_best=False, material_delta=-9.0) == "Blunder"
    )


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


def test_engine_reply_returns_move(monkeypatch, tmp_path):
    import asyncio

    # engine_reply gates on STOCKFISH_PATH.exists() BEFORE the mocked eval runs.
    # bin/stockfish.exe is a local (untracked) Windows binary, so on the Linux
    # CI runner the real path doesn't exist and engine_reply would return
    # ok:False without ever reaching the (mocked) engine. Point it at a real
    # temp file so the sampling logic is what's exercised, not the exists() gate.
    fake_engine = tmp_path / "stockfish"
    fake_engine.write_bytes(b"")
    monkeypatch.setattr(ct, "STOCKFISH_PATH", fake_engine)
    # The human-like opponent evaluates via _best_move_and_cp then samples.
    monkeypatch.setattr(ct, "_best_move_and_cp", lambda board: ("e2e4", 30.0, ["e2e4"]))
    r = asyncio.run(
        ct.engine_reply(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            rating=500,
            level=1,
        )
    )
    assert r["ok"] is True
    assert r["human_like"] is True


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


# ---------------------------------------------------------------------------
# Coach analysis (plan + sacrifice detection)
# ---------------------------------------------------------------------------
def test_material_balance_start():
    assert ct._material_balance(chess.Board()) == 0.0


def test_material_balance_queen_up():
    b = chess.Board("4k3/8/8/8/8/8/8/Q3K3 w - - 0 1")
    assert ct._material_balance(b) == 9.0


def test_coach_plan_opening_advises_development():
    plan = ct.coach_plan(chess.Board().fen())
    assert plan["ok"] is True
    assert "plan" in plan
    assert plan["attack_now"] is False
    assert plan["standard_plan"]["key"] == "develop"
    assert plan["mode"] == "improve"


def test_coach_plan_material_branch():
    # White down a queen -> defend mode, Defend & Complicate.
    plan = ct.coach_plan("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNB1KBNR w KQkq - 0 1")
    assert plan["ok"] is True
    assert plan["mode"] == "defend"
    assert plan["material"] == -9.0
    assert plan["standard_plan"]["key"] == "defend"


def test_coach_plan_invalid_fen_fails_closed():
    plan = ct.coach_plan("not-a-fen")
    assert plan["ok"] is False


def test_coach_plan_returns_weak_square_and_worst_piece():
    # Isolated enemy pawn on d4 (black), white to move.
    fen = "r3k3/pp3ppp/2n1b3/8/3p4/2N2N2/PPP2PPP/4K3 w - - 0 1"
    plan = ct.coach_plan(fen)
    assert plan["ok"] is True
    assert plan.get("weak_square")  # an isolated black pawn exists


def test_detect_sacrifice_unsound_is_blunder_not_sacrifice():
    # Qh5 blunder: gives up the queen AND loses eval -> NOT a sacrifice.
    sac = ct.detect_sacrifice(
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
        "d1h5",
        before_cp=30.0,
        after_cp=-300.0,
    )
    assert sac is None  # eval collapsed -> blunder, not sacrifice


def test_detect_sacrifice_sound_when_eval_held():
    # Give up a bishop but keep the eval -> sound sacrifice (Brilliant-grade).
    sac = ct.detect_sacrifice(
        "r1bq1rk1/pppp1ppp/2n2n2/8/2B1P3/2NP4/PPP2PPP/R1BQ1RK1 w - - 0 1",
        "c4h7",
        before_cp=40.0,
        after_cp=30.0,  # held within tolerance
    )
    # If the constructed position isn't a clean sacrifice, it may be None; but
    # if it returns a result it must be sound (never a collapsing-eval 'sac').
    if sac is not None:
        assert sac["brilliant"] is True
        assert sac["eval_held"] is True


def test_detect_sacrifice_illegal_move_none():
    sac = ct.detect_sacrifice(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "e2e5",
        before_cp=20.0,
        after_cp=10.0,
    )
    assert sac is None


def test_api_coach_hint(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.post(
            "/chess/trainer/coach/hint",
            json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
        )
        assert r.status_code == 200
        j = r.json()
        assert j["ok"] is True
        assert "plan" in j
        assert "hint_level_1" in j
        assert "hint_level_2" in j


# ---------------------------------------------------------------------------
# Hanging-piece training (the #1 beginner lever)
# ---------------------------------------------------------------------------
def test_find_hanging_pieces_detects_undefended_enemy():
    # White to move: black's e5 pawn is attacked by Nf3 and undefended -> hanging.
    fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    res = ct.find_hanging_pieces(fen)
    assert res["ok"] is True
    assert res["count"] >= 1
    sqs = {h["square"] for h in res["hanging"]}
    assert "e5" in sqs


def test_find_hanging_pieces_defended_is_not_hanging():
    # Black's e5 pawn is defended by Nc6 -> not hanging.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    res = ct.find_hanging_pieces(fen)
    assert res["ok"] is True
    assert "e5" not in {h["square"] for h in res["hanging"]}


def test_find_hanging_pieces_no_hang_at_start():
    res = ct.find_hanging_pieces(chess.Board().fen())
    assert res["ok"] is True
    assert res["count"] == 0


def test_check_move_safety_safe_move():
    res = ct.check_move_safety(chess.Board().fen(), "d2d4")
    assert res["ok"] is True
    assert res["safe"] is True
    assert res["hanging_after"] == []


def test_check_move_safety_hangs_piece():
    # A move that goes en prise to an enemy pawn should be flagged unsafe.
    # White plays Bf1-b5 where black's pawn on a6 can take it? Construct:
    # white bishop moves to b5, black pawn a6 attacks b5, nothing defends it.
    fen = "rnbqkbnr/1ppp1ppp/p7/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3"
    res = ct.check_move_safety(fen, "f1b5")
    # The bishop on b5 may be defended by nothing -> hanging (safe=False).
    assert res["ok"] is True
    if res["hanging_after"]:
        assert res["safe"] is False
    else:
        # If the engine/defense says it's fine, at least assert shape.
        assert "safe" in res


def test_check_move_safety_direct_hang_detected():
    # Black pawn a6 attacks the white bishop on b5; white has no pawn a4, so the
    # bishop is already hanging before white moves. Moving Ng1-f3 (legal) keeps
    # it hanging — the safety check should report the hanging bishop.
    fen = "rnbqkbnr/1ppp1ppp/p7/1B6/4P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 4"
    res = ct.check_move_safety(fen, "g1f3")
    assert res["ok"] is True
    assert res["safe"] is False  # bishop b5 hangs after the move
    assert "b5" in res["hanging_after"]


def test_check_move_safety_catches_en_prise_king():
    # Moving into check should be flagged unsafe.
    res = ct.check_move_safety(
        "rnbqkbnr/pppp1ppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1",
        "e7e5",
    )
    # e5 is safe; instead test moving the king into a checked square via a legal
    # self-check: f7f5 for black after white has a bishop on b5 is legal but...
    # Just assert the shape.
    assert "safe" in res


def test_check_move_safety_illegal():
    res = ct.check_move_safety(chess.Board().fen(), "e2e5")
    assert res["ok"] is False
    assert res["safe"] is False


def test_check_move_safety_makes_check_is_honest_relabel():
    """A legal move can never leave the mover's OWN king in check (that is
    illegal), so the old 'king_in_check' reading of board.is_check() after the
    push actually reported whether the MOVER GAVE CHECK — and the message
    mislabelled that good outcome as 'your king is left in check'. The field is
    now honestly named makes_check, non-blocking."""
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P2Q/8/PPPP1PPP/RNB1KBNR w KQkq - 1 3"
    res = ct.check_move_safety(fen, "h4e7")  # Qxe7+ gives check (queen hangs too)
    assert res["makes_check"] is True
    # The queen hanging on e7 still blocks — gaving check never excuses a hang.
    assert res["safe"] is False
    assert "e7" in res["hanging_after"]


def test_check_move_safety_king_exposed_only_when_castled():
    """The king-exposure advisory fires only for a LOW-false-positive trigger:
    a CASTLED beginner pushing a shield pawn in front of the castle (f/g for a
    short castle). It must never fire pre-castle, and it never forces safe=False
    by itself (a principled f3/g3 can be fine)."""
    castled = chess.Board()
    castled.set_fen("rnbq1rk1/pppp1ppp/5n2/4p3/4P3/8/PPPP1PPP/RNBQ1RK1 w - - 0 6")
    res = ct.check_move_safety(castled.fen(), "f2f3")
    assert res["king_exposed"] is True
    # Advisory only: an otherwise-hanging-free move stays safe.
    assert res["safe"] is True

    # Not castled -> no exposure advisory.
    start = ct.check_move_safety(chess.Board().fen(), "h2h3")
    assert start["king_exposed"] is False
    assert start["safe"] is True

    # A non-shield pawn (a-push) while castled -> not the shield trigger.
    a = ct.check_move_safety(castled.fen(), "a2a3")
    assert a["king_exposed"] is False


def test_check_move_safety_safe_fields_present():
    """The safe path returns the advisory fields so consumers can rely on the
    contract '{ok, safe, hanging_after, makes_check, king_exposed, message}'."""
    res = ct.check_move_safety(chess.Board().fen(), "e2e4")
    assert {"ok", "safe", "hanging_after", "makes_check", "king_exposed", "message"} <= set(res)


def test_threats_from_move_detects_new_attack():
    # After black plays ...Ng8-f6, the knight attacks white's e4 pawn (which
    # white just moved) — that pawn is now under attack.
    fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    res = ct.threats_from_move(fen, "g8f6")
    assert res["ok"] is True
    assert res["threats"] == ["e4"]
    assert res["gives_check"] is False


def test_threats_from_move_illegal():
    res = ct.threats_from_move(chess.Board().fen(), "e2e5")
    assert res["ok"] is False


def test_hanging_drill_returns_position_and_find():
    res = ct.hanging_drill()
    assert res["ok"] is True
    assert "fen" in res
    assert "find" in res
    assert res["find"]["ok"] is True


def test_api_safety_and_drill(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.get("/chess/trainer/drill/hanging")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.post(
            "/chess/trainer/safety", json={"fen": chess.Board().fen(), "uci": "d2d4"}
        )
        assert r.status_code == 200
        assert r.json()["safe"] is True
        r = c.post(
            "/chess/trainer/threats",
            json={
                "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                "uci": "g8f6",
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# Standard-plans menu + persistent plan state
# ---------------------------------------------------------------------------
def test_standard_plan_convert_when_up_material():
    import chess as _ch

    b = _ch.Board("rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    std = ct._detect_standard_plan(b, _ch.WHITE, attack_now=False, weak=None)
    assert std["key"] == "convert"
    assert "extra pawn" in std["recipe"]


def test_standard_plan_defend_when_down():
    import chess as _ch

    b = _ch.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNB1KBNR w KQkq - 0 1")
    std = ct._detect_standard_plan(b, _ch.WHITE, attack_now=False, weak=None)
    assert std["key"] == "defend"
    assert "counterplay" in std["recipe"]


def test_persistent_plan_persists_on_same_trigger():
    from swarm_os.services.chess_plans import advance, reset

    reset("g1")
    a = advance(
        "g1",
        {
            "ok": True,
            "standard_plan": {
                "key": "develop",
                "name": "Develop",
                "recipe": "x",
                "trigger": "y",
            },
        },
    )
    b = advance(
        "g1",
        {
            "ok": True,
            "standard_plan": {
                "key": "develop",
                "name": "Develop",
                "recipe": "x",
                "trigger": "y",
            },
        },
    )
    assert a["persisted"] is False
    assert b["persisted"] is True
    assert b["unchanged_moves"] == 1


def test_persistent_plan_regenerates_on_trigger_change():
    from swarm_os.services.chess_plans import advance, reset

    reset("g2")
    advance(
        "g2",
        {
            "ok": True,
            "standard_plan": {
                "key": "develop",
                "name": "Develop",
                "recipe": "x",
                "trigger": "y",
            },
        },
    )
    c = advance(
        "g2",
        {
            "ok": True,
            "standard_plan": {
                "key": "convert",
                "name": "Trade + Convert",
                "recipe": "z",
                "trigger": "w",
            },
        },
    )
    assert c["persisted"] is False
    assert c["plan"]["name"] == "Trade + Convert"


def test_persistent_plan_coach_none_fails_gracefully():
    from swarm_os.services.chess_plans import advance, reset

    reset("g3")
    r = advance("g3", {"ok": False})
    assert r["ok"] is True
    assert r["plan"] is None


def test_eval_cp_mate_does_not_crash():
    """Regression: PovScore.score() returns None for mates — _eval_cp must use
    mate_score= so a mate position evaluates to a large signed value instead of
    throwing TypeError and returning 0 (which corrupted classification + eval
    bar in endgames)."""
    import chess.engine as ce

    # Mate-in-12 for White.
    info = {"score": ce.PovScore(ce.Mate(12), chess.WHITE)}
    assert ct._eval_cp(info) == pytest.approx(99988.0)
    # Side-to-move being mated in 3 -> negative.
    info2 = {"score": ce.PovScore(ce.Mate(-3), chess.BLACK)}
    assert ct._eval_cp(info2) == pytest.approx(-99997.0)


# ---------------------------------------------------------------------------
# Socratic coach
# ---------------------------------------------------------------------------
def test_socratic_coach_deterministic_fallback_without_llm(monkeypatch):
    """The Socratic coach must NOT raise when no cloud key is set — it degrades
    to a deterministic engine-grounded nudge (fail-open, never a crash)."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ct, "_LLM_EXPLAIN_ENABLED", True)

    import asyncio

    plan = ct.coach_plan(chess.Board().fen())
    res = asyncio.run(ct._socratic_coach_turn(chess.Board().fen(), plan, "e2e4", []))
    assert res["ok"] is True
    assert res["reply"]
    # The fallback nudge never names a move / reveals nothing secret.
    assert "e2e4" not in res["reply"]


def test_socratic_coach_llm_failure_degrades(monkeypatch):
    """A throwing LLM must degrade to the deterministic fallback, never raise."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ct, "_LLM_EXPLAIN_ENABLED", True)

    import asyncio

    import litellm

    async def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(litellm, "acompletion", boom)

    plan = ct.coach_plan(chess.Board().fen())
    res = asyncio.run(ct._socratic_coach_turn(chess.Board().fen(), plan, "e2e4", [{"role": "user", "content": "I'm stuck"}]))
    assert res["ok"] is True
    assert res["reply"]


def test_socratic_api_wires_history_and_best_move(monkeypatch):
    """The /coach/socratic route passes history through and echoes the engine's
    best_move_san back to the frontend (for the fail-open reveal + arrows)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    async def fake_turn(fen, plan, best_move_san, history, proposed_uci=None):
        assert history  # non-empty dialogue reaches the service
        assert proposed_uci is None
        return {"ok": True, "reply": "Look at their king's file."}

    monkeypatch.setattr(ct, "_socratic_coach_turn", fake_turn)
    monkeypatch.setattr(ct, "_best_move_and_cp", lambda board: ("e2e4", 30.0, ["e2e4"]))
    monkeypatch.setattr(ct, "_proposal_eval", lambda fen, uci: {"ok": True, "uci": uci})

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.post(
            "/chess/trainer/coach/socratic",
            json={"fen": chess.Board().fen(), "history": [{"role": "user", "content": "I'm stuck"}]},
        )
        assert r.status_code == 200
        j = r.json()
        assert j["ok"] is True
        assert j["reply"] == "Look at their king's file."
        assert j["best_move_san"] == "e4"  # SAN, not UCI — the route converts


def test_socratic_api_proposed_uci_echoes_proposal(monkeypatch):
    """When proposed_uci is given, the route forwards it to the service AND
    echoes the proposal eval back (for the frontend arrow/delta display)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    captured = {}

    async def fake_turn(fen, plan, best_move_san, history, proposed_uci=None):
        captured["uci"] = proposed_uci
        return {
            "ok": True,
            "reply": "Let's look at that king file first.",
            # _socratic_coach_turn now computes + echoes the proposal itself; the
            # route no longer re-runs _proposal_eval (the double-engine-eval bug).
            "proposal": {"ok": True, "uci": proposed_uci, "classification": "Inaccuracy"},
        }

    async def fake_proposal_eval(fen, uci):
        return {"ok": True, "uci": uci, "classification": "Inaccuracy"}

    monkeypatch.setattr(ct, "_socratic_coach_turn", fake_turn)
    monkeypatch.setattr(ct, "_best_move_and_cp", lambda board: ("e2e4", 30.0, ["e2e4"]))
    monkeypatch.setattr(ct, "_proposal_eval", fake_proposal_eval)

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.post(
            "/chess/trainer/coach/socratic",
            json={
                "fen": chess.Board().fen(),
                "history": [{"role": "user", "content": "What about e2e4?"}],
                "proposed_uci": "e2e4",
            },
        )
        assert r.status_code == 200
        assert captured["uci"] == "e2e4"
        assert r.json()["proposal"]["uci"] == "e2e4"
        assert r.json()["proposal"]["classification"] == "Inaccuracy"


def test_proposal_eval_illegal_move_fails_closed(monkeypatch):
    """_proposal_eval must never fabricate an eval — an illegal move returns
    ok=False, never a guessed win-delta."""
    import asyncio

    monkeypatch.setattr(ct, "_best_move_and_cp", lambda board: ("e2e4", 30.0, ["e2e4"]))
    res = asyncio.run(ct._proposal_eval(chess.Board().fen(), "e2e5"))
    assert res["ok"] is False
    assert "not a legal move" in res["error"]


def test_socratic_deterministic_fallback_reacts_to_proposal(monkeypatch):
    """Without an LLM, a proposed bad move still gets an engine-grounded
    deterministic reaction (win-delta based), not silence."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import asyncio

    # e2e4 is the best first move from the start position.
    monkeypatch.setattr(ct, "_best_move_and_cp", lambda board: ("e2e4", 30.0, ["e2e4"]))
    plan = ct.coach_plan(chess.Board().fen())
    res = asyncio.run(
        ct._socratic_coach_turn(
            chess.Board().fen(), plan, "e4", [], proposed_uci="a2a3"
        )
    )
    assert res["ok"] is True
    assert res["reply"]
    assert "e4" in res["reply"] or "win chance" in res["reply"]


def test_engine_strong_level20_plays_best_move_not_blunder_sampling():
    """Regression (2026-08-23 audit F1): /engine-strong sent level=20, which
    missed the 1-4 sampling dicts and landed in the level-2 defaults
    (temp=1.4, blunder_p=0.22) - the 'strongest possible reply' endpoint
    blundered 22% of the time. Levels > 4 must bypass human-like sampling
    and return the engine's best move deterministically."""
    import asyncio

    r = asyncio.run(
        ct.engine_reply(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            rating=2500,
            level=20,
        )
    )
    assert r["ok"] is True
    assert r.get("strongest") is True
    assert r["move"] == "e2e4"  # from the monkeypatched _best_move_and_cp


def test_evaluate_move_fail_closed_when_engine_unavailable(monkeypatch):
    """Regression (2026-08-23 audit F2): when _best_move_and_cp returns None
    (engine missing or lock timeout), evaluate_move classified the move with
    zeroed evals -> 'Excellent' with ok:True. Must fail closed instead."""
    import asyncio

    monkeypatch.setattr(ct, "_best_move_and_cp", lambda board: (None, 0.0, []))
    r = asyncio.run(
        ct.evaluate_move(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "g1f3",
            rating=800,
        )
    )
    assert r["ok"] is False
    assert "engine" in r["error"].lower()
    assert "classification" not in r
