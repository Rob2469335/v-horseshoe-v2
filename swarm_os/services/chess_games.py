"""Full-game recording + guided review for the chess trainer.

The 2026 SOTA post-game review (chess.com Game Review / lichess "learn from
your mistakes"): persist every move of a game with its eval + classification,
then offer a guided review — the eval curve, the "key moments" (blunder
hotspots, best moves, missed tactics), and a one-click action to queue the
game's mistakes into the spaced-repetition store.

Every move that evaluate_move processes is appended to the active game. The
review aggregates the per-move data, highlights the swing points, and computes
a rough per-game accuracy (0-100, chess.com-CAPS-style from expected-points
kept). Fail-closed: unreadable store -> empty review, never a crash.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_DIR = Path("data/chess")
_GAMES_FILE = _DATA_DIR / "games.jsonl"
_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _load_games() -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    try:
        if _GAMES_FILE.exists():
            for line in _GAMES_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    games.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("chess games load failed: %s", exc)
    return games


def _save_games(games: list[dict[str, Any]]) -> None:
    from .chess_store import save_jsonl

    save_jsonl(_GAMES_FILE, games)


def start_game(
    start_fen: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    finalize_existing: bool = True,
    player_color: str = "w",
    interactive: bool = True,
) -> dict[str, Any]:
    """Begin a new recorded game. Returns the game id.

    By default any existing 'in-progress' game is finalized (no review data
    lost) — the interactive trainer starts a fresh game each session. Background
    bulk imports MUST pass finalize_existing=False so they never truncate a
    live interactive game the user is mid-way through (the old behavior
    silently finalized every in-progress game on every imported game).

    `player_color` ('w'/'b') records which side the player is on so accuracy and
    analytics only count THEIR moves (parity: even ply = white, odd = black).

    `interactive=True` (default) means only the player's moves are recorded
    (engine replies omitted). `interactive=False` means a full game with both
    sides' moves (used by bulk import)."""
    with _LOCK:
        games = _load_games()
        if finalize_existing:
            for g in games:
                if g.get("status") == "in_progress":
                    g["status"] = "finished"
                    g["ended_at"] = _now()
        game = {
            "id": uuid.uuid4().hex[:12],
            "start_fen": start_fen,
            "started_at": _now(),
            "ended_at": None,
            "status": "in_progress",
            "player_color": player_color,
            "interactive": interactive,
            "moves": [],
        }
        games.append(game)
        _save_games(games)
        # Reset the persistent current-plan state for the new game.
        try:
            from .chess_plans import reset

            reset(game["id"])
        except Exception as exc:
            log.warning("plan state reset failed: %s", exc)
        return game


def record_move(game_id: str, move: dict[str, Any]) -> None:
    """Append a move to the active game. `move` carries {uci, san, fen,
    classification, eval_before_cp, eval_after_cp, win_delta_pct, is_best}."""
    with _LOCK:
        games = _load_games()
        game = next(
            (
                g
                for g in games
                if g.get("id") == game_id and g.get("status") == "in_progress"
            ),
            None,
        )
        if game is None:
            return
        game["moves"].append(move)
        _save_games(games)


def record_game(game: dict[str, Any]) -> dict[str, Any]:
    """Persist a COMPLETE recorded game in ONE load/save.

    The bulk-import path used to call start_game + record_move per move — each
    record_move loaded + rewrote the whole games.jsonl, i.e. hundreds of GB of
    write amplification importing 1,000 games. This writes the finished game
    once. `game` should be a finished-game dict (id, moves, status='finished',
    started_at/ended_at)."""
    with _LOCK:
        games = _load_games()
        existing = next((g for g in games if g.get("id") == game.get("id")), None)
        if existing is not None:
            existing.update(game)
        else:
            games.append(game)
        _save_games(games)
        return {"ok": True, "id": game.get("id")}


