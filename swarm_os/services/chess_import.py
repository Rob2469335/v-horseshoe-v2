"""Chess.com game import — learn your weaknesses from your REAL games.

Fetches a player's recent games from chess.com's PUBLIC API (no auth, no
browser needed), parses each PGN with python-chess, evaluates every move with
the same engine + classification machinery as the trainer, and feeds the
results into the two stores the trainer already learns from:

  - chess_mistakes: every Mistake/Blunder becomes a "find the better move"
    spaced-repetition review position (your own blunders, from real games);
  - chess_games: the game is recorded so progress analytics / the weekly plan
    see it.

The analysis is deterministic + engine-grounded (the trainer's exact seams).
Evaluation of a ~40-move game is feasible with the persistent engine (a few
seconds), and positions are FEN-cached across games.

Fail-closed: a network failure / bad username returns an error; nothing is
half-recorded. Each game is recorded as its own game_id.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import chess
import chess.pgn

from .chess_trainer import _best_move_and_cp, _classify

log = logging.getLogger(__name__)

_API = "https://api.chess.com/pub/player/{username}/games/{year}/{month}"


async def _fetch(url: str, timeout: float = 20.0) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _last_archives(username: str, n: int = 3) -> list[str]:
    """The most recent `n` monthly game archives for a player."""
    import httpx

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(
            f"https://api.chess.com/pub/player/{username}/games/archives"
        )
        resp.raise_for_status()
        data = resp.json()
    archives = data.get("archives", [])
    return archives[-n:] if archives else []


def _parse_game_pgn(pgn_text: str) -> tuple[dict[str, Any], chess.Board] | None:
    """Parse a PGN into (headers, board-with-moves). Returns None on failure or
    when the game has no moves.

    NOTE: game.board() does NOT populate move_stack in python-chess 1.11 — the
    moves must be pushed by traversing game.mainline(). (Verified empirically:
    board() returns 0 moves, mainline traversal returns all of them.)"""
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            return None
        board = chess.Board()
        for node in game.mainline():
            if node.move is not None:
                board.push(node.move)
        if not board.move_stack:
            return None
        return dict(game.headers), board
    except Exception as exc:
        log.warning("pgn parse failed: %s", exc)
        return None


def _analyze_game(board: chess.Board, max_plies: int = 40) -> list[dict[str, Any]]:
    """Evaluate up to `max_plies` moves in the game, classifying each. Returns
    per-move records like the trainer's evaluate_move output (uci, san, pre_fen,
    classification, win_delta_pct, best_uci, best_move_san). Bounded so a long
    game can't stall the import (the persistent engine is ~0.8s/eval)."""
    records: list[dict[str, Any]] = []
    moves = list(board.move_stack)[:max_plies]  # Move objects
    replay = chess.Board()
    for mv in moves:
        uci = mv.uci()
        pre_fen = replay.fen()
        before_best, before_cp, _ = _best_move_and_cp(replay)
        san = replay.san(mv)
        replay.push(mv)
        after_cp = _best_move_and_cp(replay)[1]
        mover_after = -after_cp
        was_best = before_best == uci
        classification = _classify(500, before_cp, mover_after, was_best)
        best_san = None
        if before_best:
            try:
                bb = chess.Board(pre_fen)
                best_san = bb.san(chess.Move.from_uci(before_best))
            except Exception:
                pass
        records.append(
            {
                "uci": uci,
                "san": san,
                "pre_fen": pre_fen,
                "fen": replay.fen(),
                "classification": classification,
                "best_uci": before_best,
                "best_move_san": best_san,
                "was_best": was_best,
                "eval_before_cp": round(before_cp, 1),
                "eval_after_cp": round(mover_after, 1),
            }
        )
    return records


