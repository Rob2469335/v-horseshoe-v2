"""SOTA chess trainer — Stockfish 18 evaluation + book-grounded LLM feedback.

The trainer's core loop (2026 SOTA — chess.com Game Review / lichess "learn from
your mistakes" patterns):

  1. The learner plays a move on a legal position (python-chess validates).
  2. Stockfish 18 evaluates the position BEFORE and AFTER the move, and the
     best alternative move (what they should have played).
  3. The move is classified by the expected-points model (chess.com's
     rating-scaled scheme — the current SOTA for "why was my move bad"):
        Best / Excellent / Good / Inaccuracy / Mistake / Blunder / Miss
     thresholds in expected-points LOST, scaled by the learner's rating.
  4. Feedback surfaces: eval-delta in WDL-style win% (raw centipawns hidden for
     a beginner), a one-line prose classification, and a grounded "why" written
     by the LOCAL LLM (llama.cpp qwen3.5-4b on :8080) using the chess-book
     digests as the concept vocabulary.

The engine runs once per request (popen_uci) and is torn down after — the
analysis needs only depth ~12 for instant blunder feedback on a home CPU.

Fail-closed: any stage failure degrades to the raw legal-move result with an
explicit error string — never a fabricated explanation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

import chess

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
STOCKFISH_PATH = _ROOT / "bin" / "stockfish.exe"
_ENGINE_LOCK = threading.Lock()
_MAX_ENGINE_WAIT_S = 60.0
# Bound the local-LLM explanation so the UI never hangs on a slow generation.
_EXPLAIN_TIMEOUT_S = 45.0
# Qwen3.5's think-block is disabled per-request via chat_template_kwargs
# {"enable_thinking": False} (verified: clean prose, finish_reason=stop). The
# local LLM writes the "friendly voice" layer over the engine facts + Qdrant
# book citations; set SWARM_CHESS_LLM_EXPLAIN=0 to force deterministic-only.
_LLM_EXPLAIN_ENABLED = os.environ.get("SWARM_CHESS_LLM_EXPLAIN", "1").strip() not in (
    "0",
    "false",
    "no",
)


# ---------------------------------------------------------------------------
# Move classification (chess.com expected-points model — rating-scaled)
# ---------------------------------------------------------------------------
# Expected-points thresholds per classification (points lost, lower-bound).
# Rating-scaled: a 400-rated blunder threshold is much tighter than a 2400's.
_CLASS_THRESHOLDS = [
    ("Blunder", 0.20),
    ("Mistake", 0.10),
    ("Inaccuracy", 0.05),
    ("Good", 0.02),
    ("Excellent", 0.00),
    ("Best", 0.00),
]


def _expected_points(rating: int, cp: float) -> float:
    """Map a centipawn eval to expected points (1.0 = always winning), roughly
    chess.com's EPM. cp is from the mover's perspective."""
    cp = max(-1000, min(1000, cp))
    win = 1.0 / (1.0 + 10 ** (-cp / 400.0))
    return win


def _classify(rating: int, before_cp: float, after_cp: float, was_best: bool) -> str:
    """Classify a move by expected points LOST between the pre-move eval (mover's
    perspective) and the post-move eval. `was_best` short-circuits to 'Best'."""
    if was_best:
        return "Best"
    loss = _expected_points(rating, before_cp) - _expected_points(rating, after_cp)
    loss = max(0.0, loss)
    scale = max(0.3, min(1.0, rating / 1500.0))  # tighter thresholds for lower ratings
    for name, threshold in _CLASS_THRESHOLDS:
        if loss >= threshold * scale and name != "Best":
            return name
    return "Best"


# ---------------------------------------------------------------------------
# Engine evaluation
# ---------------------------------------------------------------------------
def _new_engine():
    import chess.engine

    return chess.engine.SimpleEngine.popen_uci(str(STOCKFISH_PATH))


def _eval_cp(info) -> float:
    """Return the eval in centipawns from the side-to-move's perspective (the
    default python-chess pov), clamped."""
    score = info.get("score")
    if score is None:
        return 0.0
    pov = score.pov(score.turn)
    if pov.is_mate():
        return 10000.0 if pov.score() > 0 else -10000.0
    return float(pov.score() or 0.0)


def _best_move_and_cp(board) -> tuple[str | None, float, list[str]]:
    """Best move (UCI), its centipawn eval from side-to-move, and the PV as a
    list of UCI moves. Runs on a worker thread (the engine blocks on the UCI
    subprocess) and never raises: on failure returns (None, 0.0, [])."""
    if not STOCKFISH_PATH.exists():
        log.warning("stockfish binary missing at %s", STOCKFISH_PATH)
        return None, 0.0, []
    try:
        import chess.engine
        import concurrent.futures

        with _ENGINE_LOCK:

            def _run():
                engine = _new_engine()
                try:
                    info = engine.analyse(board, chess.engine.Limit(depth=12))
                    pv = [m.uci() for m in info.get("pv", [])]
                    best = pv[0] if pv else None
                    return best, _eval_cp(info), pv
                finally:
                    engine.quit()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_run)
                return fut.result(timeout=_MAX_ENGINE_WAIT_S)
    except Exception as exc:
        log.warning("engine evaluation failed: %s", exc)
        return None, 0.0, []


def _eval_only(board) -> float:
    best, cp, _ = _best_move_and_cp(board)
    return cp


# ---------------------------------------------------------------------------
# Coach analysis (2026 SOTA — engine-grounded plan + sacrifice detection)
# ---------------------------------------------------------------------------
# The research's beginner plan checklist (Silman's 5-step method reduced to 3
# questions) + the chess.com "Brilliant" soundness rule for sacrifices. Every
# output is computed from engine numbers / python-chess structure, never free-
# form LLM speculation (ACT-Eval: freeform chess commentary hallucinates >40%).
_PIECE_VALUE = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9}


def _material_balance(board) -> float:
    """Material from White's perspective (pawns=1, minors=3, rooks=5, queen=9)."""
    bal = 0.0
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if not p:
            continue
        val = _PIECE_VALUE.get(p.symbol().lower(), 0)
        bal += val if p.color == chess.WHITE else -val
    return bal