def finish_game(game_id: str) -> dict[str, Any]:
    """Finalize a game and return its review (or the review of the most recent
    finished game if the id is unknown)."""
    with _LOCK:
        games = _load_games()
        game = next((g for g in games if g.get("id") == game_id), None)
        if game is None and games:
            game = games[-1]
        if game is None:
            return {"ok": False, "error": "no games recorded"}
        if game.get("status") == "in_progress":
            game["status"] = "finished"
            game["ended_at"] = _now()
            _save_games(games)
        return _review_of(game)


def list_games(limit: int = 20) -> dict[str, Any]:
    with _LOCK:
        games = _load_games()
    return {
        "ok": True,
        "count": len(games),
        "games": [
            {
                "id": g.get("id"),
                "started_at": g.get("started_at"),
                "status": g.get("status"),
                "move_count": len(g.get("moves", [])),
                "accuracy": _accuracy_of(g),
            }
            for g in games[-limit:]
        ],
    }


def _player_color_of(game: dict[str, Any]) -> str:
    """Which color the human played in a recorded game ('w'/'b'). The player is
    'w' unless the game record says otherwise (legacy records pre-date the field,
    and interactive training is always white)."""
    return (game.get("player_color") or "w").lower()


def _is_player_move(game: dict[str, Any], start_idx: int, offset: int) -> bool:
    """True if the move at (global) index `start_idx + offset` was played by the
    human, via mover-color parity (even ply = white, odd = black). Every
    consumer that must only reflect the player's own play — accuracy, the
    guided-review queue payload, queue_game_mistakes, and progress analytics —
    goes through this single predicate so the canonical full-both-sides game
    shape (as stored) never leaks the opponent's moves into user-facing stats.

    For interactive games (player-only moves recorded), all recorded moves are
    the player's moves, so return True unconditionally. Legacy games without the
    'interactive' field are also player-only (both interactive and imported
    games filter to player moves only), so default to True."""
    if game.get("interactive", True):
        return True
    player_color = _player_color_of(game)
    return ((start_idx + offset) % 2 == 0) == (player_color == "w")


def _accuracy_of(game: dict[str, Any], start_idx: int = 0) -> float:
    """Per-game accuracy 0-100 (chess.com-CAPS-style): average expected-points
    kept per move (Best=1.0). Only counts moves that were evaluated.

    Only the PLAYER's moves count (mover color via even/odd ply index) so the
    opponent's accuracy never inflates the player's rating.

    `start_idx` is the global move index the (possibly sliced) `moves` list
    begins at, so phase-slices keep the correct mover parity."""
    moves = [
        m
        for i, m in enumerate(game.get("moves", []))
        if m.get("win_delta_pct") is not None and _is_player_move(game, start_idx, i)
    ]
    if not moves:
        return 0.0
    # Clamp each move's contribution to [0, 1]: a Best move can improve win%
    # by more than the starting point (e.g. +5% -> 1.05), which would push a
    # perfect game's accuracy ABOVE 100. 100 is a ceiling, not a suggestion.
    kept = [
        max(0.0, min(1.0, 1.0 + m.get("win_delta_pct", 0.0) / 100.0)) for m in moves
    ]
    return round(sum(kept) / len(kept) * 100.0, 1)