async def import_games(
    username: str,
    months: int = 3,
    max_games: int = 30,
    color: str = "both",
    record_mistakes: bool = True,
    record_games: bool = True,
) -> dict[str, Any]:
    """Fetch + analyze a player's recent games and feed the trainer's stores.

    Returns {ok, username, games_fetched, games_analyzed, moves_analyzed,
    mistakes_queued, errors: [...]}."""
    if not username or not username.strip():
        return {"ok": False, "error": "username is required"}
    username = username.strip().lower()
    try:
        archives = await _last_archives(username, n=months)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"could not fetch archives for '{username}': {exc}",
        }
    if not archives:
        return {"ok": False, "error": f"no game archives for '{username}'"}

    games_fetched = 0
    games_analyzed = 0
    moves_analyzed = 0
    mistakes_queued = 0
    errors: list[str] = []

    from .chess_games import record_move, start_game
    from .chess_mistakes import record_mistake

    for url in archives:
        if games_analyzed >= max_games:
            break
        try:
            data = await _fetch(url)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        for game in data.get("games", []):
            if games_analyzed >= max_games:
                break
            games_fetched += 1
            parsed = _parse_game_pgn(game.get("pgn", ""))
            if parsed is None:
                errors.append("unparseable pgn")
                continue
            headers, board = parsed
            # Optional color filter: only keep games where `username` played
            # the requested color.
            if color in ("white", "black"):
                white_name = (headers.get("White") or "").strip().lower()
                black_name = (headers.get("Black") or "").strip().lower()
                is_white = username in white_name
                is_black = username in black_name
                if not (is_white or is_black):
                    continue
                if color == "white" and not is_white:
                    continue
                if color == "black" and not is_black:
                    continue
            # Skip games we don't want (shorter than 4 moves are meaningless).
            if len(board.move_stack) < 8:
                continue
            try:
                # Bound each game's analysis (~40 plies x 2 evals x ~0.8s worst).
                async with asyncio.timeout(120):
                    records = await asyncio.to_thread(_analyze_game, board, 40)
            except Exception as exc:
                errors.append(f"analysis failed: {exc}")
                continue
            moves_analyzed += len(records)

            if record_games:
                from .chess_games import finish_game
                from .chess_trainer import _expected_points

                gid = start_game()["id"]
                for rec in records:
                    win_before = _expected_points(500, rec["eval_before_cp"])
                    win_after = _expected_points(500, rec["eval_after_cp"])
                    record_move(
                        gid,
                        {
                            "uci": rec["uci"],
                            "san": rec["san"],
                            "fen": rec["fen"],
                            "pre_fen": rec["pre_fen"],
                            "classification": rec["classification"],
                            "eval_before_cp": rec["eval_before_cp"],
                            "eval_after_cp": rec["eval_after_cp"],
                            "win_delta_pct": round((win_after - win_before) * 100, 1),
                            "best_uci": rec["best_uci"],
                            "best_move_san": rec["best_move_san"],
                            "is_best": rec["was_best"],
                            "concept": "",
                            "source": f"chess.com:{username}",
                        },
                    )
                # Finalize so the game counts in analytics (only finished games
                # do) — otherwise every imported game stays in_progress forever.
                finish_game(gid)
                games_analyzed += 1

            if record_mistakes:
                for rec in records:
                    if rec["classification"] in ("Mistake", "Blunder", "Inaccuracy"):
                        record_mistake(
                            pre_fen=rec["pre_fen"],
                            played_uci=rec["uci"],
                            played_san=rec["san"],
                            best_uci=rec["best_uci"],
                            best_san=rec["best_move_san"],
                            classification=rec["classification"],
                            concept="imported",
                            book_titles=[],
                        )
                        mistakes_queued += 1

    return {
        "ok": True,
        "username": username,
        "games_fetched": games_fetched,
        "games_analyzed": games_analyzed,
        "moves_analyzed": moves_analyzed,
        "mistakes_queued": mistakes_queued,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Bulk player profile — your weaknesses + strengths from ALL games
# ---------------------------------------------------------------------------
# The personalization core: parse EVERY game (thousands, no engine needed) and
# extract the statistical profile the trainer personalizes from:
#   - openings (which you score well / badly with, by frequency)
#   - color + result splits (white vs black performance)
#   - time controls (bullet/blitz/rapid performance)
#   - rating trend (recent vs older)
#   - clock/think-time per phase (the #1 research finding — time management)
# Persisted to data/chess/profile_<username>.json, fail-closed.

import re
from pathlib import Path

_PROFILE_DIR = Path("data/chess")
_CLOCK_RE = re.compile(r"%clk\s+([\d:.]+)")


def _parse_clock(comment: str) -> float | None:
    """Parse a %clk '0:05:30' (or '5:30') comment into seconds, or None."""
    m = _CLOCK_RE.search(comment or "")
    if not m:
        return None
    parts = m.group(1).split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None
    return None


def _think_time(game) -> list[float]:
    """Per-move think time (seconds) from %clk annotations, or [] if absent."""
    clock = []
    prev = None
    for node in game.mainline():
        t = _parse_clock(node.comment)
        if t is not None:
            if prev is not None:
                think = prev - t
                if think >= 0:
                    clock.append(think)
            prev = t
    return clock


async def build_profile(
    username: str,
    max_archives: int | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """Fetch + bulk-parse ALL of a player's games and compute their profile.

    No engine required (uses chess.com's %clk annotations + results). Returns
    the profile and persists it to data/chess/profile_<username>.json."""
    if not username or not username.strip():
        return {"ok": False, "error": "username is required"}
    username = username.strip().lower()

    # All archives (oldest -> newest), optionally capped.
    try:
        data = await _last_archives(username, n=9999)
    except Exception as exc:
        return {"ok": False, "error": f"could not fetch archives: {exc}"}
    if not data:
        return {"ok": False, "error": f"no game archives for '{username}'"}
    if max_archives:
        data = data[-max_archives:]

    stats = {
        "games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "white_wins": 0,
        "black_wins": 0,
        "openings": {},  # opening -> {games, score_pct}
        "time_controls": {},  # tc -> {games, score_pct}
        "ratings": [],  # [{date, rating, opponent_rating}]
        "think": {"opening": [], "middlegame": [], "endgame": []},
        "results_history": [],
    }
    errors: list[str] = []

    for url in data:
        try:
            month = await _fetch(url)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        for game in month.get("games", []):
            try:
                game_obj = chess.pgn.read_game(io.StringIO(game.get("pgn", "")))
                if game_obj is None:
                    continue
                board = chess.Board()
                for node in game_obj.mainline():
                    if node.move:
                        board.push(node.move)
                if not board.move_stack:
                    continue
            except Exception:
                continue
            stats["games"] += 1
            # Determine the player's color from the PGN headers (the API's
            # "white"/"black" fields are dicts; headers are plain strings).
            white_hdr = (game_obj.headers.get("White") or "").strip().lower()
            black_hdr = (game_obj.headers.get("Black") or "").strip().lower()
            is_white = username in white_hdr
            is_black = username in black_hdr
            # Result from the player's perspective. The API has no white_result:
            # each side dict carries .result ("win"/"timeout"/"agreed"/
            # "repetition"/"stalemate"/"checkmated"/"resigned"/"abandoned").
            white_side = game.get("white") or {}
            black_side = game.get("black") or {}
            white_res = white_side.get("result", "")
            player_result = None
            if is_white:
                if white_res in ("win", "timeout"):
                    player_result = "win"
                elif white_res in ("checkmated", "resigned", "abandoned"):
                    player_result = "loss"
                elif white_res in (
                    "agreed",
                    "repetition",
                    "stalemate",
                    "insufficient",
                    "50move",
                    "timevsinsufficient",
                ):
                    player_result = "draw"
            elif is_black:
                black_res = black_side.get("result", "")
                if black_res in ("win", "timeout"):
                    player_result = "win"
                elif black_res in ("checkmated", "resigned", "abandoned"):
                    player_result = "loss"
                elif black_res in (
                    "agreed",
                    "repetition",
                    "stalemate",
                    "insufficient",
                    "50move",
                    "timevsinsufficient",
                ):
                    player_result = "draw"
            if player_result == "win":
                stats["wins"] += 1
                if is_white:
                    stats["white_wins"] += 1
                else:
                    stats["black_wins"] += 1
            elif player_result == "loss":
                stats["losses"] += 1
            elif player_result == "draw":
                stats["draws"] += 1

            # Time control bucket — use the API's time_class (cleaner than
            # inferring from the control string).
            bucket = game.get("time_class") or "other"
            entry = stats["time_controls"].setdefault(
                bucket, {"games": 0, "score": 0.0}
            )
            entry["games"] += 1
            if player_result == "win":
                entry["score"] += 1.0
            elif player_result == "draw":
                entry["score"] += 0.5

            # Opening (label by first 6 plies; the API also gives ECO but a
            # moves-based label is clearer for a beginner).
            opening_key = " ".join(m.uci() for m in board.move_stack[:6]) or "unknown"
            o = stats["openings"].setdefault(opening_key, {"games": 0, "score": 0.0})
            o["games"] += 1
            if player_result == "win":
                o["score"] += 1.0
            elif player_result == "draw":
                o["score"] += 0.5

            # Rating + result history (for trend). Store result IN the record so
            # rating and result can never diverge.
            date = game.get("end_time") or ""
            my_rating = (
                (game.get("white") or {}).get("rating")
                if is_white
                else (game.get("black") or {}).get("rating")
            )
            opp_rating = (
                (game.get("black") or {}).get("rating")
                if is_white
                else (game.get("white") or {}).get("rating")
            )
            stats["ratings"].append(
                {
                    "date": date,
                    "rating": my_rating,
                    "opp": opp_rating,
                    "result": player_result,
                }
            )

            # Clock / think time by phase (thirds).
            think = _think_time(game_obj)
            if think:
                n = len(think)
                third = max(1, n // 3)
                for label, sl in (
                    ("opening", think[:third]),
                    ("middlegame", think[third : 2 * third]),
                    ("endgame", think[2 * third :]),
                ):
                    stats["think"][label].extend(sl)

    # Derive the report (strengths/weaknesses).
    def _score_pct(d: dict) -> float:
        return (
            round(d.get("score", 0.0) / d["games"] * 100, 1) if d.get("games") else 0.0
        )

    opening_report = [
        {"opening": k, "games": v["games"], "score_pct": _score_pct(v)}
        for k, v in stats["openings"].items()
        if v["games"] >= 3
    ]
    opening_report.sort(key=lambda x: -x["games"])

    tc_report = [
        {"tc": k, "games": v["games"], "score_pct": _score_pct(v)}
        for k, v in stats["time_controls"].items()
        if v["games"] >= 3
    ]

    think_report = {
        phase: (round(sum(x) / len(x), 1) if x else None)
        for phase, x in stats["think"].items()
    }

    # Journey: the FULL rating history (all games, oldest -> newest) so the
    # trainer can show the ups-and-downs. Plus a summary (peak/trough/current,
    # biggest climb/drop) for the dashboard.
    ratings = stats["ratings"]
    journey = [
        {
            "date": r.get("date"),
            "rating": r.get("rating"),
            "opp": r.get("opp"),
            "result": r.get("result"),
        }
        for r in ratings
    ]
    journey_ratings = [r.get("rating") for r in ratings if r.get("rating") is not None]
    journey_summary = {}
    if journey_ratings:
        journey_summary = {
            "first": journey_ratings[0],
            "current": journey_ratings[-1],
            "peak": max(journey_ratings),
            "trough": min(journey_ratings),
            "best_gain": 0,
            "worst_drop": 0,
        }
        prev = journey_ratings[0]
        for r in journey_ratings[1:]:
            delta = r - prev
            journey_summary["best_gain"] = max(journey_summary["best_gain"], delta)
            journey_summary["worst_drop"] = min(journey_summary["worst_drop"], delta)
            prev = r

    profile = {
        "ok": True,
        "username": username,
        "generated_at": __import__("time").time(),
        "games": stats["games"],
        "record": {
            "wins": stats["wins"],
            "losses": stats["losses"],
            "draws": stats["draws"],
            "white_wins": stats["white_wins"],
            "black_wins": stats["black_wins"],
        },
        "opening_report": opening_report[:15],
        "time_controls": tc_report,
        "think_seconds": think_report,
        "journey": journey,
        "journey_summary": journey_summary,
        "errors": errors,
    }

    if record:
        try:
            from pathlib import Path

            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            Path(_PROFILE_DIR, f"profile_{username}.json").write_text(
                __import__("json").dumps(profile, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            profile["errors"].append(f"save failed: {exc}")

    return profile
