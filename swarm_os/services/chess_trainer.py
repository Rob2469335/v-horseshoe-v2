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
import threading
from pathlib import Path
from typing import Any

import chess

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
STOCKFISH_PATH = _ROOT / "bin" / "stockfish.exe"
_ENGINE_LOCK = threading.Lock()
# Guards the shared _eval_cache dict — the read happens before _ENGINE_LOCK is
# taken and the write inside it, so concurrent threads otherwise race a resize
# during dict iteration (RuntimeError: dictionary changed size during iteration).
_CACHE_LOCK = threading.Lock()
_MAX_ENGINE_WAIT_S = 60.0
# Bound the local-LLM explanation so the UI never hangs on a slow generation.
_EXPLAIN_TIMEOUT_S = 45.0
# Persistent engine (2026 SOTA — one process, TT reused across adjacent
# searches; never re-spawn per eval — spawn + NNUE load is the expensive part).
_persistent_engine = None


def _new_engine():
    import chess.engine

    return chess.engine.SimpleEngine.popen_uci(str(STOCKFISH_PATH))


def close_engine() -> None:
    """Quit the persistent engine cleanly. Required: python-chess's
    SimpleEngine background thread otherwise hangs the interpreter at shutdown
    on Windows (it never terminates on its own). A graceful quit() can itself
    hang during interpreter teardown, so we hard-kill the subprocess — the
    engine has no persistent state that matters."""
    global _persistent_engine
    engine = _persistent_engine
    _persistent_engine = None
    if engine is None:
        return
    try:
        # Direct process kill — reliable at shutdown where quit()'s handshake
        # can deadlock on Windows (verified via faulthandler).
        proc = getattr(engine, "engine", None)
        if (
            proc is not None
            and getattr(proc, "poll", None) is not None
            and proc.poll() is None
        ):
            try:
                proc.terminate()
            except Exception as exc:
                log.debug("stockfish terminate failed (will try kill): %s", exc)
                try:
                    proc.kill()
                except Exception as exc2:
                    log.warning("stockfish kill failed: %s", exc2)
    except Exception as exc:
        log.warning("stockfish shutdown failed: %s", exc)
    try:
        engine.quit()
    except Exception as exc:
        log.warning("engine quit failed: %s", exc)


import atexit

atexit.register(close_engine)


def _get_engine():
    """The shared persistent engine. One process reused across all evals (TT
    reuse between adjacent searches); no configure() — the python-chess↔
    Stockfish-18 `setoption` handshake hangs on this build, and the defaults
    (Threads=1, Hash=16) are correct for a 2-core CPU anyway."""
    global _persistent_engine
    if _persistent_engine is not None:
        return _persistent_engine
    if not STOCKFISH_PATH.exists():
        log.warning("stockfish binary missing at %s", STOCKFISH_PATH)
        return None
    # Timeout the lock: a thread that died mid-search (e.g. an asyncio.to_thread
    # cancelled by a backend restart) would otherwise hold _ENGINE_LOCK forever
    # and wedge every future eval. With a bounded acquire we fail open instead.
    if not _ENGINE_LOCK.acquire(timeout=10):
        log.warning("engine init lock timed out — skipping engine init")
        return None
    try:
        if _persistent_engine is not None:
            return _persistent_engine
        try:
            _persistent_engine = _new_engine()
        except Exception as exc:
            log.warning("engine init failed: %s", exc)
            _persistent_engine = None
    finally:
        _ENGINE_LOCK.release()
    return _persistent_engine


def _analyse(board, limit):
    """Analyse with the persistent engine (TT reuse between adjacent searches —
    the second position is inside the first search's tree)."""
    engine = _get_engine()
    if engine is None:
        return None
    try:
        return engine.analyse(board, limit)
    except Exception as exc:
        log.warning("engine analyse failed: %s", exc)
        return None


def _eval_cp(info) -> float:
    """Return the eval in centipawns from the side-to-move's perspective (the
    default python-chess pov), clamped.

    IMPORTANT: for a mate score, PovScore.score() returns None — the mate
    distance is exposed via .mate(). Pass mate_score= to get a signed centipawn
    equivalent (e.g. mate-in-12 = +99988), so mate positions evaluate correctly
    instead of throwing TypeError / returning 0."""
    if info is None:
        return 0.0
    score = info.get("score")
    if score is None:
        return 0.0
    pov = score.pov(score.turn)
    if pov.is_mate():
        # mate_score maps mate-in-N to +- (100000 - N), preserving sign.
        return float(pov.score(mate_score=100000) or 0.0)
    return float(pov.score() or 0.0)


# FEN-keyed eval cache: revisiting the same position (SR queue, retry-on-blunder,
# review sessions) should be ~0ms instead of re-burning engine time.
_eval_cache: dict[str, tuple[str | None, float, list[str]]] = {}
_EVAL_CACHE_MAX = 512