def _king_zone(board, color) -> tuple[int, int]:
    """(pawn-shield count, king-in-center?) for the given color."""
    king_sq = board.king(color)
    if king_sq is None:
        return (0, 1)
    file, rank = chess.square_file(king_sq), chess.square_rank(king_sq)
    shield = 0
    for df in (-1, 0, 1):
        sq = chess.square(file + df, rank + (1 if color == chess.WHITE else -1))
        if chess.square_rank(sq) in range(8) and chess.square_file(sq) in range(8):
            p = board.piece_at(sq)
            if p and p.color == color and p.piece_type == chess.PAWN:
                shield += 1
    center = rank in (4, 5) if color == chess.WHITE else rank in (3, 2)
    return (shield, 1 if center else 0)


def _worst_piece(board, color) -> str | None:
    """The mover's least-active minor piece (knight/bishop) or queen that's not
    developed and has few moves — the 'develop your worst piece' signal. Rooks
    are ignored (they belong at home until files open)."""
    best_sq = None
    best_score = 99
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if not p or p.color != color:
            continue
        if p.piece_type in (chess.KING, chess.ROOK):
            continue  # rooks stay home in the opening; kings never 'develop'
        count = sum(1 for m in board.legal_moves if m.from_square == sq)
        developed = not (
            (color == chess.WHITE and chess.square_rank(sq) in (0, 1))
            or (color == chess.BLACK and chess.square_rank(sq) in (6, 7))
        )
        score = count - (0 if developed else 3)
        if score < best_score:
            best_score = score
            best_sq = sq
    if best_sq is None:
        return None
    return f"the {chess.piece_name(board.piece_at(best_sq).piece_type)} on {chess.square_name(best_sq)}"


def _weakest_square(board, color) -> str | None:
    """The weakest enemy pawn (isolated/doubled/backward) or a weak square in
    front of one — the 'aim at his weakness' signal."""
    enemy = not color
    weak: list[str] = []
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if not p or p.color != enemy or p.piece_type != chess.PAWN:
            continue
        file, rank = chess.square_file(sq), chess.square_rank(sq)
        neighbors = 0
        for df in (-1, 0, 1):
            if df == 0:
                continue
            f = file + df
            if 0 <= f < 8:
                n = board.piece_at(chess.square(f, rank))
                if n and n.color == enemy and n.piece_type == chess.PAWN:
                    neighbors += 1
        if neighbors == 0:
            weak.append(chess.square_name(sq))
    return weak[0] if weak else None


