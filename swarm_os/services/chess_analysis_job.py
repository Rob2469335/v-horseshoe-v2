"""Resumable background chess.com analysis job — every game, every move, best eval.

The heavy pass: processes ALL of a player's games with Stockfish (engine
analysis of every move — the trainer's best-eval seam), feeding every blunder
into the spaced-repetition mistake store and recording games for analytics.

Runs as a background asyncio task so the API never blocks. Designed for a
long unattended run (hours):
  - resumable: progress is persisted after each game; on restart (or app boot)
    an incomplete job resumes from where it left off;
  - bounded memory: one game at a time, progress written per game;
  - fail-closed: per-game errors are recorded and the job continues; a network
    failure pauses (retries later) rather than losing progress.

Job state lives in data/chess/analysis_jobs/<job_id>.json:
  {job_id, username, archives:[url...], next_archive, done_games:int,
   total_games:int, mistakes_queued:int, started_at, updated_at, status}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import chess

from .chess_import import _analyze_game, _fetch, _last_archives, _parse_game_pgn
from .chess_trainer import _best_move_and_cp  # noqa: F401 (engine warm-up)

log = logging.getLogger(__name__)

_JOBS_DIR = Path("data/chess/analysis_jobs")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = asyncio.Lock()

# Max wall-clock per single game analysis. A 100+ move game is genuinely slow,
# but a wedged engine (leaked lock) must never stall the whole run — games
# exceeding this bound are skipped and the job continues.
_GAME_TIMEOUT_S = 300


def _job_path(job_id: str) -> Path:
    return _JOBS_DIR / f"{job_id}.json"


def _record_completion(job: dict[str, Any]) -> None:
    """Append a (done_games, timestamp) point to the job's rolling completion
    log (capped at 20). The slope of these points is the ACTUAL recent rate,
    used for an honest ETA instead of a manual games-per-minute average."""
    completions = job.setdefault("completions", [])
    completions.append([job.get("done_games", 0), time.time()])
    job["completions"] = completions[-20:]


def _rolling_rate(job: dict[str, Any]) -> float | None:
    """Games per minute from a least-squares fit over the recent completion
    points (the last up-to-6 points spanning >= 60s). Using more than two
    points makes the ETA robust to a single slow game — a 2-point window
    swings wildly (0.89 -> 1.99 /min across consecutive games). Returns None
    when there isn't enough recent history yet."""
    completions = job.get("completions") or []
    if len(completions) < 2:
        return None
    pts = completions[-6:]
    t0 = pts[0][1]
    if pts[-1][1] - t0 < 60.0:
        return None  # not enough elapsed time for a stable slope
    # Least-squares slope of done_games vs time (games per second).
    n = len(pts)
    sx = sum(p[1] for p in pts)
    sy = sum(p[0] for p in pts)
    sxx = sum(p[1] * p[1] for p in pts)
    sxy = sum(p[1] * p[0] for p in pts)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return None
    slope = (n * sxy - sx * sy) / denom  # games per second
    if slope <= 0:
        return None
    return slope * 60.0  # games per minute


def _job_eta(job: dict[str, Any]) -> float | None:
    """Estimated hours remaining from the rolling rate (None if unknown)."""
    rate = _rolling_rate(job)
    if not rate:
        return None
    remaining = job.get("total_games", 0) - job.get("done_games", 0)
    if remaining <= 0:
        return 0.0
    return remaining / rate / 60.0


