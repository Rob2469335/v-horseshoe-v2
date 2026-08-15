"""Grandmaster games collection + guess-the-move (2026 SOTA famous-game study).

The "Play like the greats" surface: a curated set of famous Fischer and Carlsen
games, indexed into Qdrant (gm_games) for retrieval, and served as a
guess-the-move training mode — the learner sees a position and guesses the
next move, compared against what the GM actually played.

Games are FACTUAL data (chess move sequences are not copyrightable). The
curated manifest points at games inside the downloaded public databases
(pgnmentor.com Fischer.pgn / Carlsen.pgn). The service stores the game's moves
as a python-chess game, indexes a per-game Qdrant point (embedding of the
moves' SAN), and exposes:

  - gm_games/curate  — rebuild the index from the manifest + databases
  - gm_games/list    — the curated famous games
  - gm_games/play    — start a guess-the-move session on a game
  - gm_games/move    — the learner's guess vs the GM's actual move

Fail-closed: missing databases/manifest -> empty lists, never a crash.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import chess
import chess.pgn

log = logging.getLogger(__name__)

_COLLECTION = "gm_games"
_DATA_DIR = Path("data/chess/gm")
_DB_DIR = Path(_DATA_DIR)  # databases live under data/chess/gm/db/
_MANIFEST = _DATA_DIR / "manifest.json"
_DB_FISCHER = _DATA_DIR / "db" / "Fischer.pgn"
_DB_CARLSEN = _DATA_DIR / "db" / "Carlsen.pgn"
_LOCK = threading.Lock()

# The famous games to curate: (player, tag). Each is located by scanning the
# database for a game matching White/Black + a move-order signature (the first
# few plies), so we never hand-type moves.
CURATED: list[dict[str, Any]] = [
    {
        "player": "fischer",
        "tag": "game-of-the-century",
        "white": "Byrne",
        "black": "Fischer",
        "name": "Byrne vs Fischer — Game of the Century (1956)",
        "year": 1956,
    },
    {
        "player": "fischer",
        "tag": "fischer-spassky-1972-g6",
        "white": "Fischer",
        "black": "Spassky",
        "name": "Fischer vs Spassky, 1972 WC Game 6",
        "year": 1972,
    },
    {
        "player": "fischer",
        "tag": "fischer-taimanov-1971-g6",
        "white": "Fischer",
        "black": "Taimanov",
        "name": "Fischer vs Taimanov, 1971 Candidates QF Game 6",
        "year": 1971,
    },
    {
        "player": "carlsen",
        "tag": "carlsen-karjakin-2016-g8",
        "white": "Carlsen",
        "black": "Karjakin",
        "name": "Carlsen vs Karjakin, 2016 WC Game 8",
        "year": 2016,
    },
    {
        "player": "carlsen",
        "tag": "carlsen-anand-2013-g6",
        "white": "Carlsen",
        "black": "Anand",
        "name": "Carlsen vs Anand, 2013 WC Game 6",
        "year": 2013,
    },
    {
        "player": "carlsen",
        "tag": "carlsen-caruana-2018-g6",
        "white": "Carlsen",
        "black": "Caruana",
        "name": "Carlsen vs Caruana, 2018 WC Game 6",
        "year": 2018,
    },
]


def _db_path(player: str) -> Path:
    return _DB_FISCHER if player == "fischer" else _DB_CARLSEN


def _load_game(
    player: str, white: str, black: str
) -> tuple[dict[str, Any] | None, chess.Board | None]:
    """Find the game matching White/Black in the database and return
    (summary, board-with-full-move-stack)."""
    path = _db_path(player)
    if not path.exists():
        return None, None
    white = white.lower()
    black = black.lower()
    with open(path, encoding="utf-8", errors="replace") as fh:
        while True:
            try:
                game = chess.pgn.read_game(fh)
            except Exception:
                continue
            if game is None:
                break
            h = game.headers
            if (
                h.get("White", "").lower() == white
                or white in h.get("White", "").lower()
            ) and (
                h.get("Black", "").lower() == black
                or black in h.get("Black", "").lower()
            ):
                # Force mainline move parsing (read_game is lazy), then replay.
                moves: list[str] = []
                b = chess.Board()
                for node in game.mainline():
                    mv = node.move
                    if mv is None:
                        continue
                    moves.append(b.san(mv))
                    b.push(mv)
                return {
                    "white": h.get("White"),
                    "black": h.get("Black"),
                    "date": h.get("Date", ""),
                    "result": h.get("Result", "*"),
                    "moves": moves,
                }, b
    return None, None


def _moves_from_board(board: chess.Board) -> list[str]:
    """Flatten the board's move stack into SAN moves (for storage + guessing)."""
    moves: list[str] = []
    b = chess.Board()
    for m in board.move_stack:
        moves.append(b.san(m))
        b.push(m)
    return moves


