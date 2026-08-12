"""Tests for the oral-argument panel-prep moot court
(swarm_os/services/legal/moot.py).

Practitioner-reported technique (flagged UNVERIFIED in the audit); the closest
academic support is CHANCERY (2506.04636). Tests pin the DETERMINISTIC parts:
judge profile distillation from opinion text, generic-profile degradation on
fetch outage, and the bench-session contract (LLM question generation mocked).
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.services.legal.moot import (
    distill_profile, generic_profile, fetch_judge_profile, run_bench, render_bench,
)


def test_distill_profile_detects_judge_concerns():
    text = ("The standard of review is plain error. The defendant failed to "
            "preserve the objection below, so we apply plain error review. "
            "The guidelines calculation and the restitution award are at issue. "
            "Binding precedent controls.")
    topics = distill_profile(text)
    assert topics.get("standard_of_review", 0) >= 2
    assert topics.get("preservation", 0) >= 1
    assert topics.get("sentencing", 0) >= 1
    assert topics.get("precedent", 0) >= 1


def test_distill_profile_empty_text():
    assert distill_profile("") == {}
    assert distill_profile(None) == {}


def test_generic_profile_degrades_gracefully():
    prof = generic_profile("Walker")
    assert prof.name == "Walker"
    assert "standard_of_review" in prof.topics  # neutral fallback


@pytest.mark.asyncio
async def test_fetch_judge_profile_distills_from_opinions():
    """fetch_judge_profile must keep ONLY opinions attributed to the judge (via
    panel/author) and distill their concerns — driving the REAL distillation
    with only the HTTP leg mocked."""
    from types import SimpleNamespace
    mock_resp = SimpleNamespace(status_code=200, json=lambda: {
        "results": [
            {"plain_text": "We review the abuse of discretion standard. The record shows no error.",
             "panel": [{"name": "Walker"}], "author": None},
            {"plain_text": "Sentencing guidelines govern. Binding precedent requires affirmance.",
             "panel": [{"name": "Walker"}], "author": None},
        ]
    })
    with patch("swarm_os.services.legal.moot.httpx.AsyncClient") as mock_cls:
        client_mock = mock_cls.return_value.__aenter__.return_value
        client_mock.get = AsyncMock(return_value=mock_resp)
        prof = await fetch_judge_profile(client_mock, "Walker", max_opinions=2)
    assert prof.opinion_count == 2
    assert "standard_of_review" in prof.topics
    assert "sentencing" in prof.topics
    assert prof.error == ""


@pytest.mark.asyncio
async def test_fetch_judge_profile_skips_non_attributed_opinions():
    """REGRESSION (from the moot hand-walk): the plain opinions fetch returns the
    most RECENT opinions in the whole database, not this judge's. An opinion
    whose panel/author does NOT name the judge must be skipped, and if none are
    attributed the profile must fail-closed with an error (never a garbage
    profile built from another judge's opinions)."""
    from types import SimpleNamespace
    mock_resp = SimpleNamespace(status_code=200, json=lambda: {
        "results": [
            # 3 recent opinions, NONE by Walker (panel is a different judge).
            {"plain_text": "standard of review is abuse of discretion",
             "panel": [{"name": "Raggi"}], "author": None},
            {"plain_text": "sentencing guidelines", "panel": None, "author": {"name": "Lynch"}},
            {"plain_text": "plain error preserved objection", "panel": [{"name": "Pooler"}], "author": None},
        ]
    })
    with patch("swarm_os.services.legal.moot.httpx.AsyncClient") as mock_cls:
        client_mock = mock_cls.return_value.__aenter__.return_value
        client_mock.get = AsyncMock(return_value=mock_resp)
        prof = await fetch_judge_profile(client_mock, "Walker", max_opinions=2)
    assert prof.error == "no_attributed_opinions"
    assert prof.topics == {}
    assert prof.opinion_count == 0


@pytest.mark.asyncio
async def test_fetch_judge_profile_degrades_on_http_error():
    from types import SimpleNamespace
    mock_resp = SimpleNamespace(status_code=429, json=lambda: {})
    with patch("swarm_os.services.legal.moot.httpx.AsyncClient") as mock_cls:
        client_mock = mock_cls.return_value.__aenter__.return_value
        client_mock.get = AsyncMock(return_value=mock_resp)
        prof = await fetch_judge_profile(client_mock, "Walker")
    assert prof.error == "http:429"
    assert prof.topics == {}


@pytest.mark.asyncio
async def test_run_bench_generates_questions_with_mocked_llm():
    """run_bench must generate one bench question per (judge, issue) — driving
    the session assembly with the LLM leg mocked (the established seam)."""
    issues = [{"issue": "plain error"}, {"issue": "restitution"}]
    arg_by = {"plain error": "counsel argues no plain error occurred",
              "restitution": "the award is an abuse of discretion"}
    with patch("swarm_os.services.legal.moot.fetch_judge_profile",
               new=AsyncMock(return_value=generic_profile("Walker"))), \
         patch("swarm_os.services.legal.moot._ask_judge",
               new=AsyncMock(side_effect=[
                   "What is the plain error standard, counsel?",
                   "Why isn't the restitution award an abuse of discretion?",
               ])):
        session = await run_bench(["Walker"], issues, arg_by, ["507 U.S. 725"],
                                  fetch_profiles=False)
    assert len(session.questions) == 2
    assert session.questions[0].judge == "Walker"
    assert session.questions[0].issue == "plain error"
    assert "plain error standard" in session.questions[0].question


def test_render_bench_readable():
    from swarm_os.services.legal.moot import BenchSession, BenchQuestion, JudgeProfile
    session = BenchSession(
        judges=[JudgeProfile(name="Walker", topics={"standard_of_review": 2})],
        questions=[BenchQuestion(judge="Walker", issue="plain error",
                                 question="What is the standard?")],
    )
    out = render_bench(session)
    assert "Judge Walker" in out
    assert "standard_of_review" in out
    assert "What is the standard?" in out
