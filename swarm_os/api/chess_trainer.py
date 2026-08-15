"""Chess trainer routes — the Command Center's practice board.

Endpoints:
- GET    /chess/trainer/health        — engine + book-index availability.
- POST   /chess/trainer/evaluate      — evaluate a learner's move (legal-move
  check, Stockfish 18 classification, eval delta, best move, book-grounded
  explanation).
- POST   /chess/trainer/engine-move   — the engine's reply move (for playing
  against it).
- POST   /chess/trainer/index-books   — (re)build the Qdrant chess-book index.
- GET    /chess/trainer/practice      — curated practice positions (lichess
  practice-style chapters for a ~500 player).

Evaluation is the core loop: python-chess validates, Stockfish 18 evaluates
before/after + best move, the expected-points model classifies (rating-scaled,
chess.com SOTA), and the explanation is deterministic-first + Qdrant-book-
grounded with a best-effort local-LLM enhancement.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chess/trainer", tags=["chess-trainer"])

# Practice positions: curated starting points for a ~500-rated player (lichess
# practice-style chapters). FEN + a one-line goal. Positions are well-known.
PRACTICE_POSITIONS: list[dict[str, Any]] = [
    {
        "slug": "king-queen-mate",
        "name": "Mate with king + queen",
        "goal": "White to move — checkmate with king and queen.",
        "fen": "8/8/8/8/8/8/k7/QK6 w - - 0 1",
        "tier": 1,
    },
    {
        "slug": "back-rank-mate",
        "name": "Back-rank mate",
        "goal": "White to move — exploit the back rank.",
        "fen": "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1",
        "tier": 1,
    },
    {
        "slug": "fork-practice",
        "name": "Knight fork",
        "goal": "White to move — a knight fork wins material.",
        "fen": "r2q1rk1/ppp2ppp/8/8/8/5N2/PPPP1PPP/R1BQ1RK1 w - - 0 1",
        "tier": 2,
    },
    {
        "slug": "scholars-defense",
        "name": "Scholar's-mate defense",
        "goal": "Black to move — this position is reached after 1.e4 e5 2.Qh5; defend properly.",
        "fen": "r1bqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR b KQkq - 1 2",
        "tier": 1,
    },
    {
        "slug": "lucena",
        "name": "Lucena position",
        "goal": "White to move — the classic rook-endgame win. (Advanced.)",
        "fen": "5K2/4P3/8/8/8/8/2r5/3k4 w - - 0 1",
        "tier": 4,
    },
]


class EvaluateMoveRequest(BaseModel):
    fen: str = Field(..., description="Current position FEN")
    uci: str = Field(..., description="The move the learner played, UCI notation")
    rating: int = Field(500, ge=100, le=2500)
    want_explain: bool = Field(True)


class EngineMoveRequest(BaseModel):
    fen: str = Field(..., description="Current position FEN")
    rating: int = Field(500, ge=100, le=2500)
    level: int = Field(
        1, ge=1, le=4, description="Engine strength (1=weak, 4=strong) for a fair game"
    )


@router.get("/health")
async def trainer_health() -> dict[str, Any]:
    """Engine + book-index availability (fail-closed: each reports false on
    any failure, never raises)."""
    from ..services import chess_book_memory, chess_trainer

    import os

    from qdrant_client import QdrantClient

    engine_ok = os.path.exists(str(chess_trainer.STOCKFISH_PATH))
    books_ok = False
    try:
        client = QdrantClient(
            url=chess_book_memory.QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY")
        )
        cols = client.get_collections()
        books_ok = any(
            getattr(c, "name", None) == chess_book_memory.COLLECTION
            for c in cols.collections
        )
    except Exception:
        books_ok = False
    return {
        "ok": True,
        "engine": {"available": engine_ok, "path": str(chess_trainer.STOCKFISH_PATH)},
        "book_index": {
            "available": books_ok,
            "collection": chess_book_memory.COLLECTION,
        },
        "practice_positions": len(PRACTICE_POSITIONS),
    }


@router.post("/evaluate")
async def trainer_evaluate(req: EvaluateMoveRequest) -> dict[str, Any]:
    """Evaluate the learner's move. Runs the engine (on a worker thread) so it
    never blocks the event loop; explanation is deterministic-first so it never
    hangs on the LLM."""
    from ..services.chess_trainer import evaluate_move

    try:
        return await evaluate_move(
            req.fen, req.uci, rating=req.rating, want_explain=req.want_explain
        )
    except Exception as exc:
        log.warning("chess evaluate failed: %s", exc)
        raise HTTPException(status_code=503, detail="engine evaluation failed")


@router.post("/engine-move")
async def trainer_engine_move(req: EngineMoveRequest) -> dict[str, Any]:
    """The engine's reply. `level` weakens the engine for a fair game vs a
    beginner (Skill Level in UCI units) so it blunders like a ~500-rated
    opponent instead of crushing every move."""
    from ..services.chess_trainer import engine_reply

    try:
        return await engine_reply(req.fen, rating=req.rating, level=req.level)
    except Exception as exc:
        log.warning("chess engine-move failed: %s", exc)
        raise HTTPException(status_code=503, detail="engine move failed")


@router.post("/index-books")
async def trainer_index_books(force: bool = False) -> dict[str, Any]:
    """Build/refresh the Qdrant chess-book index (idempotent)."""
    from ..services.chess_book_memory import index_books

    return await index_books(force=force)


@router.get("/practice")
async def trainer_practice() -> dict[str, Any]:
    """Curated practice positions for a ~500 player."""
    return {
        "ok": True,
        "count": len(PRACTICE_POSITIONS),
        "positions": PRACTICE_POSITIONS,
    }


class ReviewResolveRequest(BaseModel):
    entry_id: str = Field(..., description="The mistake entry id")


@router.get("/review")
async def trainer_review(limit: int = 10, box: int | None = None) -> dict[str, Any]:
    """Due learn-from-mistakes positions (spaced ladder), oldest box first.
    Each entry is a position the learner blundered, to be solved as 'find the
    better move'."""
    from ..services.chess_mistakes import review_due

    return review_due(limit=limit, box=box)


@router.post("/review/solved")
async def trainer_review_solved(req: ReviewResolveRequest) -> dict[str, Any]:
    """Mark a review position solved: advance its spaced-repetition box (or
    retire it past the ladder)."""
    from ..services.chess_mistakes import mark_solved

    return mark_solved(req.entry_id)


@router.post("/review/failed")
async def trainer_review_failed(req: ReviewResolveRequest) -> dict[str, Any]:
    """Mark a review position failed: reset it to box 0 (due tomorrow)."""
    from ..services.chess_mistakes import mark_failed

    return mark_failed(req.entry_id)


@router.get("/review/stats")
async def trainer_review_stats() -> dict[str, Any]:
    from ..services.chess_mistakes import stats

    return stats()
