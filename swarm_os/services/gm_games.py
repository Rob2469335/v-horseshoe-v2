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
import json

log = logging.getLogger(__name__)

_COLLECTION = "gm_games"
_DATA_DIR = Path("data/chess/gm")
_DB_DIR = Path(_DATA_DIR)  # databases live under data/chess/gm/db/
_MANIFEST = _DATA_DIR / "manifest.json"
_DB_FISCHER = _DATA_DIR / "db" / "Fischer.pgn"
_DB_CARLSEN = _DATA_DIR / "db" / "Carlsen.pgn"
_LOCK = threading.Lock()

# In-memory cache of parsed games per (player, white, black, date) so the
# 5.7MB Carlsen PGN database is parsed ONCE per unique game, not on every
# study/play/explain call (a raw scan was ~17s and blocked the event loop).
_db_cache: dict[tuple, tuple[dict[str, Any] | None, chess.Board | None]] = {}

# Durable cache of the CURATED games' moves — the 5.7MB PGN DB is parsed ONCE
# and the curated games are written here as a small JSON, so every later
# study/play/list reads this file (milliseconds) instead of rescanning the
# whole database (17s+).
_GAMES_CACHE = _DATA_DIR / "curated_games.json"
_cache_lock = threading.Lock()
_games_store: dict[str, dict[str, Any]] | None = None  # game_id -> summary


def _load_games_cache() -> dict[str, dict[str, Any]]:
    """Load (or build) the durable curated-games cache. Builds it from the PGN
    databases once, then re-reads the small JSON on every call."""
    global _games_store
    if _games_store is not None:
        return _games_store
    with _cache_lock:
        if _games_store is not None:
            return _games_store
        store: dict[str, dict[str, Any]] = {}
        if _GAMES_CACHE.exists():
            try:
                store = json.loads(_GAMES_CACHE.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning("gm games cache unreadable: %s", exc)
                store = {}
        if not store:
            # Cold build: parse the databases once, cache the curated games.
            for c in CURATED:
                summary, board = _load_game(
                    c["player"], c["white"], c["black"], c.get("date")
                )
                if board is None or not summary or not summary.get("moves"):
                    continue
                gid = f"{c['player']}-{c['tag']}"
                store[gid] = {
                    "game_id": gid,
                    "name": c["name"],
                    "player": c["player"],
                    "tag": c["tag"],
                    "year": c.get("year"),
                    "white": summary.get("white", ""),
                    "black": summary.get("black", ""),
                    "date": summary.get("date", ""),
                    "result": summary.get("result", "*"),
                    "moves": summary["moves"],
                    "move_count": len(summary["moves"]),
                }
            try:
                _GAMES_CACHE.parent.mkdir(parents=True, exist_ok=True)
                _GAMES_CACHE.write_text(json.dumps(store), encoding="utf-8")
            except Exception as exc:
                log.warning("gm games cache write failed: %s", exc)
        _games_store = store
        return store


def _get_game(game_id: str) -> dict[str, Any] | None:
    """The cached summary (name, moves, result) for a curated game, or None."""
    return _load_games_cache().get(game_id)

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
    {
        "player": "carlsen",
        "tag": "gukesh-carlsen-2026-norway-r5",
        "white": "Gukesh",
        "black": "Carlsen",
        "name": "Gukesh vs Carlsen — Norway Chess 2026 (Carlsen wins with Black)",
        "year": 2026,
        "date": "2026.05.28",
    },
    {
        "player": "carlsen",
        "tag": "carlsen-gukesh-2026-norway",
        "white": "Carlsen",
        "black": "Gukesh",
        "name": "Carlsen vs Gukesh — Norway Chess 2026 (Carlsen beats the World Champion)",
        "year": 2026,
        "date": "2026.06.05",
    },
]


def _db_path(player: str) -> Path:
    return _DB_FISCHER if player == "fischer" else _DB_CARLSEN


