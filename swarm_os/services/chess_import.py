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
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, TypedDict

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


def _parse_game_pgn(
    pgn_text: str,
) -> tuple[dict[str, Any], chess.Board, chess.pgn.Game] | None:
    """Parse a PGN into (headers, board-with-moves). Returns None on failure or
    when the game has no moves.

    Honors the [FEN ...] header (custom start positions / Chess960): the board
    is built FROM the header FEN, not the standard start, so the mainline moves
    replay the actual position (pushing onto a standard board would either crash
    with 'illegal san' or silently produce a wrong position).

    NOTE: game.board() does NOT populate move_stack in python-chess 1.11 — the
    moves must be pushed by traversing game.mainline(). (Verified empirically:
    board() returns 0 moves, mainline traversal returns all of them.)"""
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            return None
        board = game.board()
        for node in game.mainline():
            if node.move is not None:
                board.push(node.move)
        if not board.move_stack:
            return None
        return dict(game.headers), board, game
    except Exception as exc:
        log.warning("pgn parse failed: %s", exc)
        return None


def _analyze_game(
    board: chess.Board,
    max_plies: int = 100000,
    game_obj: chess.pgn.Game | None = None,
    stop_flag: list[bool] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate up to `max_plies` moves in the game, classifying each. Returns
    per-move records like the trainer's evaluate_move output (uci, san, pre_fen,
    classification, win_delta_pct, best_uci, best_move_san). Bounded so a long
    game can't stall the import (the persistent engine is ~0.8s/eval)."""
    records: list[dict[str, Any]] = []
    moves = list(board.move_stack)[:max_plies]  # Move objects
    replay = chess.Board()
    clock_remaining = []
    think_times = []
    if game_obj:
        # Track each color's clock independently. chess.com's %clk is the time
        # remaining for the player WHO JUST MOVED, so consecutive nodes alternate
        # colors — a single `prev_t` mixes white's clock with black's and produces
        # garbage think-times (verified: Nf3 probed at 20s when it was 10s). Think
        # time for a move = that player's own clock before minus after.
        prev_clocks: dict[bool, float | None] = {chess.WHITE: None, chess.BLACK: None}
        for node in game_obj.mainline():
            t = _parse_clock(node.comment)
            clock_remaining.append(t)
            mover_color = node.turn()
            prev_t = prev_clocks[mover_color]
            if t is not None and prev_t is not None:
                think_times.append(max(0.0, prev_t - t))
            else:
                think_times.append(None)
            if t is not None:
                prev_clocks[mover_color] = t

    for i, mv in enumerate(moves):
        if stop_flag and stop_flag[0]:
            break
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
            except Exception as exc:
                log.debug(
                    "best_san conversion failed for %s/%s: %s",
                    before_best,
                    pre_fen,
                    exc,
                )
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
                "clock_remaining_secs": clock_remaining[i]
                if i < len(clock_remaining)
                else None,
                "think_time_secs": think_times[i] if i < len(think_times) else None,
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

    from .chess_games import start_game
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
            headers, board, game_obj = parsed
            # Determine the user's color ONCE per game with EXACT username
            # matching (substring matching misattributes: "rob" in "robert").
            # This color also drives which moves get recorded to the game store
            # and the mistake queue — the opponent's mistakes must NEVER pollute
            # the user's training queue or inflate their rating.
            white_name = (headers.get("White") or "").strip().lower()
            black_name = (headers.get("Black") or "").strip().lower()
            is_white = white_name == username
            is_black = black_name == username
            if not (is_white or is_black):
                # Not a game the user played — skip entirely (also guards the
                # no-color-match case from queueing stranger moves).
                continue
            # Optional color filter: only keep games where `username` played
            # the requested color.
            if color == "white" and not is_white:
                continue
            if color == "black" and not is_black:
                continue
            # Skip games we don't want (shorter than 4 moves are meaningless).
            if len(board.move_stack) < 8:
                continue
            try:
                # Bound each game's analysis (~40 plies x 2 evals x ~0.8s worst).
                stop_flag = [False]
                try:
                    async with asyncio.timeout(600):
                        records = await asyncio.to_thread(
                            _analyze_game, board, 100000, game_obj, stop_flag
                        )
                except Exception as exc:
                    stop_flag[0] = True
                    raise exc
            except Exception as exc:
                errors.append(f"analysis failed: {exc}")
                continue
            moves_analyzed += len(records)

            if record_games:
                from .chess_games import start_game
                from .chess_trainer import _expected_points

                # finalize_existing=False: a background import must NOT truncate
                # a live interactive game the user is mid-way through. The game
                # is built fully in memory then written once (record_game) — the
                # old per-move record_move rewrote the whole file per move.
                gid = start_game(
                    finalize_existing=False, player_color=("w" if is_white else "b")
                )["id"]
                game_moves = []
                for i, rec in enumerate(records):
                    # Only the user's own moves belong in their recorded game
                    # (even ply index = white, odd = black; ply i is the mover).
                    if i % 2 != (0 if is_white else 1):
                        continue
                    win_before = _expected_points(500, rec["eval_before_cp"])
                    win_after = _expected_points(500, rec["eval_after_cp"])
                    game_moves.append(
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
                        }
                    )
                # Persist the whole game ONCE (finished) — the bulk path avoids
                # the per-move whole-file rewrites the old record_move did.
                from .chess_games import record_game

                record_game(
                    {
                        "id": gid,
                        "start_fen": headers.get("FEN", chess.STARTING_FEN),
                        "started_at": time.time(),
                        "ended_at": time.time(),
                        "status": "finished",
                        "player_color": "w" if is_white else "b",
                        "source": f"chess.com:{username}",
                        "moves": game_moves,
                    }
                )
                games_analyzed += 1

            if record_mistakes:
                for i, rec in enumerate(records):
                    # Only the user's own mistakes enter their review queue —
                    # the opponent's mistakes are the user's OPPORTUNITIES, not
                    # their blunders (ply i is the mover: even=white, odd=black).
                    if i % 2 != (0 if is_white else 1):
                        continue
                    if rec["classification"] in ("Mistake", "Blunder", "Inaccuracy"):
                        think = rec.get("think_time_secs")
                        record_mistake(
                            pre_fen=rec["pre_fen"],
                            played_uci=rec["uci"],
                            played_san=rec["san"],
                            best_uci=rec["best_uci"],
                            best_san=rec["best_move_san"],
                            classification=rec["classification"],
                            concept="imported",
                            book_titles=[],
                            clock_remaining_secs=rec.get("clock_remaining_secs"),
                            think_time_secs=think,
                            impulse_blunder=(think is not None and think < 3.0),
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

_PROFILE_DIR = Path("data/chess")
_CLOCK_RE = re.compile(r"%clk\s+([\d:.]+)")
_USERNAME_FILE = _PROFILE_DIR / "last_username.json"


def save_last_username(username: str) -> dict[str, Any]:
    """Persist the last chess.com username (survives any browser/origin)."""
    username = (username or "").strip()
    if not username:
        return {"ok": False, "error": "username is required"}
    try:
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        _USERNAME_FILE.write_text(
            json.dumps({"username": username.lower()}), encoding="utf-8"
        )
        return {"ok": True, "username": username.lower()}
    except Exception as exc:
        log.warning("username save failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def get_last_username() -> dict[str, Any]:
    """The last saved chess.com username (or None)."""
    try:
        if _USERNAME_FILE.exists():
            return {
                "ok": True,
                "username": json.loads(_USERNAME_FILE.read_text(encoding="utf-8")).get(
                    "username"
                ),
            }
    except Exception as exc:
        log.warning("username load failed: %s", exc)
    return {"ok": True, "username": None}


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


class BuildProfileStats(TypedDict):
    games: int
    wins: int
    losses: int
    draws: int
    white_wins: int
    black_wins: int
    openings: dict[str, dict[str, int]]
    time_controls: dict[str, dict[str, int]]
    ratings: list[dict[str, int | str]]
    think: dict[str, list[int]]
    results_history: list[dict[str, int]]


def _think_time(game) -> list[float]:
    """Per-move think time (seconds) from %clk annotations, or [] if absent."""
    clock = []
    prev_clocks = {chess.WHITE: None, chess.BLACK: None}
    for node in game.mainline():
        t = _parse_clock(node.comment)
        mover_color = node.turn()
        prev_t = prev_clocks[mover_color]
        if t is not None and prev_t is not None:
            think = max(0.0, prev_t - t)
            clock.append(think)
        else:
            clock.append(None)
        if t is not None:
            prev_clocks[mover_color] = t
    return [c for c in clock if c is not None]


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

    stats: BuildProfileStats = {
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
            except Exception as exc:
                log.warning("game pgn parse failed (%s): %s", game.get("id"), exc)
                continue
            stats["games"] += 1
            # Determine the player's color from the PGN headers (the API's
            # "white"/"black" fields are dicts; headers are plain strings).
            white_hdr = (game_obj.headers.get("White") or "").strip().lower()
            black_hdr = (game_obj.headers.get("Black") or "").strip().lower()
            is_white = white_hdr == username
            is_black = black_hdr == username
            # Result from the player's perspective. The API has no white_result:
            # each side dict carries .result ("win"/"timeout"/"agreed"/
            # "repetition"/"stalemate"/"checkmated"/"resigned"/"abandoned").
            white_side = game.get("white") or {}
            black_side = game.get("black") or {}
            white_res = white_side.get("result", "")
            player_result = None
            if is_white:
                if white_res == "win":
                    player_result = "win"
                elif white_res in ("checkmated", "resigned", "abandoned", "timeout"):
                    # "timeout" means THIS player ran out of time -> a loss for
                    # them (the side dict's result is from that side's view).
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
                if black_res == "win":
                    player_result = "win"
                elif black_res in ("checkmated", "resigned", "abandoned", "timeout"):
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

            # Opening: extract readable name from chess.com ECOUrl header or Opening header
            eco_url = game_obj.headers.get("ECOUrl", "")
            opening_name = game_obj.headers.get("Opening", "")

            if eco_url:
                slug = eco_url.strip("/").split("/")[-1]
                opening_key = slug.replace("-", " ")
            elif opening_name:
                opening_key = opening_name
            else:
                opening_key = (
                    " ".join(m.uci() for m in board.move_stack[:6]) or "unknown"
                )

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