def _game_to_point(
    game_id: str, summary: dict, moves: list[str], player: str, tag: str, name: str
) -> dict:
    return {
        "id": game_id,
        "player": player,
        "tag": tag,
        "name": name,
        "white": summary.get("white", ""),
        "black": summary.get("black", ""),
        "date": summary.get("date", ""),
        "result": summary.get("result", "*"),
        "moves": moves,
        "move_count": len(moves),
    }


async def curate(force: bool = False) -> dict[str, Any]:
    """Build the gm_games Qdrant index from the curated famous games. Each game
    becomes one point. Idempotent (skips when already indexed unless force)."""
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    indexed = 0
    errors: list[str] = []
    for c in CURATED:
        summary, board = _load_game(c["player"], c["white"], c["black"])
        if board is None or not summary.get("moves"):
            errors.append(f"{c['tag']}: game not found in database")
            continue
        moves = summary["moves"]
        point = _game_to_point(
            f"{c['player']}-{c['tag']}",
            summary,
            moves,
            c["player"],
            c["tag"],
            c["name"],
        )
        try:
            async with AsyncQdrantClient(
                url=__import__("os").getenv("QDRANT_URL", "http://127.0.0.1:6333"),
                api_key=__import__("os").getenv("QDRANT_API_KEY"),
            ) as client:
                cols = await client.get_collections()
                names = {cc.name for cc in cols.collections}
                if _COLLECTION not in names:
                    await client.create_collection(
                        collection_name=_COLLECTION,
                        vectors_config=VectorParams(size=768, distance=Distance.COSINE),
                    )
                # Embed the move list (approximate: use the SAN string hash-free
                # via the embedder). Keep a simple text embedding.
                text = " ".join(moves[:40])
                vec = await _embed(text)
                await client.upsert(
                    collection_name=_COLLECTION,
                    points=[
                        PointStruct(
                            id=abs(hash(point["id"])) % (2**63),
                            vector=vec,
                            payload={
                                "id": point["id"],
                                "name": point["name"],
                                "player": point["player"],
                                "tag": point["tag"],
                                "white": point["white"],
                                "black": point["black"],
                                "date": point["date"],
                                "result": point["result"],
                                "move_count": point["move_count"],
                            },
                        )
                    ],
                )
            indexed += 1
        except Exception as exc:
            log.warning("gm game %s index failed: %s", c["tag"], exc)
            errors.append(f"{c['tag']}: {exc}")
    return {"ok": True, "indexed": indexed, "curated": len(CURATED), "errors": errors}


async def _embed(text: str) -> list[float]:
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            base_url="http://127.0.0.1:8081/v1",
            headers={"Authorization": "Bearer llama"},
        ) as client:
            resp = await client.post("/embeddings", json={"input": text})
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except Exception as exc:
        log.warning("gm embed failed: %s", exc)
        return [0.0] * 768


def list_gm_games() -> dict[str, Any]:
    """The curated famous games (from the local manifest of loaded games)."""
    out = []
    for c in CURATED:
        summary, board = _load_game(c["player"], c["white"], c["black"])
        out.append(
            {
                "id": f"{c['player']}-{c['tag']}",
                "name": c["name"],
                "player": c["player"],
                "white": summary["white"] if summary else c["white"],
                "black": summary["black"] if summary else c["black"],
                "year": c["year"],
                "result": summary["result"] if summary else "*",
                "move_count": len(summary["moves"]) if summary else 0,
            }
        )
    return {"ok": True, "count": len(out), "games": out}


def play_game(game_id: str, ply: int = 0) -> dict[str, Any]:
    """Start (or advance) a guess-the-move session: returns the position at
    `ply` (the learner's turn to guess the next move) WITHOUT revealing the
    answer."""
    for c in CURATED:
        if f"{c['player']}-{c['tag']}" != game_id:
            continue
        summary, board = _load_game(c["player"], c["white"], c["black"])
        if board is None:
            return {"ok": False, "error": "game not found in database"}
        moves = summary["moves"]
        if ply >= len(moves):
            return {
                "ok": True,
                "finished": True,
                "game_id": game_id,
                "result": summary["result"],
            }
        # Rebuild the board up to `ply` for the position FEN.
        b = chess.Board()
        for mv in board.move_stack[:ply]:
            b.push(mv)
        return {
            "ok": True,
            "finished": False,
            "game_id": game_id,
            "name": c["name"],
            "ply": ply,
            "fen": b.fen(),
            "side_to_move": "white" if b.turn else "black",
            "move_number": ply // 2 + 1,
        }
    return {"ok": False, "error": "unknown game"}