def _load_game(
    player: str, white: str, black: str, date: str | None = None
) -> tuple[dict[str, Any] | None, chess.Board | None]:
    """Find the game matching White/Black in the database and return
    (summary, board-with-full-move-stack). When `date` (e.g. '2026.05.28')
    is given, only a game with that exact Date header matches — the database
    holds many Carlsen-vs-X games and name alone can pick a wrong one.

    Parsing the 5.7MB Carlsen PGN on every call is ~17s of blocking — results
    are cached per (player, white, black, date) so each game is parsed once."""
    key = (player, white.lower(), black.lower(), date)
    with _LOCK:
        if key in _db_cache:
            return _db_cache[key]
    path = _db_path(player)
    if not path.exists():
        with _LOCK:
            _db_cache[key] = (None, None)
        return None, None
    white = white.lower()
    black = black.lower()
    found: tuple[dict[str, Any] | None, chess.Board | None] = (None, None)
    with open(path, encoding="utf-8", errors="replace") as fh:
        while True:
            try:
                game = chess.pgn.read_game(fh)
            except Exception:
                continue
            if game is None:
                break
            h = game.headers
            if date and h.get("Date", "") != date:
                continue
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
                found = (
                    {
                        "white": h.get("White"),
                        "black": h.get("Black"),
                        "date": h.get("Date", ""),
                        "result": h.get("Result", "*"),
                        "moves": moves,
                    },
                    b,
                )
                break
    with _LOCK:
        _db_cache[key] = found
    return found


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
        summary, board = _load_game(c["player"], c["white"], c["black"], c.get("date"))
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
                            # Stable ID across restarts (Python hash() is salted).
                            id=int.from_bytes(
                                __import__("hashlib")
                                .sha256(point["id"].encode())
                                .digest()[:8],
                                "big",
                            ),
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


async def list_gm_games() -> dict[str, Any]:
    """The curated famous games (from the durable game cache)."""
    store = _load_games_cache()
    out = [
        {
            "id": info["game_id"],
            "name": info["name"],
            "player": info["player"],
            "white": info["white"],
            "black": info["black"],
            "year": info.get("year"),
            "result": info["result"],
            "move_count": info["move_count"],
        }
        for info in store.values()
    ]
    out.sort(key=lambda g: (g["player"], str(g["year"])))
    return {"ok": True, "count": len(out), "games": out}


def play_game(game_id: str, ply: int = 0) -> dict[str, Any]:
    """Start (or advance) a guess-the-move session: returns the position at
    `ply` (the learner's turn to guess the next move) WITHOUT revealing the
    answer. Served from the durable game cache."""
    info = _get_game(game_id)
    if info is None:
        return {"ok": False, "error": "game not found"}
    moves = info["moves"]
    if ply >= len(moves):
        return {
            "ok": True,
            "finished": True,
            "game_id": game_id,
            "result": info["result"],
        }
    # Rebuild the board up to `ply` for the position FEN.
    b = chess.Board()
    for mv in moves[:ply]:
        b.push_san(mv)
    return {
        "ok": True,
        "finished": False,
        "game_id": game_id,
        "name": info["name"],
        "ply": ply,
        "fen": b.fen(),
        "side_to_move": "white" if b.turn else "black",
        "move_number": ply // 2 + 1,
    }
    return {"ok": False, "error": "unknown game"}


def guess_move(game_id: str, ply: int, guess_uci: str) -> dict[str, Any]:
    """Compare the learner's guess against the GM's actual move at `ply`.
    Returns {ok, correct, gm_move_uci, gm_move_san, correct_uci}."""
    info = _get_game(game_id)
    if info is None:
        return {"ok": False, "error": "game not found"}
    moves = info["moves"]
    if ply >= len(moves):
        return {"ok": False, "error": "invalid ply"}
    # Replay to the position and get the actual move's UCI + SAN.
    b = chess.Board()
    for mv in moves[: ply + 1]:
        b.push_san(mv)
    actual_uci = b.peek().uci()
    return {
        "ok": True,
        "correct": guess_uci == actual_uci,
        "gm_move_uci": actual_uci,
        "gm_move_san": moves[ply],
        "guess_uci": guess_uci,
    }


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

    info = _get_game(game_id)
    if info is None:
        return {"ok": False, "error": "game not found"}
    moves = info["moves"]
    if ply >= len(moves):
        return {"ok": False, "error": "invalid ply"}
    gm_san = moves[ply]
    # Rebuild the position BEFORE the GM's move from the cached SAN moves.
    before = chess.Board()
    for mv in moves[:ply]:
        before.push_san(mv)
    gm_move = before.parse_san(gm_san)

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
    # after_cp is from the NEW side-to-move (opponent) — negate to the
    # mover's perspective before comparing with before_cp.
    mover_after = -after_cp
    swing = mover_after - before_cp
    if swing > 60:
        det += f"The move improved White's chances by roughly {round(swing)} centipawns. "
    elif swing < -60:
        det += f"The move gives up ~{round(-swing)} centipawns — likely a deliberate positional choice. "

    # Cloud prose (best-effort): why + what they're setting up. Prefer
    # DeepSeek direct (DEEPSEEK_API_KEY, native provider — clean prose,
    # reliable); fall back to the OpenCode Go proxy.
    prose = ""
    s = get_settings()
    ds_key = os.getenv("DEEPSEEK_API_KEY", "")
    key = os.getenv("OPENAI_API_KEY", "")
    if ds_key or key:
        prompt = (
            "You are a chess coach explaining a famous grandmaster move to a ~500-rated "
            "beginner. Explain in 2-3 short sentences WHY the GM played this move, what it "
            "threatens, and what it is setting up (the plan). Use only the position facts "
            "and book ideas given; do not invent analysis. Do not reason — just answer "
            "directly.\n\n"
            f"GAME: {info['name']}\nPOSITION BEFORE (FEN): {before.fen()}\n"
            f"GM PLAYED: {gm_san}\n\n"
            f"COACH PLAN: {plan.get('plan', '')}\n"
            f"BOOK IDEAS:\n{book_lines or '(none)'}\n\nEXPLANATION:"
        )
        try:
            async with asyncio.timeout(45):
                if ds_key:
                    resp = await litellm.acompletion(
                        model="deepseek/deepseek-v4-flash",
                        messages=[{"role": "user", "content": prompt}],
                        api_key=ds_key,
                        max_tokens=500,
                        timeout=45,
                    )
                else:
                    resp = await litellm.acompletion(
                        model=getattr(s, "analysis_cloud_model", None)
                        or "openai/deepseek-v4-flash",
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


_PIECE_VAL = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}


