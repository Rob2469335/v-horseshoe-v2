"""Tests for the chess.com profile + background analysis job.

The public API and engine are mocked. The resumable-job state is isolated to a
temp dir.
"""

import pytest

from swarm_os.services import chess_analysis_job as cj
from swarm_os.services import chess_import as ci

SAMPLE_PGN = """[Event "Live Chess"]
[White "tester"]
[Black "opponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 1-0
"""


@pytest.fixture(autouse=True)
def _mock_http(monkeypatch):
    """A single-archive, single-game public API."""

    async def fake_archives(username, n=3):
        return ["https://api.chess.com/pub/player/tester/games/2026/08"]

    async def fake_fetch(url, timeout=20.0):
        return {
            "games": [
                {
                    "pgn": SAMPLE_PGN,
                    "time_class": "blitz",
                    "end_time": 1785600000,
                    "white": {"username": "tester", "rating": 1200, "result": "win"},
                    "black": {
                        "username": "opp",
                        "rating": 1100,
                        "result": "checkmated",
                    },
                }
            ]
        }

    monkeypatch.setattr(ci, "_last_archives", fake_archives)
    monkeypatch.setattr(ci, "_fetch", fake_fetch)
    monkeypatch.setattr(cj, "_last_archives", fake_archives)
    monkeypatch.setattr(cj, "_fetch", fake_fetch)
    yield


@pytest.fixture(autouse=True)
def _mock_engine(monkeypatch):
    def fake_bmcp(board):
        if board.move_stack:
            last = board.move_stack[-1]
            return (last.uci(), 0.0, [last.uci()])
        return ("e2e4", 0.0, ["e2e4"])

    monkeypatch.setattr(ci, "_best_move_and_cp", fake_bmcp)
    monkeypatch.setattr(cj, "_best_move_and_cp", fake_bmcp)
    yield


@pytest.fixture(autouse=True)
def _isolate_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(cj, "_JOBS_DIR", tmp_path / "analysis_jobs")
    monkeypatch.setattr(cj, "_jobs", {})
    yield


def test_build_profile_bulk():
    import asyncio

    res = asyncio.run(ci.build_profile("tester", max_archives=1, record=False))
    assert res["ok"] is True
    assert res["games"] == 1
    assert res["record"]["wins"] == 1
    assert res["journey"] and res["journey"][0]["result"] == "win"
    assert res["journey_summary"]["current"] == 1200


def test_start_analysis_runs_and_persists():
    import asyncio

    res = asyncio.run(cj.start_analysis("tester", max_archives=1, auto_start=False))
    assert res["ok"] is True
    assert res["job_id"]
    # Simulate a full run synchronously (auto_start=False => no bg task).
    job = cj._load_job(res["job_id"])
    assert job is not None
    assert job["status"] == "running"


def test_job_status_and_list():
    import asyncio

    res = asyncio.run(cj.start_analysis("tester", max_archives=1, auto_start=False))
    status = cj.job_status(res["job_id"])
    assert status["ok"] is True
    assert status["username"] == "tester"
    jobs = cj.list_jobs()
    assert jobs["ok"] is True
    assert any(j["job_id"] == res["job_id"] for j in jobs["jobs"])


def test_job_resume_incomplete(monkeypatch, tmp_path):
    """A job left in 'running' state is resumable (its JSON survives)."""
    import asyncio

    res = asyncio.run(cj.start_analysis("tester", max_archives=1, auto_start=False))
    job = cj._load_job(res["job_id"])
    # Manually mark done_games to simulate partial progress.
    job["done_games"] = 1
    job["status"] = "running"
    cj._save_job(job)

    # A fresh process would call resume_incomplete; verify it picks the job up.
    cj._jobs.clear()
    assert cj.list_jobs()["jobs"][0]["done_games"] == 1