def _review_of(game: dict[str, Any]) -> dict[str, Any]:
    """Build the guided review: eval curve, key moments (blunders/mistakes +
    best moves), per-phase breakdown, and the queue-mistakes payload."""
    moves = game.get("moves", [])
    # Eval curve (win% after each move, from the mover's perspective).
    curve = [
        {
            "n": i + 1,
            "san": m.get("san"),
            "win_pct": m.get("win_after_pct"),
            "classification": m.get("classification"),
        }
        for i, m in enumerate(moves)
    ]
    # Key moments: the swings (lichess evalSwings pattern).
    key_moments: list[dict[str, Any]] = []
    for i, m in enumerate(moves):
        cls = m.get("classification")
        if cls in ("Blunder", "Mistake", "Inaccuracy"):
            key_moments.append(
                {
                    "type": "blunder",
                    "move_n": i + 1,
                    "san": m.get("san"),
                    "classification": cls,
                    "win_delta_pct": m.get("win_delta_pct"),
                    "fen": m.get("fen"),
                }
            )
        elif m.get("is_best"):
            key_moments.append(
                {
                    "type": "best",
                    "move_n": i + 1,
                    "san": m.get("san"),
                    "classification": "Best",
                }
            )
    # Per-phase accuracy (rough thirds: opening/middlegame/endgame).
    n = len(moves)
    phases = {}
    if n:
        third = max(1, n // 3)
        for label, start_sl, sl in (
            ("opening", 0, moves[:third]),
            ("middlegame", third, moves[third : 2 * third]),
            ("endgame", 2 * third, moves[2 * third :]),
        ):
            if sl:
                sub = {
                    "id": game.get("id"),
                    "status": "finished",
                    "player_color": game.get("player_color"),
                    "moves": sl,
                }
                phases[label] = _accuracy_of(sub, start_idx=start_sl)
    # Queue-mistakes payload: the blunder/mistake FENs + best moves, ready for
    # chess_mistakes.record_mistake. Only the PLAYER's own mistakes belong —
    # the opponent's mistakes are the player's opportunities, not their blunders.
    queue_mistakes = [
        {
            "pre_fen": m.get("pre_fen") or m.get("fen"),
            "played_uci": m.get("uci"),
            "played_san": m.get("san"),
            "best_uci": m.get("best_uci"),
            "best_san": m.get("best_move_san"),
            "classification": m.get("classification"),
            "concept": m.get("concept", ""),
        }
        for i, m in enumerate(moves)
        if m.get("classification") in ("Mistake", "Blunder", "Inaccuracy")
        and _is_player_move(game, 0, i)
    ]
    return {
        "ok": True,
        "game_id": game.get("id"),
        "move_count": n,
        "accuracy": _accuracy_of(game),
        "curve": curve,
        "key_moments": key_moments,
        "phases": phases,
        "queue_mistakes": queue_mistakes,
    }


def queue_game_mistakes(game_id: str | None = None) -> dict[str, Any]:
    """Queue every mistake/blunder of a game (or the most recent finished one)
    into the spaced-repetition store."""
    from .chess_mistakes import record_mistake

    with _LOCK:
        games = _load_games()
        game = next((g for g in games if g.get("id") == game_id), None)
        if game is None and games:
            game = games[-1]
        if game is None:
            return {"ok": False, "error": "no games recorded"}
    queued = 0
    for i, m in enumerate(game.get("moves", [])):
        if m.get("classification") not in ("Mistake", "Blunder", "Inaccuracy"):
            continue
        if not _is_player_move(game, 0, i):
            continue
        record_mistake(
            pre_fen=m.get("pre_fen") or m.get("fen") or "",
            played_uci=m.get("uci") or "",
            played_san=m.get("san") or "",
            best_uci=m.get("best_uci"),
            best_san=m.get("best_move_san"),
            classification=m.get("classification") or "Mistake",
            concept=m.get("concept", ""),
            book_titles=m.get("book_titles") or [],
        )
        queued += 1
    return {"ok": True, "queued": queued, "game_id": game.get("id")}


# ---------------------------------------------------------------------------
# Progress analytics (2026 SOTA — Aimchess-style skill bars + training rating)
# ---------------------------------------------------------------------------
# Aggregates the recorded games into the signals that actually matter for a
# beginner (per the research): move accuracy, blunder rate, per-phase precision,
# and a smoothed "training rating" estimate. Honest scope: these measure the
# learner's move QUALITY vs the engine, not their competitive Elo.

# Classification -> move-quality points (0-100, chess.com-CAPS-like).
_CLASS_QUALITY = {
    "Best": 100.0,
    "Excellent": 95.0,
    "Good": 82.0,
    "Inaccuracy": 65.0,
    "Mistake": 45.0,
    "Blunder": 20.0,
    "Missed sacrifice": 60.0,
}


def _move_quality(m: dict[str, Any]) -> float:
    """A single move's quality 0-100 from its classification."""
    cls = m.get("classification") or ""
    if cls in _CLASS_QUALITY:
        return _CLASS_QUALITY[cls]
    return 60.0


def progress_analytics() -> dict[str, Any]:
    """Aggregate recorded games into skill bars + a training-rating estimate.

    Returns {ok, training_rating, games_count, moves_count, skills: {
    accuracy, blunder_rate, mistake_rate, best_rate, opening, middlegame,
    endgame},     recent: [{game_id, accuracy, move_count, started_at}]}."""
    with _LOCK:
        games = _load_games()
    finished = [g for g in games if g.get("status") == "finished" and g.get("moves")]
    if not finished:
        return {
            "ok": True,
            "training_rating": None,
            "games_count": 0,
            "moves_count": 0,
            "skills": {},
            "recent": [],
        }

    all_moves = []
    opening_moves = []
    middlegame_moves = []
    endgame_moves = []

    for g in finished:
        g_moves = g.get("moves", [])
        player_moves = [m for i, m in enumerate(g_moves) if _is_player_move(g, 0, i)]
        all_moves.extend(player_moves)

        n_p = len(player_moves)
        if n_p == 0:
            continue

        third = max(1, n_p // 3)
        opening_moves.extend(player_moves[:third])
        middlegame_moves.extend(player_moves[third : 2 * third])
        endgame_moves.extend(player_moves[2 * third :])

    n = len(all_moves)
    if n == 0:
        return {
            "ok": True,
            "training_rating": None,
            "games_count": len(finished),
            "moves_count": 0,
            "skills": {},
            "recent": [],
        }

    quals = [_move_quality(m) for m in all_moves]
    accuracy = round(sum(quals) / n, 1)
    blunder_rate = round(
        sum(1 for m in all_moves if m.get("classification") == "Blunder") / n * 100, 1
    )
    mistake_rate = round(
        sum(1 for m in all_moves if m.get("classification") in ("Mistake", "Blunder"))
        / n
        * 100,
        1,
    )
    best_rate = round(
        sum(1 for m in all_moves if m.get("classification") in ("Best", "Excellent"))
        / n
        * 100,
        1,
    )

    phases: dict[str, float] = {}
    for label, sl in (
        ("opening", opening_moves),
        ("middlegame", middlegame_moves),
        ("endgame", endgame_moves),
    ):
        if sl:
            phases[label] = round(sum(_move_quality(m) for m in sl) / len(sl), 1)

    # Training rating: a smoothed estimate from the last several games' accuracy.
    # Map average move quality to a rating around the 500-1500 band (rating =
    # 300 + 11 * accuracy is a simple monotonic calibration — a display aid,
    # NOT a real Elo).
    recent = finished[-20:]
    recent_accs = [_accuracy_of(g) for g in recent]
    avg_acc = sum(recent_accs) / len(recent_accs)
    training_rating = round(300 + 11 * avg_acc)

    return {
        "ok": True,
        "training_rating": training_rating,
        "games_count": len(finished),
        "moves_count": n,
        "phases": phases,
        "skills": {
            "accuracy": accuracy,
            "blunder_rate": blunder_rate,
            "mistake_rate": mistake_rate,
            "best_rate": best_rate,
            "opening": phases.get("opening", 0.0),
            "middlegame": phases.get("middlegame", 0.0),
            "endgame": phases.get("endgame", 0.0),
        },
        "recent": [
            {
                "game_id": g.get("id"),
                "accuracy": _accuracy_of(g),
                "move_count": len(g.get("moves", [])),
                "started_at": g.get("started_at"),
            }
            for g in recent[-8:][::-1]
        ],
    }