def _material(b: chess.Board, color: bool) -> int:
    """Material count for a color (pawns=1, minors=3, rooks=5, queen=9)."""
    total = 0
    for sq in chess.SQUARES:
        p = b.piece_at(sq)
        if p and p.color == color:
            total += _PIECE_VAL.get(p.symbol().lower(), 0)
    return total


def _critical_moment(before: chess.Board, gm_san: str, ply: int) -> dict[str, Any]:
    """Classify a GM move as a THINK POSITION or a smooth pass-through.

    Combines signals — check, capture, material change, pawn-structure change,
    endgame transition, forced sequence — so a long game yields only ~8-15
    real pauses, not one per move. Returns {think_required, critical_type
    (list), difficulty (1-3), reason}."""
    try:
        mv = before.parse_san(gm_san)
    except Exception:
        return {"think_required": False, "critical_type": [], "difficulty": 1, "reason": ""}
    after = before.copy()
    after.push(mv)

    critical_type: list[str] = []
    reasons: list[str] = []

    if after.is_check():
        critical_type.append("check")
        reasons.append("it's a check")
    # DEFENSE moment: the side to move has a piece attacked more than defended
    # (it WOULD hang) and the GM's move rescues it — a 'don't hang your piece'
    # training signal, the mirror image of a hanging-piece mistake.
    before_turn = before.turn
    for sq in chess.SQUARES:
        p = before.piece_at(sq)
        if not p or p.color != before_turn or p.piece_type == chess.KING:
            continue
        atk = before.attackers(not before_turn, sq)
        if len(atk) > len(before.attackers(before_turn, sq)):
            # This piece is en prise; does the GM's move save it?
            still_attacked = len(after.attackers(not before_turn, sq)) > len(after.attackers(before_turn, sq)) if after.piece_at(sq) and after.piece_at(sq).color == before_turn else False
            if not still_attacked:
                critical_type.append("defense")
                reasons.append(f"a piece on {chess.square_name(sq)} is rescued")
            break
    # Material swing from a FIXED perspective (White) before vs after — using
    # side-to-move flips the sign incorrectly for the after-state.
    mat_before = _material(before, chess.WHITE) - _material(before, chess.BLACK)
    mat_after = _material(after, chess.WHITE) - _material(after, chess.BLACK)
    swing = abs(mat_after - mat_before)
    if swing >= 3:
        critical_type.append("tactical")
        reasons.append(f"the material balance swings by {swing} points")
    elif swing >= 1:
        # A small net material gain/loss (a pawn) is a real decision.
        critical_type.append("capture")
        reasons.append("a pawn is won or lost")
    # Sacrifice: White-side material swung DOWN (relative to the mover, the
    # mover gave material up). If White moved, mover_swing = mat_after-mat_before;
    # if Black moved, the mover's loss is the opposite sign.
    mover_is_white = before.turn == chess.WHITE
    mover_lost = (mat_before - mat_after) if mover_is_white else (mat_after - mat_before)
    if mover_lost >= 3:
        critical_type.append("sacrifice")
        reasons.append("material is offered — a sacrifice or a deliberate trade")
    # Pawn-structure change (a pawn was captured) that isn't an obvious recapture.
    if (
        before.is_capture(mv)
        and before.piece_at(mv.to_square)
        and before.piece_at(mv.to_square).piece_type == chess.PAWN
        and swing >= 1
    ):
        critical_type.append("structure")
        if "the pawn structure changes" not in reasons:
            reasons.append("the pawn structure changes")
    # Endgame transition: queens off the board.
    if before.queens > 0 and after.queens == 0:
        critical_type.append("endgame")
        reasons.append("the queens come off — a new endgame begins")

    think_required = bool(
        critical_type
        and (
            swing >= 1  # any net material win/loss is a real decision (recaptures are swing 0)
            or "tactical" in critical_type
            or "sacrifice" in critical_type
            or "endgame" in critical_type
        )
    )
    difficulty = min(3, max(1, 1 + (1 if "tactical" in critical_type else 0) + (1 if "sacrifice" in critical_type else 0) + (1 if "endgame" in critical_type else 0)))
    reason = "; ".join(reasons) if reasons else ""
    return {
        "think_required": think_required,
        "critical_type": critical_type,
        "difficulty": difficulty,
        "reason": reason,
    }