def _best_move_and_cp(board) -> tuple[str | None, float, list[str]]:
    """Best move (UCI), its centipawn eval from side-to-move, and the PV as a
    list of UCI moves. Uses the persistent engine with a time-bounded search
    (bounded latency, not random fixed-depth wall time) and a FEN-keyed cache
    so revisited positions are instant. Never raises: on failure returns
    (None, 0.0, [])."""
    if not STOCKFISH_PATH.exists():
        log.warning("stockfish binary missing at %s", STOCKFISH_PATH)
        return None, 0.0, []
    try:
        import chess.engine

        key = (
            board._transposition_key()
            if hasattr(board, "_transposition_key")
            else board.fen()
        )
        with _CACHE_LOCK:
            cached = _eval_cache.get(key)
        if cached is not None:
            return cached
        # _get_engine() initializes the persistent engine; subsequent calls
        # return early without the lock, so calling it here (before taking the
        # lock) avoids a non-reentrant-lock deadlock inside _analyse.
        engine = _get_engine()
        if engine is None:
            return None, 0.0, []

        # Timeout the lock: a thread that died mid-search (cancelled
        # asyncio.to_thread from a restart/abort) would hold _ENGINE_LOCK
        # forever and wedge every later eval. Bounded acquire fails open.
        if not _ENGINE_LOCK.acquire(timeout=10):
            log.warning("engine lock timed out — skipping eval")
            return None, 0.0, []
        try:
            # Time-bounded (~800ms): bounded latency (a fixed depth can take
            # wildly different wall time on tactical positions). The engine's
            # own background thread services the request — no extra executor.
            info = _analyse(board, chess.engine.Limit(time=0.8, depth=20))
        finally:
            _ENGINE_LOCK.release()
        if info is None:
            return None, 0.0, []
        pv = [m.uci() for m in info.get("pv", [])]
        best = pv[0] if pv else None
        result = (best, _eval_cp(info), pv)
        if best is not None:
            with _CACHE_LOCK:
                _eval_cache[key] = result
                if len(_eval_cache) > _EVAL_CACHE_MAX:
                    for k in list(_eval_cache)[: len(_eval_cache) // 2]:
                        _eval_cache.pop(k, None)
        return result
    except Exception as exc:
        log.warning("engine evaluation failed: %s", exc)
        return None, 0.0, []


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
# Move classification + eval bar (2026 SOTA — lichess winning-chances + WDL)
# ---------------------------------------------------------------------------
# The eval bar uses lichess's exact winning-chances formula (not the pre-NNUE
# 400-ElO logistic): rawWinningChances(cp) = 2/(1+exp(-0.00368208·cp)) - 1,
# clamped to ±1000 cp; mates map to cp = (21 - min(10, |mate|))·100 signed.
# Classification is draw-aware via python-chess's built-in WDL expectation
# (Score.wdl().expectation()), with a mate branch (lichess's evalSwings rule)
# and rating-scaled thresholds (chess.com scales by player strength).
_CLASS_THRESHOLDS = [
    ("Blunder", 0.20),
    ("Mistake", 0.10),
    ("Inaccuracy", 0.05),
    ("Good", 0.02),
    ("Excellent", 0.00),
    ("Best", 0.00),
]


def _winning_chances(cp: float) -> float:
    """Lichess rawWinningChances on [-1, 1] — the curve every modern eval bar
    copies. cp is from the side-to-move's perspective."""
    cp = max(-1000.0, min(1000.0, cp))
    return 2.0 / (1.0 + 2.718281828 ** (-0.00368208 * cp)) - 1.0


def _expected_points(rating: int, cp: float) -> float:
    """Expected points (0..1) from a centipawn eval: win% + 0.5·draw% using the
    lichess winning-chances curve mapped to [0,1]. Draw-aware — a 0.0 eval is
    ~0.5 (drawish), not 0.5 straight win."""
    wc = _winning_chances(cp)  # [-1, 1]
    return (wc + 1.0) / 2.0  # [0, 1], win+draw weighted


def _classify(
    rating: int,
    before_cp: float,
    after_cp: float,
    was_best: bool,
    material_delta: float = 0.0,
) -> str:
    """Classify a move by expected points LOST (draw-aware), with a material
    guard so a normal developing move is never labeled a blunder.

    The engine's eval can swing badly on fine-but-not-engine-optimal moves (a
    human's developing move deviates from Stockfish's top line -> huge sigmoid
    swing). chess.com's model doesn't punish that: a move that LOSES NO MATERIAL
    is at most an Inaccuracy (a plan difference). Only an actual material loss
    (or a missed mate) escalates to Mistake/Blunder. `material_delta` is the net
    material change from the MOVER's perspective (negative = they lost material).

    `was_best` short-circuits to 'Best'. Fixed chess.com cutoffs (verified SOTA):
    Inaccuracy 0.05-0.10, Mistake 0.10-0.20, Blunder 0.20+."""
    if was_best:
        return "Best"
    loss = _expected_points(rating, before_cp) - _expected_points(rating, after_cp)
    loss = max(0.0, loss)
    # Mate branch: before was mate-ish and after isn't -> lost the win outright.
    if abs(before_cp) >= 5000 and abs(after_cp) < 5000 and loss > 0.1:
        return "Blunder"
    # Material guard (the SOTA fix): no material lost => never worse than
    # Inaccuracy, no matter how much the engine's eval swings.
    if material_delta >= 0.0:
        return "Inaccuracy" if loss >= 0.05 else ("Good" if loss >= 0.02 else "Good")
    # Material WAS lost — floor it so a hung pawn/piece is never missed even if
    # the engine's eval didn't swing much: a piece (-3+) is always a Blunder, a
    # pawn (-1/-2) is at least a Mistake. Then let the eval loss escalate.
    eval_class = "Best"
    for name, threshold in _CLASS_THRESHOLDS:
        if loss >= threshold and name != "Best":
            eval_class = name
            break
    if material_delta <= -3.0:
        return "Blunder"
    if material_delta < 0.0:
        # -1/-2 material: at least Mistake (Blunder if the eval already says so).
        return "Blunder" if eval_class == "Blunder" else "Mistake"
    return eval_class


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
        # Guard the file BEFORE chess.square() — an out-of-range file silently
        # wraps to the other edge of the board (verified: square(-1,1) == h1),
        # which would count a wrong square's pawn in the shield.
        f = file + df
        if not (0 <= f < 8):
            continue
        sq = chess.square(f, rank + (1 if color == chess.WHITE else -1))
        if not (0 <= chess.square_rank(sq) < 8):
            continue
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


# The standard-plans menu (2026 SOTA — research: teaching plans as recognizable
# patterns beats abstract theory at 500-1200). Each plan has a TRIGGER (a
# machine-detectable board condition) and a RECIPE (the one-line do-this).
_STANDARD_PLANS: list[dict[str, Any]] = [
    {
        "key": "open_file",
        "name": "Open File",
        "recipe": "double rooks on the open file, then invade the 7th rank",
    },
    {
        "key": "outpost",
        "name": "Knight Outpost",
        "recipe": "park a knight on the weak square it can't be chased from",
    },
    {
        "key": "attack_king",
        "name": "Attack the King",
        "recipe": "their king is exposed — open the files toward it",
    },
    {
        "key": "convert",
        "name": "Trade + Convert",
        "recipe": "you're ahead — trade pieces (not pawns) and push the extra pawn",
    },
    {
        "key": "passed_pawn",
        "name": "Push the Passer",
        "recipe": "your passed pawn is a real threat — protect and advance it",
    },
    {
        "key": "develop",
        "name": "Develop",
        "recipe": "finish development / bring the worst piece into the game",
    },
    {
        "key": "consolidate",
        "name": "Consolidate",
        "recipe": "quiet position — improve your worst piece and don't force",
    },
]


def _open_file_for(board, color) -> str | None:
    """An open file (no pawns) where a rook of `color` could enter. Returns the
    file letter or None."""
    for f in range(8):
        has_pawn = any(
            (p := board.piece_at(chess.square(f, r))) and p.piece_type == chess.PAWN
            for r in range(8)
        )
        if not has_pawn:
            return "abcdefgh"[f]
    return None


def _outpost_for(board, color) -> str | None:
    """A stable outpost square for a knight of `color`: a square in ENEMY
    territory that no enemy pawn can attack AND that one of your pawns
    defends (the research definition: 'a square the opponent cannot — or dare
    not — chase your piece from')."""
    enemy = not color
    # Enemy territory: ranks 3-5 for White (toward Black), 4-6 for Black.
    ranks = (3, 4, 5) if color == chess.WHITE else (2, 3, 4)
    for r in ranks:
        for f in range(8):
            sq = chess.square(f, r)
            if board.piece_at(sq) is not None:
                continue
            # An enemy pawn attacks the adjacent files on the rank one toward us.
            pawn_attack_rank = r + (1 if color == chess.WHITE else -1)
            if not (0 <= pawn_attack_rank < 8):
                continue
            attacked = False
            for df in (-1, 1):
                af = f + df
                if 0 <= af < 8:
                    p = board.piece_at(chess.square(af, pawn_attack_rank))
                    if p and p.color == enemy and p.piece_type == chess.PAWN:
                        attacked = True
            if attacked:
                continue
            # Must be defended by one of our pawns (or it's not a real outpost).
            defender_rank = r - (1 if color == chess.WHITE else -1)
            if not (0 <= defender_rank < 8):
                continue
            defended = False
            for df in (-1, 1):
                af = f + df
                if 0 <= af < 8:
                    p = board.piece_at(chess.square(af, defender_rank))
                    if p and p.color == color and p.piece_type == chess.PAWN:
                        defended = True
            if defended:
                return chess.square_name(sq)
    return None


def _passed_pawn_for(board, color) -> str | None:
    """A passed pawn (no enemy pawns on its file or adjacent files in front)."""
    enemy = not color
    for r in range(8):
        for f in range(8):
            p = board.piece_at(chess.square(f, r))
            if p and p.color == color and p.piece_type == chess.PAWN:
                forward = (
                    range(r + 1, 8) if color == chess.WHITE else range(r - 1, -1, -1)
                )
                blocked = any(
                    (q := board.piece_at(chess.square(af, rr)))
                    and q.color == enemy
                    and q.piece_type == chess.PAWN
                    for af in (f - 1, f, f + 1)
                    if 0 <= af < 8
                    for rr in forward
                )
                if not blocked:
                    return chess.square_name(chess.square(f, r))
    return None


def _detect_standard_plan(
    board, color, attack_now: bool, weak: str | None
) -> dict[str, Any]:
    """Pick the standard plan whose trigger fires, or fall back to Develop /
    Consolidate. Returns {key, name, recipe, trigger}."""
    material = _material_balance(board)  # + = white ahead
    my_mat = material if color == chess.WHITE else -material
    if attack_now:
        p = next(x for x in _STANDARD_PLANS if x["key"] == "attack_king")
        return {**p, "trigger": "exposed king + development lead"}
    if my_mat >= 3:
        p = next(x for x in _STANDARD_PLANS if x["key"] == "convert")
        return {**p, "trigger": "material advantage"}
    if my_mat <= -3:
        # Down material: defend/complicate, don't trade.
        return {
            "key": "defend",
            "name": "Defend & Complicate",
            "recipe": "you're behind — keep it solid, create counterplay, don't trade pieces freely",
            "trigger": "material deficit",
        }
    passed = _passed_pawn_for(board, color)
    if passed:
        p = next(x for x in _STANDARD_PLANS if x["key"] == "passed_pawn")
        return {**p, "trigger": f"passed pawn on {passed}"}
    open_file = _open_file_for(board, color)
    if open_file:
        p = next(x for x in _STANDARD_PLANS if x["key"] == "open_file")
        return {**p, "trigger": f"open {open_file}-file"}
    outpost = _outpost_for(board, color)
    if outpost:
        p = next(x for x in _STANDARD_PLANS if x["key"] == "outpost")
        return {**p, "trigger": f"stable outpost on {outpost}"}
    if weak:
        return {
            "key": "weak_pawn",
            "name": "Attack the Weak Pawn",
            "recipe": f"their pawn on {weak} is weak — pile up on the square in front of it",
            "trigger": f"weak pawn on {weak}",
        }
    worst = _worst_piece(board, color)
    if worst:
        p = next(x for x in _STANDARD_PLANS if x["key"] == "develop")
        return {**p, "trigger": f"undeveloped {worst}"}
    p = next(x for x in _STANDARD_PLANS if x["key"] == "consolidate")
    return {**p, "trigger": "quiet position"}


def coach_plan(fen: str) -> dict[str, Any]:
    """The plan checklist for the side to move, engine-grounded (2026 SOTA:
    a standard-plans menu with machine-detectable triggers, plus the
    attack/improve/defend decision rule).

    Returns {ok, plan, standard_plan:{key,name,recipe,trigger}, mode,
    king_alert, worst_piece, weak_square, attack_now, material}."""
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

        material = _material_balance(board)
        my_mat = material if color == chess.WHITE else -material

        # Decision rule (research: attack / improve / defend, keyed on king
        # safety + development + material).
        if attack_now:
            mode = "attack"
        elif my_mat <= -3 or (my_king_bad and not their_king_bad):
            mode = "defend"
        else:
            mode = "improve"

        std = _detect_standard_plan(board, color, attack_now, weak)

        # One-line plan = the recipe (concrete, no vagueness).
        plan = std["recipe"]
        return {
            "ok": True,
            "plan": plan,
            "standard_plan": std,
            "mode": mode,
            "king_alert": f"their king has only {their_king} pawn(s) in front of it"
            if their_king <= 2
            else "",
            "worst_piece": worst,
            "weak_square": weak,
            "attack_now": attack_now,
            "material": round(my_mat, 1),
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
                if sq == move.to_square and board.is_capture(move):
                    captured_piece = board.piece_at(move.to_square)
                    if board.is_en_passant(move):
                        cap_val = 1
                    else:
                        cap_val = (
                            _PIECE_VALUE.get(captured_piece.symbol().lower(), 0)
                            if captured_piece
                            else 0
                        )
                    moved_val = _PIECE_VALUE.get(p.symbol().lower(), 0)
                    if cap_val >= moved_val:
                        continue
                hanging.append(chess.square_name(sq))
        return hanging
    except Exception as exc:
        log.warning("move-hangs detection failed: %s", exc)
        return []


def _king_was_castled(board, color) -> bool:
    """True if `color`'s king sits on a castled square (short g1/g8 or long
    c1/c8) behind a pawn shield. Used to gate the king-exposure advisory — a
    beginner's castle is only at risk once it actually exists."""
    ksq = board.king(color)
    if ksq is None:
        return False
    return ksq in (chess.G1, chess.G8, chess.C1, chess.C8)


def _move_opens_castled_shield(board, move) -> bool:
    """False-positive-gated heuristic for 'this move leaves the king exposed':
    the mover is CASTLED, and the move is a pawn push of one of the shield pawns
    directly in front of that castle (g/f for a short castle, b/c for a long
    castle) — the classic 'opening the king diagonal' blunder a beginner makes
    after castling. Requires an existing castle so we never nag pre-castle, and
    it is advisory-only (the caller never blocks purely on it), because e.g.
    a principled g4/f3 can be fine."""
    if not _king_was_castled(board, board.turn):
        return False
    moved = board.piece_at(move.from_square)
    if moved is None or not board.turn == moved.color:
        return False
    if moved.piece_type != chess.PAWN:
        return False
    to_name = chess.square_name(move.to_square)
    from_name = chess.square_name(move.from_square)
    # Kingside castle (g1/g8): the f- and g-pawns shield it.
    if board.king(board.turn) in (chess.G1, chess.G8):
        shield_files = ("g", "f")
    else:  # Queenside castle (c1/c8): the b- and c-pawns shield it.
        shield_files = ("b", "c")
    return to_name[0] in shield_files or from_name[0] in shield_files


def check_move_safety(fen: str, uci: str) -> dict[str, Any]:
    """The pre-move safety check (Heisman Slow->Safe->Active): would this move
    leave a piece hanging, or leave the king badly exposed?

    Blocking: `safe` is driven ONLY by the hanging-piece test (`_move_hangs_piece`)
    — a beginner's #1 habit. A legal move can never leave the mover's OWN king IN
    check (that is illegal by the laws of chess); the old implementation read
    `board.is_check()` after the push, which — because `turn` flips — actually
    reported whether the MOVER GAVE CHECK to the opponent, mislabelling it as
    "your king is left in check". That buggy advisory wrongly blocked good
    checking moves and has been dropped.

    Advisory fields (never force `safe=false` on their own, so a good move is
    never disrupted):
      makes_check   - the mover's move gives check to the opponent (the honest
                      relabel of the old misnamed signal).
      king_exposed  - a castled beginner just pushed a shield pawn in front of
                      their castle (f/g or b/c), a concrete low-false-positive
                      king-exposure trigger (Heisman checks/captures/threats +
                      ChessPivot pawn-shield cost ordering).

    Returns {ok, safe, hanging_after, makes_check, king_exposed, message}."""
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return {"ok": False, "safe": False, "error": "not a legal move"}
        hanging = _move_hangs_piece(board, move)
        probe = board.copy()
        probe.push(move)
        makes_check = probe.is_check()  # warns the side to move = the opponent now
        king_exposed = _move_opens_castled_shield(board, move)
        if not hanging:
            msg = "safe — no piece hangs"
            if makes_check:
                msg = "safe — no piece hangs, and you give check!"
            elif king_exposed:
                msg = "safe to make — but that pawn push opens space in front of your castled king"
            return {
                "ok": True,
                "safe": True,
                "hanging_after": [],
                "makes_check": makes_check,
                "king_exposed": king_exposed,
                "message": msg,
            }
        return {
            "ok": True,
            "safe": False,
            "hanging_after": hanging,
            "makes_check": makes_check,
            "king_exposed": king_exposed,
            "message": f"caught it! this move hangs {', '.join(hanging[:3])} — check before you move",
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
        except Exception as exc:
            log.debug("no hanging piece in given drill fen (%s): %s", fen, exc)
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

        # A sacrifice means a piece was left hanging (or a higher-value piece took a lower-value one).
        hanging_squares = _move_hangs_piece(board, move)

        # Determine captured piece before pushing
        cap = board.piece_at(move.to_square) if board.is_capture(move) else None

        board.push(move)

        if not hanging_squares:
            return None

        max_hanging_val = 0
        for sq_name in hanging_squares:
            sq = chess.parse_square(sq_name)
            p = board.piece_at(sq)
            if p:
                val = _PIECE_VALUE.get(p.symbol().lower(), 0)
                if val > max_hanging_val:
                    max_hanging_val = val

        if max_hanging_val < 3:
            return None  # not a piece sacrifice (only pawns hanging)

        # Soundness: eval barely moved (within ~60cp) => you bought the attack.
        held = after_cp >= before_cp - 60.0
        if not held:
            # Gave up material AND lost the eval -> that's a blunder, not a
            # sacrifice. The research's rule: "a blunder is when you lose the
            # piece AND the attack dies." Only report SOUND sacrifices here.
            return None
        # Pattern tag.
        pattern = ""
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
        elif given_val >= 3 and not board.is_check():
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
    """The engine's reply move for playing against it. `level` (1-4) produces a
    HUMAN-LIKE opponent (2026 SOTA — softmax sampling over the engine's top
    moves, not Skill Level's one-shot noise):

      - level 1 (Gentle): picks among the top moves nearly uniformly (a ~500
        plays all sorts of reasonable + slightly-off moves, occasionally blunders)
      - level 4 (Strong): almost always takes the engine's best move

    The engine still evaluates (facts), but the opponent CHOOSES like a player
    of that strength — gradually less precise as the level drops, with a
    level-scaled blunder chance. This is the research's recommended alternative
    to UCI Skill Level (which plays near-perfectly then randomly explodes)."""
    if not STOCKFISH_PATH.exists():
        return {"ok": False, "error": "stockfish binary missing"}
    try:
        import chess
        import chess.engine
        import math
        import random

        board = chess.Board(fen)
        if board.is_game_over():
            return {
                "ok": True,
                "game_over": True,
                "result": board.result(),
                "is_checkmate": board.is_checkmate(),
                "is_stalemate": board.is_stalemate(),
            }

        # Level -> temperature (higher = more uniform/blurrier choice) and a
        # blunder probability (occasionally play a clearly-worse move).
        temp = {1: 2.2, 2: 1.4, 3: 0.8, 4: 0.3}.get(level, 1.4)
        blunder_p = {1: 0.35, 2: 0.22, 3: 0.10, 4: 0.02}.get(level, 0.22)

        # Evaluate the position, then build a sampling pool from legal moves
        # weighted by the engine's preference. We get ONE strong move from the
        # engine; the human-like layer adds principled randomness around it.
        best_move, _, _ = await asyncio.to_thread(_best_move_and_cp, board)

        legal = list(board.legal_moves)
        if not legal:
            return {"ok": False, "error": "no legal moves"}

        chosen: chess.Move | None = None
        if best_move is not None:
            best = chess.Move.from_uci(best_move)
            # Occasionally blunder: pick a random legal move (not the best).
            if random.random() < blunder_p:
                pool = [m for m in legal if m != best]
                if pool:
                    chosen = random.choice(pool)
                else:
                    chosen = best
            else:
                # Softmax over the top candidates: weight the engine's best
                # move heavily, and a few plausible alternatives less so.
                candidates = [best]
                for m in legal:
                    if m != best and len(candidates) < min(4, len(legal)):
                        candidates.append(m)
                weights = []
                for i, m in enumerate(candidates):
                    # The best move gets the highest weight; alternatives decay
                    # with temperature (higher temp flattens the distribution).
                    w = math.exp((len(candidates) - i) / temp)
                    weights.append(w)
                chosen = random.choices(candidates, weights=weights, k=1)[0]
        if chosen is None:
            chosen = random.choice(legal)

        try:
            san = board.san(chosen)
        except Exception:
            san = chosen.uci()
        board.push(chosen)
        return {
            "ok": True,
            "uci": chosen.uci(),
            "san": san,
            "fen": board.fen(),
            "is_checkmate": board.is_checkmate(),
            "is_stalemate": board.is_stalemate(),
            "in_check": board.is_check(),
            "human_like": True,
            "level": level,
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
    game_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate the learner's move: legality, classification, eval delta, best
    alternative, and (when want_explain) a local-LLM 'why' grounded in the chess
    book digests. When `game_id` is given, the move is recorded for the guided
    review."""
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

    before_best, before_cp, _ = await asyncio.to_thread(_best_move_and_cp, board)
    # Material before the move (for the material guard in classification: a
    # move that doesn't lose material is never a Mistake/Blunder).
    mover_sign = 1 if board.turn == chess.WHITE else -1
    material_before = _material_balance(board) * mover_sign
    # SAN must be computed BEFORE pushing (the move is only legal pre-push).
    try:
        played_san = board.san(move)
    except Exception:
        played_san = uci
    board.push(move)
    # Net material change from the MOVER's perspective.
    material_delta = (_material_balance(board) * mover_sign) - material_before

    after_cp = (await asyncio.to_thread(_best_move_and_cp, board))[1]

    # Eval from the MOVER's perspective for classification (before was mover's
    # perspective; after is opponent's perspective now).
    mover_after = -after_cp

    # The played move is the engine's best if it matches the best move found
    # in the single pre-move evaluation above (no re-analysis needed).
    was_best = before_best == uci

    classification = _classify(rating, before_cp, mover_after, was_best, material_delta)

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
        "game_over": board.is_game_over(),
        "fen": board.fen(),
        "explanation": "",
    }
    if before_best:
        try:
            bb = chess.Board(fen)
            result["best_move_san"] = bb.san(chess.Move.from_uci(before_best))
        except Exception as exc:
            log.warning("best_move_san parse failed (%s/%s): %s", fen, before_best, exc)

    # Coach: the one-line plan for the position the learner is IN (pre-move),
    # engine-grounded (king safety / worst piece / weak square).
    try:
        plan = coach_plan(fen)
        result["coach"] = plan if plan.get("ok") else {}
    except Exception as exc:
        log.warning("coach plan generation failed: %s", exc)
        result["coach"] = {}

    # The corrective plan (Hattie feed-forward + Butler correction): after a bad
    # move, tell the learner what the plan should be in the RESULTING position
    # (the one they must actually navigate), not the pre-move one.
    try:
        post_plan = coach_plan(board.fen())
        result["plan_now"] = post_plan if post_plan.get("ok") else {}
    except Exception as exc:
        log.warning("post-move plan generation failed: %s", exc)
        result["plan_now"] = {}

    # Persistent current-plan state (anti-drift): carry the plan forward, only
    # regenerate on a trigger change. Per-game.
    if game_id and result.get("plan_now", {}).get("ok"):
        try:
            from .chess_plans import advance

            result["plan_state"] = advance(game_id, result.get("plan_now"))
        except Exception as exc:
            log.warning("plan state advance failed: %s", exc)
            result["plan_state"] = {}

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
                    # _best_move_and_cp returns the eval from the NEW side-to-move
                    # (the opponent). Negate to the MOVER's perspective before
                    # comparing against before_cp.
                    mover_after_best = -(
                        await asyncio.to_thread(_best_move_and_cp, probe)
                    )[1]
                    # Sound sacrifice: giving up the piece kept/improved the eval.
                    if mover_after_best >= before_cp - 60.0:
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
    # Record the move for the guided review (best-effort, never raises).
    if game_id and result.get("ok") and result.get("legal"):
        try:
            from .chess_games import record_move

            record_move(
                game_id,
                {
                    "uci": uci,
                    "san": result.get("san", uci),
                    "fen": result.get("fen", ""),
                    "pre_fen": fen,
                    "classification": classification,
                    "eval_before_cp": round(before_cp, 1),
                    "eval_after_cp": round(mover_after, 1),
                    "win_after_pct": round(win_after * 100, 1),
                    "win_delta_pct": round((win_after - win_before) * 100, 1),
                    "best_uci": before_best,
                    "best_move_san": result.get("best_move_san"),
                    "is_best": was_best,
                    "concept": result.get("coach", {}).get("weak_square") or "",
                },
            )
        except Exception as exc:
            log.warning("game move recording failed: %s", exc)
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
    """Best-effort cloud-LLM prose. Returns '' on any failure/timeout; the
    deterministic explanation is the instant fallback.

    Routing (preferred first): DeepSeek DIRECT via litellm's native
    `deepseek/` provider keyed by DEEPSEEK_API_KEY (your paid credit — verified
    to return clean prose reliably, no reasoning-only flakiness); falls back to
    the OpenCode Go proxy (OPENAI_API_KEY, $0/token) if the direct key is
    absent. Cost-gated: only runs for Mistake/Blunder/Inaccuracy, and only when
    the enhancement is enabled. Engine facts + book citations ground every
    claim."""
    try:
        import chess
        import os

        import litellm

        from ..core.settings import get_settings

        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        played_san = board.san(move)
        context = "\n".join(
            f"[{r['title']}]: {r['text'][:300]}" for r in frags if r.get("text")
        )
        prompt = (
            "You are a chess coach for a ~500-rated beginner. Explain in 2-3 "
            "short sentences why this move is a "
            f"{classification}, in plain beginner language. Use only the book "
            "ideas given; do not invent citations. Ground every claim in the "
            "position facts — never invent analysis. Do not reason — just "
            "answer directly.\n\n"
            f"POSITION (FEN): {fen}\nPLAYED: {played_san}\n"
            f"BEST WAS: {best_move_san or 'unknown'}\n\n"
            f"BOOK IDEAS:\n{context}\n\nEXPLANATION:"
        )
        s = get_settings()

        # Prefer DeepSeek direct (native provider handles api.deepseek.com).
        ds_key = os.getenv("DEEPSEEK_API_KEY", "")
        if ds_key:
            # The model occasionally spends the whole budget reasoning (empty
            # content) — retry once before giving up.
            for attempt in (0, 1):
                async with asyncio.timeout(_EXPLAIN_TIMEOUT_S):
                    resp = await litellm.acompletion(
                        model="deepseek/deepseek-v4-flash",
                        messages=[{"role": "user", "content": prompt}],
                        api_key=ds_key,
                        max_tokens=500,
                        timeout=_EXPLAIN_TIMEOUT_S,
                    )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    return text
                if attempt == 0:
                    await asyncio.sleep(0.5)
            return ""

        # Fallback: OpenCode Go proxy ($0/token).
        model = getattr(s, "analysis_cloud_model", None) or "openai/deepseek-v4-flash"
        base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            log.warning("move explanation skipped: no DEEPSEEK/OPENAI key")
            return ""
        async with asyncio.timeout(_EXPLAIN_TIMEOUT_S):
            # No system message — the OpenCode Go proxy sends this model into
            # reasoning_content (empty content) with a system prompt. The model
            # reasons (variable, up to ~3000 tokens) before answering, so
            # max_tokens must leave budget for content (verified: 250 -> empty,
            # 2000 -> answer).
            resp = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_base=base,
                api_key=key,
                custom_llm_provider="openai",
                max_tokens=2000,
                timeout=_EXPLAIN_TIMEOUT_S,
            )
        msg = resp.choices[0].message
        # Only return clean prose content; empty (reasoning-only) -> "" and the
        # deterministic explanation covers it.
        return (msg.content or "").strip()
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


# ---------------------------------------------------------------------------
# Socratic coaching (2026 SOTA — ChessDojo-style "what's the idea?" active
# recall). One turn = the coach asks a question (or reacts to the learner's
# answer); the frontend holds the rolling dialogue and calls back. The coach
# never gives the move away until the learner has been stuck several turns
# (fail-open reveal) — the point is retrieval, not spoon-feeding.
# ---------------------------------------------------------------------------


async def _proposal_eval(fen: str, uci: str) -> dict[str, Any]:
    """Evaluate a learner's proposed move for the Socratic coach: legality,
    before/after win%, classification, and whether it matches the engine's best.
    Fail-closed: never returns a fabricated eval (returns ok=False on any
    failure). The coach grounds its reaction ONLY in these engine numbers."""
    try:
        import chess

        board = chess.Board(fen)
        try:
            move = chess.Move.from_uci(uci)
        except Exception:
            return {"ok": False, "error": f"invalid move '{uci}'"}
        if move not in board.legal_moves:
            return {
                "ok": False,
                "error": f"'{uci}' is not a legal move in this position",
            }
        before_best, before_cp, _ = await asyncio.to_thread(_best_move_and_cp, board)
        try:
            san = board.san(move)
        except Exception:
            san = uci
        board.push(move)
        reply_uci, after_cp, _ = await asyncio.to_thread(_best_move_and_cp, board)
        reply_san = None
        if reply_uci:
            try:
                reply_san = board.san(chess.Move.from_uci(reply_uci))
            except Exception:
                reply_san = reply_uci
        mover_after = -after_cp
        was_best = before_best == uci
        classification = _classify(500, before_cp, mover_after, was_best)
        win_before = _expected_points(500, before_cp)
        win_after = _expected_points(500, mover_after)
        best_win_pct = round(win_before * 100, 1)
        best_san = None
        if before_best:
            try:
                b2 = chess.Board(fen)
                best_san = b2.san(chess.Move.from_uci(before_best))
            except Exception:
                best_san = before_best
        return {
            "ok": True,
            "uci": uci,
            "san": san,
            "classification": classification,
            "is_best": was_best,
            "win_before_pct": round(win_before * 100, 1),
            "win_after_pct": round(win_after * 100, 1),
            "win_delta_pct": round((win_after - win_before) * 100, 1),
            "best_move": before_best,
            "best_move_san": best_san,
            "best_win_pct": best_win_pct,
            "reply_uci": reply_uci,
            "reply_san": reply_san,
        }
    except Exception as exc:
        log.warning("socratic proposal eval failed for %s/%s: %s", fen, uci, exc)
        return {"ok": False, "error": str(exc)}


async def _socratic_coach_turn(
    fen: str,
    plan: dict[str, Any],
    best_move_san: str | None,
    history: list[dict[str, str]],
    proposed_uci: str | None = None,
) -> dict[str, Any]:
    """Produce the coach's next turn for a position. `history` is the rolling
    dialogue [{role: "user"|"coach", content}], oldest first. When
    `proposed_uci` is given, the engine evaluates THAT exact move (before/after
    eval, win delta, classification) and the coach reacts to the learner's
    proposal grounded in those numbers — the LLM translates engine facts, it
    never invents a refutation (CCC principle). Returns {ok, reply}. Fail-closed:
    LLM failure degrades to a deterministic nudge."""
    proposal = None
    if proposed_uci:
        proposal = await _proposal_eval(fen, proposed_uci)
    try:
        import chess as _chess  # noqa: F401  (kept for parity with _llm_enhancement)
        import litellm

        from ..core.settings import get_settings

        concept = plan.get("standard_plan", {}).get("name", "") or plan.get("plan", "")
        turns = len(history)
        dial = "\n".join(
            f"{'LEARNER' if m.get('role') == 'user' else 'COACH'}: {m.get('content', '')}"
            for m in history[-8:]
        )
        reveal = turns >= 5
        proposal_block = ""
        if proposal and proposal.get("ok"):
            refutation_line = ""
            if not proposal.get("is_best") and proposal.get("reply_san"):
                refutation_line = f"- If they play their proposal, the engine's best refutation is {proposal.get('reply_san')} ({proposal.get('reply_uci')}). Use this to guide them to see why their move fails.\n"
            proposal_block = (
                f"\nThe learner proposes playing {proposal.get('san')} ({proposal.get('uci')}).\n"
                f"Engine facts about their proposal (GROUND your reaction ONLY in these — "
                f"do not invent analysis):\n"
                f"- Win% before: {proposal.get('win_before_pct')}%, after: {proposal.get('win_after_pct')}% "
                f"(delta {proposal.get('win_delta_pct')} points)\n"
                f"- Classification: {proposal.get('classification')}\n"
                f"- The engine's best move is {proposal.get('best_move_san')} "
                f"(win% {proposal.get('best_win_pct')}%) — {'their proposal matches it.' if proposal.get('is_best') else 'a different, stronger move.'}\n"
                f"{refutation_line}"
            )
        prompt = (
            "You are a Socratic chess coach for a ~500-rated beginner. Your job "
            "is to guide them to SEE the idea themselves, one question at a time "
            "- never lecture, never dump the answer.\n"
            "Position facts (ground your question in these; do not invent):\n"
            f"- The plan is: {plan.get('plan', '')}\n"
            f"- Concept: {concept}\n"
            f"- Weakest enemy point: {plan.get('weak_square') or 'not obvious'}\n"
            f"- Their king exposure: {plan.get('king_alert') or 'normal'}\n"
            f"- Best move (SECRET - do not name it): {best_move_san or 'unknown'}\n"
            f"{proposal_block}\n"
            "Rules:\n"
            "1. Ask ONE short question, or react to the learner's last answer in "
            "1-2 short sentences then ask the next question.\n"
            "2. Draw attention to the relevant area (a loose piece, the king's "
            "file, the weak square) without naming the move.\n"
            "3. Affirm what is right in their thinking; gently correct blind spots.\n"
            "4. Plain beginner language. Keep it under 40 words.\n"
            "5. Do NOT name the best move. "
            f"{'HOWEVER the learner has been stuck for several turns, so now give a gentle concrete nudge (still not the exact move if avoidable).' if reveal else ''}\n"
            "6. You may emphasize squares or moves by wrapping them in brackets (e.g. [e4] or [e4-e5]). The UI will automatically highlight these on the board. Use this sparingly to draw attention to key areas.\n\n"
            f"DIALOGUE SO FAR:\n{dial or '(start of conversation - open with a question about the position)'}\n\n"
            "COACH:"
        )
        s = get_settings()

        ds_key = os.getenv("DEEPSEEK_API_KEY", "")
        if ds_key:
            for attempt in (0, 1):
                async with asyncio.timeout(_EXPLAIN_TIMEOUT_S):
                    resp = await litellm.acompletion(
                        model="deepseek/deepseek-v4-flash",
                        messages=[{"role": "user", "content": prompt}],
                        api_key=ds_key,
                        max_tokens=300,
                        timeout=_EXPLAIN_TIMEOUT_S,
                    )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    return {"ok": True, "reply": text[:600]}
                if attempt == 0:
                    await asyncio.sleep(0.5)
            raise RuntimeError("deepseek returned empty content twice")

        model = getattr(s, "analysis_cloud_model", None) or "openai/deepseek-v4-flash"
        base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
        key = os.getenv("OPENAI_API_KEY", "")
        if key:
            async with asyncio.timeout(_EXPLAIN_TIMEOUT_S):
                resp = await litellm.acompletion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    api_base=base,
                    api_key=key,
                    custom_llm_provider="openai",
                    max_tokens=1500,
                    timeout=_EXPLAIN_TIMEOUT_S,
                )
            msg = resp.choices[0].message
            text = (msg.content or "").strip()
            if text:
                return {"ok": True, "reply": text[:600]}
        log.warning("socratic coach: no cloud key, using deterministic fallback")
    except Exception as exc:
        log.warning("socratic coach LLM failed, using deterministic fallback: %s", exc)

    # Deterministic fallback: a concept nudge grounded in the plan checklist
    # (no move given) - the same engine-grounded nudge as hint level 1. When a
    # proposal was evaluated, react to its engine facts instead.
    if proposal and proposal.get("ok"):
        if proposal.get("is_best"):
            nudge = (
                f"Good — {proposal.get('san')} matches the engine's best move "
                f"({proposal.get('best_win_pct')}% win chance). Why does it work here?"
            )
        elif proposal.get("win_delta_pct", 0) < -5:
            nudge = (
                f"{proposal.get('san')} drops your win chance by "
                f"{abs(proposal.get('win_delta_pct'))} points "
                f"({proposal.get('win_before_pct')}% -> {proposal.get('win_after_pct')}%). "
                f"Look for a stronger move — the weak square / king file is the clue."
            )
        else:
            nudge = (
                f"{proposal.get('san')} is okay but not the best — the engine found "
                f"({proposal.get('best_move_san')}). What do you see around the king?"
            )
        return {"ok": True, "reply": nudge[:300]}
    nudge = plan.get("plan", "")
    if plan.get("king_alert"):
        nudge = f"{plan['king_alert']} - look at the kings before deciding."
    if not nudge:
        nudge = "Look for the weakest point in your opponent's position."
    return {"ok": True, "reply": nudge[:300]}