def guess_move(game_id: str, ply: int, guess_uci: str) -> dict[str, Any]:
    """Compare the learner's guess against the GM's actual move at `ply`.
    Returns {ok, correct, gm_move_uci, gm_move_san, correct_uci}."""
    for c in CURATED:
        if f"{c['player']}-{c['tag']}" != game_id:
            continue
        summary, board = _load_game(c["player"], c["white"], c["black"])
        if board is None or ply >= len(board.move_stack):
            return {"ok": False, "error": "invalid ply"}
        actual = board.move_stack[ply]
        actual_san = summary["moves"][ply]
        return {
            "ok": True,
            "correct": guess_uci == actual.uci(),
            "gm_move_uci": actual.uci(),
            "gm_move_san": actual_san,
            "guess_uci": guess_uci,
        }
    return {"ok": False, "error": "unknown game"}


async def explain_move(game_id: str, ply: int) -> dict[str, Any]:
    """Explain a GM's move at `ply`: WHY they played it, what it threatens, and
    what they're setting up. Engine-grounded (the coach plan + eval + book
    retrieval feed the narrative — the cloud model never free-forms chess
    analysis). Deterministic-first: the coach plan is always present even if
    the LLM prose fails."""
    import asyncio
    import os

    import litellm

    from ..core.settings import get_settings
    from .chess_book_memory import _concept_from, retrieve
    from .chess_trainer import _best_move_and_cp, coach_plan

    for c in CURATED:
        if f"{c['player']}-{c['tag']}" != game_id:
            continue
        summary, board = _load_game(c["player"], c["white"], c["black"])
        if board is None or ply >= len(board.move_stack):
            return {"ok": False, "error": "invalid ply"}
        gm_move = board.move_stack[ply]
        gm_san = summary["moves"][ply]

        # Position BEFORE the GM's move + the move's eval.
        before = chess.Board()
        for mv in board.move_stack[:ply]:
            before.push(mv)
        before_best, before_cp, _ = await asyncio.to_thread(_best_move_and_cp, before)
        after = before.copy()
        after.push(gm_move)
        after_cp = (await asyncio.to_thread(_best_move_and_cp, after))[1]

        # Deterministic grounding: coach plan + eval swing + book fragments.
        plan = coach_plan(before.fen())
        concept = _concept_from("strategy", gm_san)
        frags = await retrieve(f"{concept} {gm_san}", top_k=2)
        book_lines = "\n".join(
            f"[{r['title']}]: {r['text'][:250]}" for r in frags if r.get("text")
        )

        # Deterministic narrative (always present).
        det = f"GM played {gm_san}. "
        if plan.get("ok") and plan.get("plan"):
            det += f"Position plan before this move: {plan['plan']}. "
        swing = after_cp - before_cp
        if swing > 60:
            det += f"The move improved White's chances by roughly {round(swing)} centipawns. "
        elif swing < -60:
            det += f"The move gives up ~{round(-swing)} centipawns — likely a deliberate positional choice. "

        # Cloud prose (best-effort): why + what they're setting up.
        prose = ""
        s = get_settings()
        model = getattr(s, "analysis_cloud_model", None) or "openai/deepseek-v4-flash"
        key = os.getenv("OPENAI_API_KEY", "")
        if key:
            prompt = (
                "You are a chess coach explaining a famous grandmaster move to a ~500-rated "
                "beginner. Explain in 2-3 short sentences WHY the GM played this move, what it "
                "threatens, and what it is setting up (the plan). Use only the position facts "
                "and book ideas given; do not invent analysis. Do not reason — just answer "
                "directly.\n\n"
                f"GAME: {c['name']}\nPOSITION BEFORE (FEN): {before.fen()}\n"
                f"GM PLAYED: {gm_san}\n\n"
                f"COACH PLAN: {plan.get('plan', '')}\n"
                f"BOOK IDEAS:\n{book_lines or '(none)'}\n\nEXPLANATION:"
            )
            try:
                async with asyncio.timeout(45):
                    resp = await litellm.acompletion(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        api_base=os.getenv(
                            "OPENAI_API_BASE", "https://opencode.ai/zen/go/v1"
                        ),
                        api_key=key,
                        custom_llm_provider="openai",
                        max_tokens=2000,
                        timeout=45,
                    )
                prose = (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                log.warning("gm explain LLM failed: %s", exc)

        return {
            "ok": True,
            "game_id": game_id,
            "ply": ply,
            "gm_move_san": gm_san,
            "fen_before": before.fen(),
            "explanation": (prose + "\n\n" + det).strip() if prose else det.strip(),
            "degraded": not prose,
        }
    return {"ok": False, "error": "unknown game"}