def _load_job(job_id: str) -> dict[str, Any] | None:
    try:
        if _job_path(job_id).exists():
            return json.loads(_job_path(job_id).read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("job load failed: %s", exc)
    return None


def _save_job(job: dict[str, Any]) -> None:
    try:
        _JOBS_DIR.mkdir(parents=True, exist_ok=True)
        # Never persist the in-memory `_games` (full PGN-laden game dicts) or
        # `_live_task` (a live asyncio.Task, not JSON-serializable) — only the
        # light `game_refs` (url, index) survive to disk.
        disk = {k: v for k, v in job.items() if k not in ("_games", "_live_task")}
        path = _job_path(job["job_id"])
        # Atomic write: serialize to a temp file in the same dir, then rename.
        # A crash mid-write otherwise leaves a truncated JSON that fails to load
        # and silently kills resume-on-restart.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(disk), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        log.warning("job save failed: %s", exc)


def list_jobs() -> dict[str, Any]:
    """All known jobs (from disk + memory) with their status + rolling ETA."""
    if not _JOBS_DIR.exists():
        return {"ok": True, "jobs": []}
    out = []
    for p in sorted(_JOBS_DIR.glob("*.json")):
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
            entry = {
                k: job.get(k)
                for k in (
                    "job_id",
                    "username",
                    "status",
                    "done_games",
                    "total_games",
                    "mistakes_queued",
                    "started_at",
                    "updated_at",
                    "error",
                )
            }
            eta_h = _job_eta(job)
            rate = _rolling_rate(job)
            if eta_h is not None:
                entry["eta_hours"] = round(eta_h, 1)
            if rate is not None:
                entry["games_per_minute"] = round(rate, 2)
            out.append(entry)
        except Exception as exc:
            log.warning("job list: skipping unreadable job file %s: %s", p, exc)
            continue
    out.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
    return {"ok": True, "jobs": out}


def job_status(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id) or _load_job(job_id)
    if not job:
        return {"ok": False, "error": "job not found"}
    return {
        "ok": True,
        "job_id": job_id,
        "username": job.get("username"),
        "status": job.get("status"),
        "done_games": job.get("done_games", 0),
        "total_games": job.get("total_games", 0),
        "mistakes_queued": job.get("mistakes_queued", 0),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
        "running": job.get("status") == "running",
    }


async def _collect_games(
    username: str, max_archives: int | None = None
) -> tuple[list[tuple[str, int, dict]], list[str]]:
    """All (url, index, game) across the player's archives (or capped). The
    index lets us persist only light references for resume (not the full PGNs —
    that bloated the job file to MBs and slowed per-game saves)."""
    games: list[tuple[str, int, dict]] = []
    errors: list[str] = []
    try:
        archives = await _last_archives(username, n=9999)
    except Exception as exc:
        return [], [f"archives: {exc}"]
    if max_archives:
        archives = archives[-max_archives:]
    for url in archives:
        try:
            month = await _fetch(url)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        for idx, g in enumerate(month.get("games", [])):
            games.append((url, idx, g))
    return games, errors


async def _analyze_one(username: str, game: dict) -> dict[str, Any]:
    """Analyze one game with the engine (best eval, every move). Returns
    {records, mistakes, error}."""
    parsed = _parse_game_pgn(game.get("pgn", ""))
    if parsed is None:
        return {"records": [], "mistakes": 0, "error": "unparseable pgn"}
    headers, board, game_obj = parsed
    if len(board.move_stack) < 4:
        return {"records": [], "mistakes": 0, "error": None}
    # The user's color from the headers (exact match — substring would misattribute
    # e.g. "rob" in "robert"). Only their moves are recorded / queued as mistakes.
    white_hdr = (headers.get("White") or "").strip().lower()
    black_hdr = (headers.get("Black") or "").strip().lower()
    is_white = white_hdr == username
    is_black = black_hdr == username
    if not (is_white or is_black):
        return {"records": [], "mistakes": 0, "error": None}
    stop_flag = [False]
    try:
        # Pass game_obj so clock/think data survives into bulk records (the
        # import path does; the bulk path previously stripped it).
        async with asyncio.timeout(300):
            records = await asyncio.to_thread(
                _analyze_game, board, 100000, game_obj, stop_flag
            )
    except Exception as exc:
        stop_flag[0] = True
        return {"records": [], "mistakes": 0, "error": f"analysis: {exc}"}

    from .chess_games import record_game, start_game
    from .chess_mistakes import record_mistake
    from .chess_trainer import _expected_points

    # finalize_existing=False: a background bulk job must NOT truncate a live
    # interactive game the user is mid-way through.
    gid = start_game(finalize_existing=False, player_color=("w" if is_white else "b"))[
        "id"
    ]
    game_moves = []
    mistakes = 0
    for i, rec in enumerate(records):
        # Even ply = white mover, odd = black. Skip the opponent's moves — they
        # are not the user's games/errors (data pollution otherwise).
        if i % 2 != (0 if is_white else 1):
            continue
        # Real win-delta from the evals (mover's perspective on both).
        win_before = _expected_points(500, rec["eval_before_cp"])
        win_after = _expected_points(500, rec["eval_after_cp"])
        win_delta = round((win_after - win_before) * 100, 1)
        game_moves.append(
            {
                "uci": rec["uci"],
                "san": rec["san"],
                "fen": rec["fen"],
                "pre_fen": rec["pre_fen"],
                "classification": rec["classification"],
                "eval_before_cp": rec["eval_before_cp"],
                "eval_after_cp": rec["eval_after_cp"],
                "win_delta_pct": win_delta,
                "best_uci": rec["best_uci"],
                "best_move_san": rec["best_move_san"],
                "is_best": rec["was_best"],
                "concept": "",
                "source": f"chess.com:{username}",
            }
        )

        if rec["classification"] in ("Mistake", "Blunder", "Inaccuracy"):
            from .chess_book_memory import _concept_from

            concept = _concept_from(
                rec["classification"], f"{rec['uci']} {rec['best_uci'] or ''}"
            )

            think = rec.get("think_time_secs")

            # Extract 3-5 lead in moves
            start_idx = max(0, i - 4)
            lead_in_moves = [
                {
                    "fen": r["pre_fen"],
                    "san": r["san"],
                    "uci": r["uci"],
                }
                for r in records[start_idx:i]
            ]

            record_mistake(
                pre_fen=rec["pre_fen"],
                played_uci=rec["uci"],
                played_san=rec["san"],
                best_uci=rec["best_uci"],
                best_san=rec["best_move_san"],
                classification=rec["classification"],
                concept=concept,
                book_titles=[],
                clock_remaining_secs=rec.get("clock_remaining_secs"),
                think_time_secs=think,
                impulse_blunder=(think is not None and think < 3.0),
                lead_in_moves=lead_in_moves,
            )
            mistakes += 1
    # Persist the whole game ONCE (finished) — the bulk path avoids the per-move
    # whole-file rewrites the old record_move did. Only finished games count in
    # analytics, and a finished game can never be re-advanced.
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
    return {"records": records, "mistakes": mistakes, "error": None}


async def _run_job(job: dict[str, Any]) -> None:
    try:
        await _run_job_impl(job)
    except Exception as exc:
        log.error(
            "Fatal error in background analysis job %s: %s", job.get("job_id"), exc
        )
        job["status"] = "error"
        job["error"] = f"Fatal crash: {exc}"
        _save_job(job)


async def _run_job_impl(job: dict[str, Any]) -> None:
    """Process every game in the job, updating progress + persisting per game.
    Only lightweight references (url, index) are persisted — the full game
    dicts live in memory and are re-fetched on resume."""
    username = job["username"]
    # Collect all games (or resume from stored refs). On resume the job has
    # `game_refs` but no `games` — only collect when NEITHER is present
    # (otherwise resume re-fetches everything and resets done_games to 0).
    if "games" not in job and "game_refs" not in job:
        games, errs = await _collect_games(username, job.get("max_archives"))
        if not games:
            job["status"] = "error"
            job["error"] = "no games found" + (f": {errs[0]}" if errs else "")
            _save_job(job)
            return
        # Persist only refs (url, index) — NOT the PGN-laden game dicts.
        job["game_refs"] = [(u, i) for u, i, _ in games]
        job["total_games"] = len(games)
        job["done_games"] = 0
        job["mistakes_queued"] = 0
        # Keep full games in memory only.
        job["_games"] = games
        _save_job(job)

    # Restore the in-memory game list (from memory or by refetching on resume).
    # Only fetch the REMAINING games (done_games onward), and fetch by archive
    # once (not per-game) — the old path re-fetched every game individually
    # on resume, which was minutes of sequential HTTP for a partially-done run.
    if "_games" not in job:
        by_gi: dict[int, tuple[str, int, dict]] = {}
        refs = job.get("game_refs", [])
        start = job.get("done_games", 0)
        # Group remaining refs by archive URL so we fetch each archive once.
        by_url: dict[str, list[tuple[int, int]]] = {}
        for gi in range(start, len(refs)):
            url, idx = refs[gi]
            by_url.setdefault(url, []).append((gi, idx))
        for url, items in by_url.items():
            try:
                month = await _fetch(url)
                games = month.get("games", [])
            except Exception as exc:
                log.warning("resume fetch failed %s: %s", url, exc)
                games = []
            for gi, idx in items:
                if 0 <= idx < len(games):
                    by_gi[gi] = (url, idx, games[idx])
                else:
                    by_gi[gi] = (url, idx, {})  # placeholder -> skip
        # Build _games in global order (games[i] aligns with done_games).
        job["_games"] = [by_gi[i] for i in range(start, len(refs))]

    games = job["_games"]  # indexed from 0, aligned with done_games offset
    start = job.get("done_games", 0)
    for local_i, (_, _, game) in enumerate(games):
        global_i = start + local_i
        job["status"] = "running"
        job["updated_at"] = time.time()
        try:
            if not game:
                # Refetch failed on resume; skip.
                job["done_games"] = global_i + 1
                _save_job(job)
                continue
            async with asyncio.timeout(_GAME_TIMEOUT_S):
                res = await _analyze_one(username, game)
            job["done_games"] = global_i + 1
            if res["mistakes"]:
                job["mistakes_queued"] = job.get("mistakes_queued", 0) + res["mistakes"]
            _record_completion(job)
        except asyncio.CancelledError:
            job.pop("_live_task", None)
            _save_job(job)
            raise
        except TimeoutError:
            # A single wedged game (e.g. the shared engine lock leaked by a
            # cancelled thread) must not stall the whole run — skip it, log it,
            # continue. The lock-acquire timeout in chess_trainer makes the
            # next eval fail-open, so we don't stay wedged.
            job["errors"] = job.get("errors", [])
            job["errors"].append(
                f"game {global_i}: timed out after {_GAME_TIMEOUT_S}s (skipped)"
            )
            job["done_games"] = global_i + 1
        except Exception as exc:
            job["errors"] = job.get("errors", [])
            job["errors"].append(f"game {global_i}: {exc}")
            job["done_games"] = global_i + 1
        # Save every 5 games (not every game) to avoid thrashing a growing file.
        if (global_i + 1) % 5 == 0 or local_i == len(games) - 1:
            _save_job(job)

    job["status"] = "done"
    job["updated_at"] = time.time()
    job.pop("_live_task", None)
    # Drop the full PGN-laden game dicts once the run finishes — they'd otherwise
    # be retained in memory for the process lifetime (the disk copy only ever
    # holds the light `game_refs`). A future resume re-fetches from refs.
    job.pop("_games", None)
    _save_job(job)


async def start_analysis(
    username: str,
    max_archives: int | None = None,
    auto_start: bool = True,
) -> dict[str, Any]:
    """Start (or resume) the background analysis job for a player."""
    username = (username or "").strip().lower()
    if not username:
        return {"ok": False, "error": "username is required"}

    async with _jobs_lock:
        # If a running/incomplete job exists for this username, resume it.
        existing = None
        for job in list_jobs().get("jobs", []):
            if job.get("username") == username and job.get("status") in (
                "running",
                "paused",
            ):
                existing = job.get("job_id")
                break
        if existing:
            job = _load_job(existing)
            # If this job already has a LIVE task in THIS process, don't spawn a
            # duplicate (it would process the same games concurrently). Only
            # (re)start when no in-memory task is running - a FINISHED or crashed
            # task still evaluates truthy, so check done() explicitly (otherwise a
            # dead task would block resuming a failed run forever).
            live_task = _jobs.get(existing) and _jobs[existing].get("_live_task")
            already_live = bool(live_task and not live_task.done())
            if (
                auto_start
                and job
                and job.get("status") in ("running", "paused")
                and not already_live
            ):
                _jobs[existing] = job
                task = asyncio.create_task(_run_job(job))
                job["_live_task"] = task
            return {"ok": True, "job_id": existing, "resumed": True}

        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "username": username,
            "max_archives": max_archives,
            "status": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
            "done_games": 0,
            "total_games": 0,
            "mistakes_queued": 0,
            "errors": [],
        }
        _jobs[job_id] = job
        _save_job(job)
        if auto_start:
            task = asyncio.create_task(_run_job(job))
            job["_live_task"] = task
        return {"ok": True, "job_id": job_id, "resumed": False}


async def resume_incomplete() -> None:
    """On app boot, resume any job left in 'running'/'paused' state from a
    prior process (crash/restart) so a long 24h run continues."""
    for job in list_jobs().get("jobs", []):
        if job.get("status") in ("running", "paused"):
            j = _load_job(job.get("job_id"))
            if j:
                _jobs[j["job_id"]] = j
                task = asyncio.create_task(_run_job(j))
                j["_live_task"] = task
                log.info(
                    "resumed chess analysis job %s (%s)", j["job_id"], j["username"]
                )