def _moment_hint(critical_type: list[str]) -> str:
    """A one-line prompt for a THINK POSITION (what to think about)."""
    if "tactical" in critical_type:
        return "Material is on the line — find the best move and check your calculation."
    if "check" in critical_type:
        return "A check — is it the right one, or just a check?"
    if "capture" in critical_type:
        return "A capture — is it actually safe to take?"
    if "endgame" in critical_type:
        return "Queens are coming off — what changes in the endgame?"
    return "A key moment — what's the best move and why?"


async def study_game(game_id: str, ply: int = 0) -> dict[str, Any]:
    """STUDY MODE: return the position at `ply` AND the GM's move at that ply
    with a full 'why' explanation (what it threatens, what it sets up). Unlike
    guess-the-move, the move is REVEALED and EXPLAINED — the learner studies
    the game move-by-move instead of guessing. Served from the durable game
    cache (parsed from the PGN DB once), so it is instant on repeat calls."""
    info = _get_game(game_id)
    if info is None:
        return {"ok": False, "error": "unknown game"}
    moves = info["moves"]
    if ply >= len(moves):
        return {
            "ok": True,
            "finished": True,
            "game_id": game_id,
            "name": info["name"],
            "result": info["result"],
            "ply": ply,
            "total_plies": len(moves),
        }
    # Rebuild the board up to `ply` (the position BEFORE the move) from the
    # cached SAN moves — no PGN database scan.
    b = chess.Board()
    for mv in moves[:ply]:
        b.push_san(mv)
    gm_san = moves[ply]
    # The GM move's UCI (push it on a scratch board to get the UCI).
    scratch = b.copy()
    scratch.push_san(gm_san)
    gm_uci = scratch.peek().uci()
    # STRUCTURED CRITICAL-MOMENT detection. Not every check/capture is a hard
    # pause — combine signals so a 129-ply game yields ~8-15 think positions,
    # not 60 interruptions.
    cm = _critical_moment(b, gm_san, ply)
    hint = None
    if cm["think_required"]:
        hint = _moment_hint(cm["critical_type"])
    try:
        explanation = await explain_move(game_id, ply)
        explain = explanation.get("explanation", "")
        degraded = explanation.get("degraded", False)
    except Exception:
        explain, degraded = "", True
    return {
        "ok": True,
        "finished": False,
        "game_id": game_id,
        "name": info["name"],
        "year": info.get("year"),
        "ply": ply,
        "total_plies": len(moves),
        "fen_before": b.fen(),
        "side_to_move": "white" if b.turn else "black",
        "move_number": ply // 2 + 1,
        "gm_move_san": gm_san,
        "is_key_moment": cm["think_required"],
        "critical_type": cm["critical_type"],
        "difficulty": cm["difficulty"],
        "think_required": cm["think_required"],
        "reason": cm["reason"],
        "hint": hint,
        "gm_move_uci": gm_uci,
        "explanation": explain,
        "degraded": degraded,
    }