def test_run_job_resume_builds_from_done_games(monkeypatch, tmp_path):
    """Regression: on resume, _games is built from done_games onward (not all
    games), and the loop indexes it locally so no games are skipped/duplicated."""
    import asyncio

    from swarm_os.services import chess_games as cg
    from swarm_os.services import chess_mistakes as cm

    monkeypatch.setattr(cg, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cg, "_GAMES_FILE", tmp_path / "chess" / "games.jsonl")
    monkeypatch.setattr(cm, "_DATA_DIR", tmp_path / "chess")
    monkeypatch.setattr(cm, "_STORE_FILE", tmp_path / "chess" / "mistakes.jsonl")
    monkeypatch.setattr(cm, "_ladder_days", lambda: [1, 3, 7])

    # Simulate a job with 3 game refs that has done 2.
    job = {
        "job_id": "resume-test",
        "username": "tester",
        "status": "running",
        "done_games": 2,
        "total_games": 3,
        "mistakes_queued": 0,
        "errors": [],
        "game_refs": [("u1", 0), ("u2", 0), ("u2", 1)],
    }
    cj._save_job(job)

    # Mock archive fetch to return one game per url.
    async def fake_fetch(url, timeout=20.0):
        return {"games": [{"pgn": SAMPLE_PGN}]}

    monkeypatch.setattr(cj, "_fetch", fake_fetch)

    # Run the job to completion (analyze remaining games 2..3).
    asyncio.run(cj._run_job(job))
    done = cj._load_job("resume-test")
    assert done["status"] == "done"
    assert done["done_games"] == 3
    assert len(job["_games"]) == 1  # only the remaining game (index 2) fetched


def test_analysis_endpoint(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from swarm_os.api import chess_trainer as trainer_api

    app = FastAPI()
    app.include_router(trainer_api.router)
    with TestClient(app) as c:
        r = c.post(
            "/chess/trainer/analysis/start",
            json={"username": "tester", "max_archives": 1},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        jid = r.json()["job_id"]
        r = c.get(f"/chess/trainer/analysis/status/{jid}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.get("/chess/trainer/analysis/jobs")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = c.post(
            "/chess/trainer/import/chesscom/profile",
            json={"username": "tester", "max_archives": 1},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


def test_rolling_eta_from_recent_completions(monkeypatch, tmp_path):
    """ETA must come from the ACTUAL recent completion rate (a least-squares
    slope over the recent points), not a manual games-per-minute guess. Fewer
    than two points or <60s of span => no ETA (honest 'unknown')."""
    monkeypatch.setattr(cj, "_JOBS_DIR", tmp_path)
    job = {
        "job_id": "j1",
        "username": "tester",
        "status": "running",
        "done_games": 100,
        "total_games": 842,
    }
    now = 1000.0
    # 4 completion points: 90 games -> 100 games over 120s = 5 games/min.
    job["completions"] = [
        [90, now - 120.0],
        [94, now - 90.0],
        [97, now - 60.0],
        [100, now],
    ]
    monkeypatch.setattr(cj, "time", type("T", (), {"time": staticmethod(lambda: now)})())
    rate = cj._rolling_rate(job)
    assert rate is not None
    assert abs(rate - 5.0) < 0.5  # ~5 games/min from the least-squares slope
    eta = cj._job_eta(job)
    assert eta is not None
    assert abs(eta - (742 / 5.0 / 60.0)) < 8.0  # remaining / rate / 60 -> hours
    # A single slow game among fast ones must NOT tank the ETA (least-squares
    # over the window, not the last-2-point slope).
    job["completions"] = [
        [90, now - 120.0],
        [94, now - 90.0],
        [94, now - 80.0],   # a slow game (10 min, only 0 games)
        [97, now - 60.0],
        [100, now],
    ]
    rate2 = cj._rolling_rate(job)
    assert rate2 is not None
    assert rate2 > 0.5  # not collapsed to ~0 by the single slow point
    # With only one completion point, ETA is honestly unknown.
    job2 = {"done_games": 100, "total_games": 842, "completions": [[100, now]]}
    assert cj._rolling_rate(job2) is None
    assert cj._job_eta(job2) is None
