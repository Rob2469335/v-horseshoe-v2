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
    game_id: str | None = Field(
        None, description="Active game id to record this move into"
    )


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


@router.get("/tips")
async def trainer_tips(count: int = 10, seed: int | None = None) -> dict[str, Any]:
    """Ten chess tips drawn from the 100-book chess library. Rotates per load
    (seeded/random) so each page refresh surfaces fresh, book-grounded advice.
    Fail-closed: curated tips always return, even if the manifest is missing."""
    from ..services.books_service import get_books_service

    count = max(1, min(int(count), 20))
    return get_books_service().get_chess_tips(count=count, seed=seed)


@router.post("/evaluate")
async def trainer_evaluate(req: EvaluateMoveRequest) -> dict[str, Any]:
    """Evaluate the learner's move. Runs the engine (on a worker thread) so it
    never blocks the event loop; explanation is deterministic-first so it never
    hangs on the LLM."""
    from ..services.chess_trainer import evaluate_move

    try:
        return await evaluate_move(
            req.fen,
            req.uci,
            rating=req.rating,
            want_explain=req.want_explain,
            game_id=req.game_id,
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


class CoachHintRequest(BaseModel):
    fen: str = Field(..., description="Current position FEN (side to move)")


@router.post("/coach/hint")
async def trainer_coach_hint(req: CoachHintRequest) -> dict[str, Any]:
    """The on-demand coach hint for the side to move (Play-Coach escalation).
    Level 1 = a concept nudge from the 3-question plan checklist (no move
    given); the frontend escalates to the best-move arrow and then the move.
    Research-grounded: actionable hints beat vague praise; escalation preserves
    the retrieval-practice benefit while rescuing a stuck learner."""
    from ..services.chess_trainer import coach_plan

    plan = coach_plan(req.fen)
    if not plan.get("ok"):
        raise HTTPException(status_code=503, detail="coach hint unavailable")
    # Build a concept nudge (level 1) without revealing the move.
    nudge = plan.get("plan", "")
    if plan.get("attack_now"):
        nudge = "Do you see how to use their exposed king? (think: open the files)"
    elif plan.get("king_alert"):
        nudge = f"{plan['king_alert']} — pieces pointing at a bare king are worth more than pawns"
    plan["hint_level_1"] = nudge
    plan["hint_level_2"] = (
        "Look for a forcing move — a check, capture, or a sacrifice that opens their position"
    )
    return plan


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


@router.get("/review/top")
async def trainer_review_top(limit: int = 10) -> dict[str, Any]:
    """The TOP recurring mistakes — the concepts you keep making, ranked by
    frequency, with example (played -> better) moves for each. This is the
    'what should I actually drill' summary, distilled from the raw queue."""
    from ..services.chess_mistakes import get_recurring_mistakes

    return get_recurring_mistakes(limit=limit)


@router.get("/review/coach")
async def trainer_review_coach() -> dict[str, Any]:
    """The personalized coach report: skill bars (tactics / positional /
    defense / calculation / endgame) built from your recurring mistake types,
    your top error concepts, and a 'today's focus' coaching line. This is the
    personal-curriculum seed."""
    from ..services.chess_mistakes import coach_report

    return coach_report()


class TrainingAnswerRequest(BaseModel):
    item_id: str = Field(..., description="The training item to record")
    correct: bool = Field(..., description="Was the solution correct?")
    confidence: str | None = Field(
        None, description="guess | idea | confident (for calibration)"
    )
    confidence_captured_at: float | None = Field(
        None,
        description="Client timestamp (epoch seconds) when the user SELECTED "
        "confidence — must be BEFORE the answer was submitted (calibration "
        "invariant: confidence_captured_at <= answer_recorded_at).",
    )


@router.get("/review/training")
async def trainer_training(
    limit: int = 10, concept: str | None = None
) -> dict[str, Any]:
    """Concept-level spaced-repetition training items (Repair / Reinforce /
    Transfer). Prioritizes your weakest concept first."""
    from ..services import chess_training

    return chess_training.training_due(limit=limit, concept=concept)


@router.post("/review/training/build")
async def trainer_training_build(force: bool = False) -> dict[str, Any]:
    """(Re)build training items from your mistakes + GM critical moments."""
    from ..services import chess_training

    own = chess_training.build_items_from_mistakes(force=force)
    gm = chess_training.build_items_from_gm(force=force)
    return {
        "ok": True,
        "own_game": own,
        "gm": gm,
        "progress": chess_training.concept_progress(),
    }


@router.post("/review/training/answer")
async def trainer_training_answer(req: TrainingAnswerRequest) -> dict[str, Any]:
    """Record a training answer and advance/fall back the item's box."""
    from ..services import chess_training

    return chess_training.record_answer(
        req.item_id, req.correct, req.confidence, req.confidence_captured_at
    )


@router.get("/review/training/progress")
async def trainer_training_progress() -> dict[str, Any]:
    """Per-concept learning progress (Repair/Reinforce/Transfer counts,
    success rate, mastery)."""
    from ..services import chess_training

    return chess_training.concept_progress()


@router.get("/review/training/calibration")
async def trainer_training_calibration() -> dict[str, Any]:
    """Confidence calibration (analytics only): how well self-reported
    confidence (guess/idea/confident) matches ACTUAL solve rate, per concept
    and stage. Overconfidence flags a concept where 'confident' solves are
    rare. This NEVER drives scheduling — observed performance outranks
    self-report."""
    from ..services import chess_training

    return chess_training.calibration_report()


class SafetyCheckRequest(BaseModel):
    fen: str = Field(..., description="Current position FEN (side to move)")
    uci: str = Field(..., description="The move to check for safety")


class ThreatCheckRequest(BaseModel):
    fen: str = Field(..., description="Current position FEN")
    uci: str = Field(..., description="The last move played (to read its threats)")


@router.get("/drill/hanging")
async def trainer_drill_hanging(fen: str | None = None) -> dict[str, Any]:
    """A hanging-piece drill: a position where the side to move can capture a
    loose enemy piece. Returns the FEN + the found loose pieces (the learner
    must spot + take one)."""
    from ..services.chess_trainer import hanging_drill

    return hanging_drill(fen)


@router.post("/safety")
async def trainer_safety(req: SafetyCheckRequest) -> dict[str, Any]:
    """The pre-move safety check (Heisman Slow->Safe->Active): would this move
    leave a piece hanging or the king exposed? The learner confirms this BEFORE
    the trainer accepts the move."""
    from ..services.chess_trainer import check_move_safety

    return check_move_safety(req.fen, req.uci)


@router.post("/threats")
async def trainer_threats(req: ThreatCheckRequest) -> dict[str, Any]:
    """'Looking for Trouble': what did the last move threaten? Lists enemy
    pieces now attacked and undefended."""
    from ..services.chess_trainer import threats_from_move

    return threats_from_move(req.fen, req.uci)


class GameIdRequest(BaseModel):
    game_id: str | None = Field(
        None, description="Game id (defaults to the most recent)"
    )


@router.post("/game/start")
async def trainer_game_start() -> dict[str, Any]:
    """Start a new recorded game. Returns the game id to pass to /evaluate."""
    from ..services.chess_games import start_game

    return start_game()


@router.post("/game/review")
async def trainer_game_review(req: GameIdRequest) -> dict[str, Any]:
    """Finalize + review a game: eval curve, key moments, per-phase accuracy,
    and the queue-mistakes payload."""
    from ..services.chess_games import finish_game

    return finish_game(req.game_id or "")


@router.post("/game/queue-mistakes")
async def trainer_game_queue_mistakes(req: GameIdRequest) -> dict[str, Any]:
    """Queue every mistake/blunder of the game into the spaced-repetition store."""
    from ..services.chess_games import queue_game_mistakes

    return queue_game_mistakes(req.game_id)


@router.get("/games")
async def trainer_games(limit: int = 20) -> dict[str, Any]:
    """Recent recorded games + their accuracy."""
    from ..services.chess_games import list_games

    return list_games(limit=limit)


@router.get("/analytics")
async def trainer_analytics() -> dict[str, Any]:
    """Progress analytics: training-rating estimate + skill bars from recorded
    games. Honest scope: measures move quality vs the engine, not Elo."""
    from ..services.chess_games import progress_analytics

    return progress_analytics()


class GmGuessRequest(BaseModel):
    game_id: str = Field(..., description="Curated GM game id")
    ply: int = Field(0, ge=0, description="The ply the learner is guessing")
    guess_uci: str = Field(..., description="The learner's guessed move, UCI")


class GmExplainRequest(BaseModel):
    game_id: str = Field(..., description="Curated GM game id")
    ply: int = Field(0, ge=0, description="The ply whose GM move to explain")


@router.get("/gm-games")
async def trainer_gm_games() -> dict[str, Any]:
    """The curated famous Fischer + Carlsen games for guess-the-move study."""
    from ..services.gm_games import list_gm_games

    return await list_gm_games()


@router.post("/gm-games/curate")
async def trainer_gm_curate(force: bool = False) -> dict[str, Any]:
    """Rebuild the gm_games Qdrant index from the curated famous games."""
    from ..services.gm_games import curate

    return await curate(force=force)


@router.post("/gm-games/play")
async def trainer_gm_play(req: GmGuessRequest) -> dict[str, Any]:
    """Get the position at `ply` (without revealing the answer)."""
    from ..services.gm_games import play_game

    return play_game(req.game_id, req.ply)


@router.post("/gm-games/study")
async def trainer_gm_study(req: GmExplainRequest) -> dict[str, Any]:
    """STUDY MODE: the position at `ply` + the GM's move there, with a full
    'why' explanation (what it threatens, what it sets up). The move is
    REVEALED and explained — no guessing. Step through the game move-by-move."""
    from ..services.gm_games import study_game

    return await study_game(req.game_id, req.ply)


@router.post("/gm-games/guess")
async def trainer_gm_guess(req: GmGuessRequest) -> dict[str, Any]:
    """Compare the learner's guess against the GM's actual move."""
    from ..services.gm_games import guess_move

    return guess_move(req.game_id, req.ply, req.guess_uci)


# GmExplainRequest is defined above GmGuessRequest (moved to fix NameError at startup)


@router.post("/gm-games/explain")
async def trainer_gm_explain(req: GmExplainRequest) -> dict[str, Any]:
    """Explain a GM's move: why they played it, what it threatens, what they're
    setting up. Engine-grounded + book-grounded with a cloud narrative."""
    from ..services.gm_games import explain_move

    return await explain_move(req.game_id, req.ply)


class ChessComImportRequest(BaseModel):
    username: str = Field(
        ..., min_length=2, max_length=60, description="chess.com username"
    )
    months: int = Field(3, ge=1, le=12)
    max_games: int = Field(30, ge=1, le=200)
    color: str = Field("both", description="'white' | 'black' | 'both'")
    record_mistakes: bool = Field(True)
    record_games: bool = Field(True)


@router.post("/import/chesscom")
async def trainer_import_chesscom(req: ChessComImportRequest) -> dict[str, Any]:
    """Import a player's recent chess.com games via the public API, analyze
    every move, and feed the trainer's mistake queue + games store — so the
    trainer learns weaknesses from REAL games."""
    from ..services.chess_import import import_games

    return await import_games(
        req.username,
        months=req.months,
        max_games=req.max_games,
        color=req.color,
        record_mistakes=req.record_mistakes,
        record_games=req.record_games,
    )


class ChessComProfileRequest(BaseModel):
    username: str = Field(
        ..., min_length=2, max_length=60, description="chess.com username"
    )
    max_archives: int | None = Field(
        None, ge=1, le=120, description="Cap monthly archives (None = all)"
    )


class UsernameRequest(BaseModel):
    username: str = Field(
        ..., min_length=2, max_length=60, description="chess.com username"
    )


@router.get("/import/chesscom/username")
async def trainer_get_username() -> dict[str, Any]:
    """The last saved chess.com username (persisted on the backend so it
    survives any browser/origin)."""
    from ..services.chess_import import get_last_username

    return get_last_username()


@router.post("/import/chesscom/username")
async def trainer_set_username(req: UsernameRequest) -> dict[str, Any]:
    """Persist the chess.com username for next time."""
    from ..services.chess_import import save_last_username

    return save_last_username(req.username)


@router.post("/import/chesscom/profile")
async def trainer_import_chesscom_profile(
    req: ChessComProfileRequest,
) -> dict[str, Any]:
    """Bulk-parse ALL of a player's games (no engine — fast) and compute their
    weaknesses/strengths profile: openings, color split, time controls,
    think-time per phase, rating trend, win/loss. The personalization source."""
    from ..services.chess_import import build_profile

    return await build_profile(req.username, max_archives=req.max_archives)


class AnalysisStartRequest(BaseModel):
    username: str = Field(
        ..., min_length=2, max_length=60, description="chess.com username"
    )
    max_archives: int | None = Field(
        None, ge=1, le=120, description="Cap monthly archives (None = all)"
    )


@router.post("/analysis/start")
async def trainer_analysis_start(req: AnalysisStartRequest) -> dict[str, Any]:
    """Start (or resume) the background engine-analysis job: every game, every
    move analyzed with Stockfish, feeding the mistake store. Runs for hours in
    the background and survives restarts."""
    from ..services.chess_analysis_job import start_analysis

    return await start_analysis(req.username, max_archives=req.max_archives)


@router.get("/analysis/jobs")
async def trainer_analysis_jobs() -> dict[str, Any]:
    """All analysis jobs + their status."""
    from ..services.chess_analysis_job import list_jobs

    return list_jobs()


@router.get("/analysis/status/{job_id}")
async def trainer_analysis_status(job_id: str) -> dict[str, Any]:
    """Live status of one analysis job."""
    from ..services.chess_analysis_job import job_status

    return job_status(job_id)