def coach_plan(fen: str) -> dict[str, Any]:
    """The 3-question plan checklist for the side to move, engine-grounded.
    Returns {ok, plan, king_alert, worst_piece, weak_square, attack_now} where
    `plan` is the one-line beginner plan."""
    try:
        board = chess.Board(fen)
        color = board.turn
    except Exception:
        return {"ok": False, "error": "invalid position"}
    try:
        my_king, my_center = _king_zone(board, color)
        their_king, their_center = _king_zone(board, not color)
        worst = _worst_piece(board, color)
        weak = _weakest_square(board, color)
        # Development count (minor pieces off the back rank).
        my_dev = sum(
            1
            for sq in chess.SQUARES
            if (p := board.piece_at(sq))
            and p.color == color
            and p.piece_type in (chess.KNIGHT, chess.BISHOP)
            and not (
                (color == chess.WHITE and chess.square_rank(sq) in (0, 1))
                or (color == chess.BLACK and chess.square_rank(sq) in (6, 7))
            )
        )
        their_dev = sum(
            1
            for sq in chess.SQUARES
            if (p := board.piece_at(sq))
            and p.color != color
            and p.piece_type in (chess.KNIGHT, chess.BISHOP)
            and not (
                (color == chess.BLACK and chess.square_rank(sq) in (0, 1))
                or (color == chess.WHITE and chess.square_rank(sq) in (6, 7))
            )
        )
        their_king_bad = their_center or their_king <= 2
        my_king_bad = my_center or my_king <= 2
        attack_now = (not my_king_bad) and their_king_bad and my_dev >= their_dev + 1

        # Build the one-line plan from the most lopsided factor.
        if attack_now:
            plan = (
                "their king is exposed and you're better developed — this is your moment; "
                "look for a way to open the files toward it"
            )
        elif weak:
            plan = f"their pawn on {weak} is weak — put a piece on the square in front of it and keep attacking it"
        elif worst:
            plan = f"{worst} isn't doing anything yet — bring it into the game"
        else:
            plan = "quiet position — improve your worst piece and don't force anything"
        return {
            "ok": True,
            "plan": plan,
            "king_alert": f"their king has only {their_king} pawn(s) in front of it"
            if their_king <= 2
            else "",
            "worst_piece": worst,
            "weak_square": weak,
            "attack_now": attack_now,
        }
    except Exception as exc:
        log.warning("coach plan failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Hanging-piece training (2026 SOTA — the #1 beginner lever)
# ---------------------------------------------------------------------------
# The research (Steps Method: board vision is "top priority"; Heisman: the
# hanging piece is "the big mistake"; ChessPivot: detection != solution)
# converges on the same missing skill: spotting LOOSE pieces — both taking the
# opponent's and seeing that your own move hangs one. These functions derive
# positions + checks purely from python-chess (no engine needed for
# attack/defense counts), and the drill feeds the spaced-review queue.


def _attackers_of(board, sq, color) -> int:
    """How many of `color`'s pieces attack the square sq (via python-chess's
    built-in attack map — correct for pawn captures, sliders, knights)."""
    return len(board.attackers(color, sq))


def _defenders_of(board, sq) -> int:
    p = board.piece_at(sq)
    if p is None:
        return 0
    return _attackers_of(board, sq, p.color)


def find_hanging_pieces(fen: str) -> dict[str, Any]:
    """Find every enemy piece that is hanging (attacked and under-defended) for
    the side to move. Returns {ok, hanging: [{square, piece, attackers,
    defenders, capture_uci}], count}."""
    try:
        board = chess.Board(fen)
        me = board.turn
        enemy = not me
        result = []
        for sq in chess.SQUARES:
            p = board.piece_at(sq)
            if not p or p.color != enemy or p.piece_type == chess.KING:
                continue  # ignore the enemy king (it's never 'hanging')
            attackers = _attackers_of(board, sq, me)
            defenders = _defenders_of(board, sq)
            if attackers > defenders:
                # Find a capture move for the learner.
                cap = None
                for m in board.legal_moves:
                    if (
                        m.to_square == sq
                        and board.piece_at(m.from_square)
                        and board.piece_at(m.from_square).color == me
                    ):
                        cap = m.uci()
                        break
                result.append(
                    {
                        "square": chess.square_name(sq),
                        "piece": p.symbol(),
                        "attackers": attackers,
                        "defenders": defenders,
                        "capture_uci": cap,
                    }
                )
        return {"ok": True, "count": len(result), "hanging": result[:8]}
    except Exception as exc:
        log.warning("hanging-piece detection failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _move_hangs_piece(board, move) -> list[str]:
    """After making `move`, which of the mover's pieces are left hanging?
    Returns a list of square names. Checks every own piece attacked more than
    defended (a real material-loss threat), excluding the moved piece (it just
    moved — unless it went en prise)."""
    try:
        probe = board.copy()
        probe.push(move)
        mover = not probe.turn  # the side that just moved
        hanging = []
        for sq in chess.SQUARES:
            p = probe.piece_at(sq)
            if not p or p.color != mover or p.piece_type == chess.KING:
                continue
            attackers = _attackers_of(probe, sq, probe.turn)  # opponent attacks
            defenders = _defenders_of(probe, sq)
            if attackers > defenders:
                hanging.append(chess.square_name(sq))
        return hanging
    except Exception as exc:
        log.warning("move-hangs detection failed: %s", exc)
        return []


def check_move_safety(fen: str, uci: str) -> dict[str, Any]:
    """The pre-move safety check (Heisman Slow->Safe->Active): would this move
    leave a piece hanging, or leave the king in check / badly exposed? Returns
    {ok, safe, hanging_after, king_in_check, message}."""
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return {"ok": False, "safe": False, "error": "not a legal move"}
        hanging = _move_hangs_piece(board, move)
        probe = board.copy()
        probe.push(move)
        king_in_check = probe.is_check()
        if not hanging and not king_in_check:
            return {
                "ok": True,
                "safe": True,
                "hanging_after": [],
                "king_in_check": False,
                "message": "safe — no piece hangs and your king is fine",
            }
        issues = []
        if king_in_check:
            issues.append("your king is left in check")
        if hanging:
            issues.append(f"this move hangs {', '.join(hanging[:3])}")
        return {
            "ok": True,
            "safe": False,
            "hanging_after": hanging,
            "king_in_check": king_in_check,
            "message": "caught it! "
            + " and ".join(issues)
            + " — check before you move",
        }
    except Exception as exc:
        log.warning("move safety check failed: %s", exc)
        return {"ok": False, "safe": False, "error": str(exc)}


def hanging_drill(fen: str | None = None) -> dict[str, Any]:
    """A hanging-piece drill: a position where the side to move can capture a
    loose enemy piece. When no FEN is given, search forward from the start
    position (deterministic, bounded) until a position with a hanging piece is
    found; fall back to the start position."""
    import random

    if fen:
        try:
            board = chess.Board(fen)
            found = find_hanging_pieces(board.fen())
            return {
                "ok": True,
                "fen": board.fen(),
                "find": found,
                "instruction": "find a loose enemy piece you can capture",
            }
        except Exception:
            pass
    # Search forward from the start position until a hanging piece appears.
    rng = random.Random(42)
    start = chess.Board()
    for _ in range(60):
        legal = [m for m in start.legal_moves]
        if not legal:
            break
        m = rng.choice(legal)
        start.push(m)
        if start.is_game_over():
            break
        found = find_hanging_pieces(start.fen())
        if found.get("count", 0) > 0:
            return {
                "ok": True,
                "fen": start.fen(),
                "find": found,
                "instruction": "find a loose enemy piece you can capture",
            }
    return {
        "ok": True,
        "fen": chess.Board().fen(),
        "find": find_hanging_pieces(chess.Board().fen()),
        "instruction": "find a loose enemy piece you can capture",
    }


def threats_from_move(fen: str, uci: str) -> dict[str, Any]:
    """'Looking for Trouble': after a move, what does it threaten? Enumerate the
    enemy pieces the move now attacks that are undefended, and any check."""
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return {"ok": False, "error": "not a legal move"}
        probe = board.copy()
        probe.push(move)
        me = probe.turn  # opponent now; their move was the threat
        threats = []
        for sq in chess.SQUARES:
            p = probe.piece_at(sq)
            if not p or p.color != me or p.piece_type == chess.KING:
                continue
            attackers = _attackers_of(probe, sq, not me)
            defenders = _defenders_of(probe, sq)
            if attackers > defenders:
                threats.append(chess.square_name(sq))
        return {
            "ok": True,
            "threats": threats,
            "gives_check": probe.is_check(),
            "summary": (
                f"their last move attacks {', '.join(threats[:3])}"
                if threats
                else "their last move doesn't hang anything — it's your turn to create a threat"
            ),
        }
    except Exception as exc:
        log.warning("threat detection failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def detect_sacrifice(
    fen: str,
    uci: str,
    before_cp: float,
    after_cp: float,
) -> dict[str, Any] | None:
    """Detect whether the played move was a sound sacrifice (the chess.com
    Brilliant rule). Returns None when not a sacrifice; otherwise a dict with
    {is_sacrifice, pattern, give_up, get_back, eval_held, brilliant}."""
    try:
        import chess

        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return None
        given = board.piece_at(move.from_square)
        if given is None:
            return None
        given_val = _PIECE_VALUE.get(given.symbol().lower(), 0)
        # A sacrifice = gave up a piece (not a pawn trade) with material cost.
        material_after = _material_balance(board)
        board.push(move)
        material_after = _material_balance(board)
        lost_material = _material_balance(chess.Board(fen)) - material_after
        if given_val < 3 and lost_material < 2:
            return None  # not a piece sacrifice (pawn move / even trade)
        # Soundness: eval barely moved (within ~60cp) => you bought the attack.
        held = after_cp >= before_cp - 60.0
        if not held:
            # Gave up material AND lost the eval -> that's a blunder, not a
            # sacrifice. The research's rule: "a blunder is when you lose the
            # piece AND the attack dies." Only report SOUND sacrifices here.
            return None
        # Pattern tag.
        pattern = ""
        cap = board.piece_at(move.to_square)
        target_sq = move.to_square
        # Greek gift: bishop takes h-pawn (h7/h2) with check.
        if (
            given.piece_type == chess.BISHOP
            and chess.square_name(target_sq) in ("h7", "h2")
            and board.is_check()
        ):
            pattern = "Greek gift (Bxh7+/Bxh2+) — rips open the castled king"
        elif (
            given.piece_type == chess.ROOK
            and cap
            and cap.piece_type in (chess.KNIGHT, chess.BISHOP)
        ):
            pattern = "exchange sacrifice — rook for a minor piece"
        elif given_val >= 3 and lost_material >= 3 and not (board.is_check()):
            pattern = "piece sacrifice for initiative"
        if not pattern:
            pattern = "piece sacrifice"
        return {
            "is_sacrifice": True,
            "sound": True,
            "pattern": pattern,
            "give_up": f"a {chess.piece_name(given.piece_type)} (worth {given_val})",
            "get_back": "the initiative — the opponent must answer your threats",
            "eval_held": True,
            "brilliant": True,
        }
    except Exception as exc:
        log.warning("sacrifice detection failed: %s", exc)
        return None


async def engine_reply(fen: str, rating: int = 500, level: int = 1) -> dict[str, Any]:
    """The engine's reply move for playing against it. `level` (1-4) maps to the
    Stockfish Skill Level option so the opponent plays at a fair strength for a
    ~500-rated learner instead of always crushing."""
    if not STOCKFISH_PATH.exists():
        return {"ok": False, "error": "stockfish binary missing"}
    try:
        import chess
        import chess.engine
        import concurrent.futures

        board = chess.Board(fen)
        if board.is_game_over():
            return {
                "ok": True,
                "game_over": True,
                "result": board.result(),
                "is_checkmate": board.is_checkmate(),
                "is_stalemate": board.is_stalemate(),
            }
        skill = max(0, min(20, int({1: 0, 2: 5, 3: 10, 4: 16}.get(level, 0))))

        def _run():
            engine = _new_engine()
            try:
                # Skill Level blunders like a human at that strength.
                engine.configure({"Skill Level": skill})
                limit = chess.engine.Limit(time=1.0)
                if level >= 4:
                    limit = chess.engine.Limit(depth=15)
                result = engine.play(board, limit)
                return result.move
            finally:
                engine.quit()

        with _ENGINE_LOCK:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_run)
                move = fut.result(timeout=_MAX_ENGINE_WAIT_S)
        if move is None:
            return {"ok": False, "error": "engine returned no move"}
        try:
            san = board.san(move)
        except Exception:
            san = move.uci()
        board.push(move)
        return {
            "ok": True,
            "uci": move.uci(),
            "san": san,
            "fen": board.fen(),
            "is_checkmate": board.is_checkmate(),
            "is_stalemate": board.is_stalemate(),
            "in_check": board.is_check(),
        }
    except Exception as exc:
        log.warning("engine reply failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Core trainer action
# ---------------------------------------------------------------------------
async def evaluate_move(
    fen: str,
    uci: str,
    rating: int = 500,
    want_explain: bool = True,
) -> dict[str, Any]:
    """Evaluate the learner's move: legality, classification, eval delta, best
    alternative, and (when want_explain) a local-LLM 'why' grounded in the chess
    book digests."""
    try:
        import chess

        board = chess.Board(fen)
    except Exception as exc:
        return {"ok": False, "error": f"invalid position: {exc}"}
    try:
        move = chess.Move.from_uci(uci)
    except Exception as exc:
        return {"ok": False, "error": f"invalid move '{uci}': {exc}"}
    if move not in board.legal_moves:
        # Legal-move check: fail-closed, never guess.
        return {
            "ok": False,
            "legal": False,
            "error": f"'{uci}' is not a legal move in this position",
            "legal_moves": [m.uci() for m in board.legal_moves][:20],
        }

    before_best, before_cp, _ = _best_move_and_cp(board)
    # SAN must be computed BEFORE pushing (the move is only legal pre-push).
    try:
        played_san = board.san(move)
    except Exception:
        played_san = uci
    board.push(move)

    after_cp = _best_move_and_cp(board)[1]

    # Eval from the MOVER's perspective for classification (before was mover's
    # perspective; after is opponent's perspective now).
    mover_after = -after_cp

    # The played move is the engine's best if it matches the best move found
    # in the single pre-move evaluation above (no re-analysis needed).
    was_best = before_best == uci

    classification = _classify(rating, before_cp, mover_after, was_best)

    # WDL-style win% for the eval bar (mover's perspective).
    win_before = _expected_points(rating, before_cp)
    win_after = _expected_points(rating, mover_after)

    result: dict[str, Any] = {
        "ok": True,
        "legal": True,
        "uci": uci,
        "san": played_san,
        "classification": classification,
        "rating": rating,
        "eval_before_cp": round(before_cp, 1),
        "eval_after_cp": round(mover_after, 1),
        "win_before_pct": round(win_before * 100, 1),
        "win_after_pct": round(win_after * 100, 1),
        "win_delta_pct": round((win_after - win_before) * 100, 1),
        "best_move": before_best,
        "best_move_san": None,
        "in_check": board.is_check(),
        "is_checkmate": board.is_checkmate(),
        "is_stalemate": board.is_stalemate(),
        "fen": board.fen(),
        "explanation": "",
    }
    if before_best:
        try:
            bb = chess.Board(fen)
            result["best_move_san"] = bb.san(chess.Move.from_uci(before_best))
        except Exception:
            pass

    # Coach: the one-line plan for the position the learner is IN (pre-move),
    # engine-grounded (king safety / worst piece / weak square).
    try:
        plan = coach_plan(fen)
        result["coach"] = plan if plan.get("ok") else {}
    except Exception as exc:
        log.warning("coach plan generation failed: %s", exc)
        result["coach"] = {}

    # Sacrifice detection (chess.com Brilliant rule): did the learner give up
    # material but keep the eval -> sound sacrifice?
    try:
        sac = detect_sacrifice(fen, uci, before_cp, mover_after)
        if sac:
            result["sacrifice"] = sac
            if sac.get("brilliant"):
                result["classification"] = "Brilliant"
    except Exception as exc:
        log.warning("sacrifice detection failed: %s", exc)

    # Missed-gift: a sound sacrifice was the engine's best move but the learner
    # played something else — flag it as a teachable moment. Rare path (only on
    # a bad move), so the extra engine eval is acceptable.
    if (
        before_best
        and before_best != uci
        and classification in ("Mistake", "Blunder", "Inaccuracy")
    ):
        try:
            probe = chess.Board(fen)
            bm = chess.Move.from_uci(before_best)
            if bm in probe.legal_moves:
                giver = probe.piece_at(bm.from_square)
                if giver and _PIECE_VALUE.get(giver.symbol().lower(), 0) >= 3:
                    probe.push(bm)
                    best_after_cp = _eval_only(probe)
                    # Sound sacrifice: giving up the piece kept/improved the eval.
                    if best_after_cp >= before_cp - 60.0:
                        result["missed_sacrifice"] = {
                            "move": before_best,
                            "san": result.get("best_move_san"),
                            "message": "you had a sound piece sacrifice here (the best move) but played something else",
                        }
        except Exception as exc:
            log.warning("missed-sacrifice probe failed: %s", exc)

    # Learn-from-mistakes: persist Mistake/Blunder positions (the research's
    # #1 evidence-backed feature — your own blunders become review puzzles).
    if classification in ("Mistake", "Blunder", "Inaccuracy"):
        try:
            from .chess_book_memory import _concept_from, retrieve
            from .chess_mistakes import record_mistake

            concept = _concept_from(classification, f"{uci} {before_best or ''}")
            frags = await retrieve(f"{concept}", top_k=2)
            record_mistake(
                pre_fen=fen,
                played_uci=uci,
                played_san=result["san"],
                best_uci=before_best,
                best_san=result.get("best_move_san"),
                classification=classification,
                concept=concept,
                book_titles=[f.get("title") for f in frags if f.get("title")][:3],
            )
        except Exception as exc:
            log.warning("mistake recording failed: %s", exc)

    # Missed sacrifice -> review queue too: the learner's own position where a
    # sound sacrifice was available but wasn't played. The review asks them to
    # FIND the sacrifice (best move).
    if result.get("missed_sacrifice"):
        try:
            from .chess_book_memory import _concept_from, retrieve
            from .chess_mistakes import record_mistake

            concept = _concept_from("sacrifice", f"{before_best or ''}")
            frags = await retrieve(f"{concept} sacrifice", top_k=2)
            record_mistake(
                pre_fen=fen,
                played_uci=uci,
                played_san=f"{result['san']} (missed a sacrifice)",
                best_uci=before_best,
                best_san=result.get("best_move_san"),
                classification="Missed sacrifice",
                concept=concept,
                book_titles=[f.get("title") for f in frags if f.get("title")][:3],
            )
        except Exception as exc:
            log.warning("missed-sacrifice queueing failed: %s", exc)

    if want_explain and classification not in ("Best", "Excellent"):
        result["explanation"] = await _explain_move(
            fen,
            uci,
            classification,
            before_cp,
            mover_after,
            result.get("best_move_san"),
            is_checkmate=result.get("is_checkmate", False),
            in_check=result.get("in_check", False),
        )
    return result


# ---------------------------------------------------------------------------
# Book-grounded explanation — deterministic first, local LLM enhancement
# ---------------------------------------------------------------------------
# The local llama.cpp Qwen3.5 models ramble in a <think> block and never emit
# prose content on this server build (verified on both 4B and 0.8B), so the
# explanation is generated deterministically from the engine data + the
# Qdrant-retrieved book fragments. The local LLM is attempted as a best-effort
# enhancement with a hard timeout; when it returns nothing (the common case),
# the deterministic text still renders — the trainer never hangs and never
# fabricates a citation.


def _deterministic_explanation(
    classification: str,
    before_cp: float,
    after_cp: float,
    best_move_san: str | None,
    is_checkmate: bool,
    in_check: bool,
    frags: list[dict],
) -> str:
    """Templated, beginner-level explanation grounded in the engine numbers and
    the retrieved book fragments. The SOTA research calls for exactly this:
    visual-first, one short sentence naming the concept, cite the book that
    teaches it — no raw centipawns for a beginner."""
    loss = max(0.0, before_cp - after_cp)
    pieces = loss / 100.0
    lines: list[str] = []
    if is_checkmate:
        lines.append("That move delivers checkmate — well done!")
    elif classification == "Best":
        lines.append("Best move — the engine agrees this is the strongest option.")
    elif classification == "Excellent":
        lines.append("Excellent — a strong move, only a marginally better one exists.")
    elif classification == "Good":
        lines.append(
            "Good move. It keeps the position level, but there was something stronger."
        )
    elif classification == "Inaccuracy":
        lines.append(
            f"This move costs a little ground — roughly {pieces:.1f} pawn(s) of advantage slipped away."
        )
    elif classification == "Mistake":
        lines.append(
            f"This is a mistake — it gives up about {pieces:.1f} pawn(s) of advantage."
        )
    elif classification == "Blunder":
        lines.append(
            f"This is a blunder — you lost roughly {pieces:.1f} pawns of winning chances."
        )
    if best_move_san and classification not in ("Best", "Excellent", "Good"):
        lines.append(f"The engine's best alternative was {best_move_san}.")
    if in_check and not is_checkmate:
        lines.append("Your king is in check — deal with the check first.")
    if frags:
        lines.append(
            "Related book idea: "
            + " | ".join(f"[{r['title']}]" for r in frags if r.get("title"))
        )
    return "\n".join(lines)


async def _book_fragments(query: str) -> list[dict]:
    """Retrieve the chess-book fragments relevant to the concept from the Qdrant
    index (falling back to the keyword scan when the index/embedder is down)."""
    try:
        from .chess_book_memory import retrieve

        frags = await retrieve(query, top_k=3)
        return [f for f in frags if f.get("title")]
    except Exception as exc:
        log.warning("book fragment retrieval failed: %s", exc)
        return []


async def _llm_enhancement(
    fen: str,
    uci: str,
    classification: str,
    before_cp: float,
    after_cp: float,
    best_move_san: str | None,
    frags: list[dict],
) -> str:
    """Best-effort local-LLM prose. Returns '' on any failure/timeout — the
    deterministic explanation already covers the fallback."""
    try:
        import chess

        from ..infra.llama_client import LlamaClient

        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        played_san = board.san(move)
        context = "\n".join(
            f"[{r['title']}]: {r['text'][:300]}" for r in frags if r.get("text")
        )
        prompt = (
            "Explain in 2-3 short sentences why this chess move is a "
            f"{classification}, in plain beginner language. Use only the book "
            "ideas given; do not invent citations.\n\n"
            f"POSITION (FEN): {fen}\nPLAYED: {played_san}\n"
            f"BEST WAS: {best_move_san or 'unknown'}\n\n"
            f"BOOK IDEAS:\n{context}\n\nEXPLANATION:"
        )
        client = LlamaClient(base_url="http://127.0.0.1:8084")
        async with asyncio.timeout(_EXPLAIN_TIMEOUT_S):
            text = await client.generate(
                "qwen3.5-0.8b",
                [
                    {
                        "role": "system",
                        "content": "You are a chess coach. Answer directly and briefly.",
                    },
                    {"role": "user", "content": prompt},
                ],
                # Qwen3.5 dropped Qwen3's /no_think soft-switch; only the
                # template-level hard switch disables the think block and makes
                # the model actually emit prose `content`.
                chat_template_kwargs={"enable_thinking": False},
                max_tokens=300,
            )
        # The 0.8B emits an empty <think>  </think> wrapper first; strip it.
        text = re.sub(r"<think>\s*</think>", "", text).strip()
        return text
    except Exception as exc:
        log.warning("move explanation LLM enhancement failed: %s", exc)
        return ""


async def _explain_move(
    fen: str,
    uci: str,
    classification: str,
    before_cp: float,
    after_cp: float,
    best_move_san: str | None,
    is_checkmate: bool = False,
    in_check: bool = False,
) -> str:
    """Build the beginner explanation: deterministic template grounded in the
    engine numbers + Qdrant book fragments, enhanced by the local LLM when it
    returns prose. Always returns a non-empty string for a classified move."""
    from .chess_book_memory import _concept_from

    concept = _concept_from(classification, f"{uci} {best_move_san or ''}")
    frags = await _book_fragments(f"{concept} {classification}")
    det = _deterministic_explanation(
        classification,
        before_cp,
        after_cp,
        best_move_san,
        is_checkmate,
        in_check,
        frags,
    )
    if not _LLM_EXPLAIN_ENABLED:
        return det
    enhanced = await _llm_enhancement(
        fen, uci, classification, before_cp, after_cp, best_move_san, frags
    )
    if enhanced:
        return f"{det}\n\n{enhanced}"
    return det
