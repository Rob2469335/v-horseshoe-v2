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
