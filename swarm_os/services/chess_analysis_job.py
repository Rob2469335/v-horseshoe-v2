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

from .chess_import import _analyze_game, _fetch, _last_archives, _parse_game_pgn
from .chess_trainer import _best_move_and_cp  # noqa: F401 (engine warm-up)

log = logging.getLogger(__name__)

_JOBS_DIR = Path("data/chess/analysis_jobs")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = asyncio.Lock()


def _job_path(job_id: str) -> Path:
    return _JOBS_DIR / f"{job_id}.json"


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
        _job_path(job["job_id"]).write_text(json.dumps(disk), encoding="utf-8")
    except Exception as exc:
        log.warning("job save failed: %s", exc)


def list_jobs() -> dict[str, Any]:
    """All known jobs (from disk + memory) with their status."""
    if not _JOBS_DIR.exists():
        return {"ok": True, "jobs": []}
    out = []
    for p in sorted(_JOBS_DIR.glob("*.json")):
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
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
            )
        except Exception:
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
    headers, board = parsed
    if len(board.move_stack) < 4:
        return {"records": [], "mistakes": 0, "error": None}
    try:
        records = await asyncio.to_thread(_analyze_game, board, max_plies=100000)
    except Exception as exc:
        return {"records": [], "mistakes": 0, "error": f"analysis: {exc}"}

    from .chess_games import finish_game, record_move, start_game
    from .chess_mistakes import record_mistake
    from .chess_trainer import _expected_points

    gid = start_game()["id"]
    mistakes = 0
    for rec in records:
        # Real win-delta from the evals (mover's perspective on both).
        win_before = _expected_points(500, rec["eval_before_cp"])
        win_after = _expected_points(500, rec["eval_after_cp"])
        win_delta = round((win_after - win_before) * 100, 1)
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
                "win_delta_pct": win_delta,
                "best_uci": rec["best_uci"],
                "best_move_san": rec["best_move_san"],
                "is_best": rec["was_best"],
                "concept": "",
                "source": f"chess.com:{username}",
            },
        )
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
            mistakes += 1
    # Finalize the game so it counts in analytics (only finished games do) —
    # otherwise every imported game stays in_progress forever.
    finish_game(gid)
    return {"records": records, "mistakes": mistakes, "error": None}


async def _run_job(job: dict[str, Any]) -> None:
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
            res = await _analyze_one(username, game)
            job["done_games"] = global_i + 1
            if res["mistakes"]:
                job["mistakes_queued"] = job.get("mistakes_queued", 0) + res["mistakes"]
        except asyncio.CancelledError:
            job.pop("_live_task", None)
            _save_job(job)
            raise
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
        # If this job already has a live task in THIS process, don't spawn a
        # duplicate (it would process the same games concurrently). Only
        # (re)start when no in-memory task is running.
        already_live = _jobs.get(existing) and job and _jobs[existing].get("_live_task")
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
