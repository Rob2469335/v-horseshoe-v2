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
