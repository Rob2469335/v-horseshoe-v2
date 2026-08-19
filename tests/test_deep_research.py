"""Tests for the fan-out + iterative deep research service.

The real web-search providers and LLM endpoints are never hit — the service's
seams (`deep_research._complete`, `web_search_handler`/`web_fetch_handler`)
are mocked so the orchestration logic is exercised deterministically.
"""

import json

from swarm_os.services import deep_research as dr


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_extract_json_array_fenced():
    assert dr._extract_json_array('```json\n["a", "b"]\n```') == ["a", "b"]


def test_extract_json_array_with_prose_and_trailing_comma():
    assert dr._extract_json_array('Here you go:\n["a", "b",]\nthanks') == ["a", "b"]


def test_extract_json_array_empty_or_none():
    assert dr._extract_json_array("") is None
    assert dr._extract_json_array("no array here") is None


def test_clean_sub_questions_accepts_dicts_and_strings():
    items = ["alpha", {"question": "beta"}, {"query": "gamma"}, ""]
    assert dr._clean_sub_questions(items, "fallback") == ["alpha", "beta", "gamma"]


def test_clean_sub_questions_falls_back_to_goal():
    assert dr._clean_sub_questions([], "the goal") == ["the goal"]


def test_compact_questions_dedupes_and_caps():
    qs = ["what is x", "What is X", "what is y", "what is z", "what is w"]
    assert dr._compact_questions(qs, 3) == ["what is x", "what is y", "what is z"]


def test_final_synthesis_renumbers_citations_across_units(monkeypatch):
    """Citations from unit A ([1][2]) and unit B ([1]) must be renumbered into
    one flat list ([1][2][3]) before the final prompt."""
    captured = {}

    async def fake_complete(prompt, **kw):
        captured["prompt"] = prompt
        return "FINAL"

    monkeypatch.setattr(dr, "_complete", fake_complete)
    reports = [
        {
            "question": "q1",
            "answer": "A says [1] and [2]",
            "citations": [
                {"n": 1, "title": "a1", "url": "http://a1"},
                {"n": 2, "title": "a2", "url": "http://a2"},
            ],
        },
        {
            "question": "q2",
            "answer": "B says [1]",
            "citations": [{"n": 1, "title": "b1", "url": "http://b1"}],
        },
    ]
    answer, flat = asyncio_run(dr._final_synthesis("goal", reports))
    assert answer == "FINAL"
    assert len(flat) == 3
    # The renumbered answers are embedded in the final prompt.
    assert "A says [1] and [2]" in captured["prompt"]
    assert "B says [3]" in captured["prompt"]
    assert "[1] a1" in captured["prompt"]
    assert "[3] b1" in captured["prompt"]


# ---------------------------------------------------------------------------
# Orchestration (mocked LLM + search)
# ---------------------------------------------------------------------------
def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def _install_mocks(monkeypatch, plan_out, gap_out, complete_reply):
    async def fake_complete(prompt, **kw):
        if "research director" in prompt:
            return json.dumps(plan_out)
        if "GAPS" in prompt or "partially-completed" in prompt:
            return json.dumps(gap_out)
        return complete_reply

    async def fake_search(params):
        q = params["query"]
        return {
            "ok": True,
            "results": [
                {
                    "url": f"http://src/{q[:8]}",
                    "title": f"Title {q}",
                    "snippet": f"snippet for {q}",
                }
            ],
        }

    async def fake_fetch(params):
        return {"ok": True, "text": f"deep content for {params['url']}"}

    monkeypatch.setattr(dr, "_complete", fake_complete)
    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_search_handler", fake_search)
    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_fetch_handler", fake_fetch)


def test_deep_research_runs_fanout_and_synthesis(monkeypatch):
    _install_mocks(
        monkeypatch,
        plan_out=["q1", "q2", "q3"],
        gap_out="[]",
        complete_reply="Final cited report [1] [2]",
    )
    res = asyncio_run(
        dr.deep_research("research topic", max_sub_questions=3, max_iterations=1)
    )
    assert res["status"] == "ok"
    assert res["sub_questions"] == ["q1", "q2", "q3"]
    assert len(res["sub_reports"]) == 3
    assert all(r["answer"] == "Final cited report [1] [2]" for r in res["sub_reports"])
    assert res["answer"] == "Final cited report [1] [2]"
    assert len(res["citations"]) == 3  # one source per unit, renumbered


def test_deep_research_iterates_on_gaps(monkeypatch):
    """When the gap evaluator returns follow-ups, a second fan-out runs and the
    sub_reports contain the initial units plus the follow-up units."""
    _install_mocks(
        monkeypatch,
        plan_out=["q1", "q2"],
        gap_out=["followup"],
        complete_reply="answer",
    )
    res = asyncio_run(
        dr.deep_research("research topic", max_sub_questions=2, max_iterations=1)
    )
    assert res["sub_questions"] == ["q1", "q2", "followup"]
    assert len(res["sub_reports"]) == 3
    assert res["iterations"] == 2


def test_deep_research_degrades_when_plan_fails(monkeypatch):
    """If the planner can't return JSON, the whole goal becomes the single
    sub-question — research still happens, never a fabricated answer."""
    _install_mocks(
        monkeypatch, plan_out="not json", gap_out="[]", complete_reply="answer"
    )
    res = asyncio_run(
        dr.deep_research("research topic", max_sub_questions=3, max_iterations=1)
    )
    assert res["sub_questions"] == ["research topic"]
    assert len(res["sub_reports"]) == 1
    assert res["status"] == "ok"


def test_deep_research_empty_goal_fails_closed():
    res = asyncio_run(dr.deep_research("   "))
    assert res["status"] == "error"


def test_deep_research_no_sources_marks_sub_unit_degraded(monkeypatch):
    async def fake_search(params):
        return {"ok": True, "results": []}

    async def fake_complete(prompt, **kw):
        if "research director" in prompt:
            return json.dumps(["lonely"])
        if "GAPS" in prompt:
            return "[]"
        return "answer"

    monkeypatch.setattr(dr, "_complete", fake_complete)
    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_search_handler", fake_search)
    monkeypatch.setattr(
        "swarm_os.lib.mcp.web_search.web_fetch_handler", lambda p: {"ok": True}
    )
    res = asyncio_run(dr.deep_research("topic", max_sub_questions=1, max_iterations=0))
    assert res["status"] == "ok"
    assert res["sub_reports"][0]["degraded"] is True
    assert res["sub_reports"][0]["note"] == "no sources found"


def test_complete_marks_token_capped_synthesis(monkeypatch):
    """A synthesis hitting the max_tokens cap (finish_reason=length) must carry
    an explicit truncation marker — otherwise the caller presents a cut-off
    report as a complete answer."""
    import types

    class FakeChoice:
        pass

    async def fake_acompletion(**kwargs):
        choice = FakeChoice()
        choice.message = types.SimpleNamespace(content="half a report")
        choice.finish_reason = "length"
        resp = types.SimpleNamespace(choices=[choice])
        return resp

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)
    text = asyncio_run(dr._complete("prompt", max_tokens=100))
    assert "[… report truncated at token cap]" in text

    async def fake_acompletion_stop(**kwargs):
        choice = FakeChoice()
        choice.message = types.SimpleNamespace(content="full report")
        choice.finish_reason = "stop"
        resp = types.SimpleNamespace(choices=[choice])
        return resp

    monkeypatch.setattr("litellm.acompletion", fake_acompletion_stop)
    text = asyncio_run(dr._complete("prompt", max_tokens=100))
    assert "[… report truncated" not in text


def test_failed_sub_synthesis_returns_raw_sources(monkeypatch):
    """When a sub-unit's synthesis fails, the raw source text must actually be
    present in the sub-report — the degradation note claims "sources returned
    raw", so dropping the text would make that claim false."""

    async def fake_search(params):
        return {
            "ok": True,
            "results": [
                {"url": "http://raw1", "title": "Raw One", "snippet": ""},
            ],
        }

    async def fake_fetch(params):
        return {"ok": True, "text": "RAW SOURCE TEXT THAT MUST SURVIVE"}

    async def fake_complete(prompt, **kw):
        raise RuntimeError("synthesis down")

    monkeypatch.setattr(dr, "_complete", fake_complete)
    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_search_handler", fake_search)
    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_fetch_handler", fake_fetch)
    rep = asyncio_run(dr._run_sub_unit("q", max_results=1, max_tokens=100))
    assert rep["degraded"] is True
    assert rep["answer"] == ""
    assert "RAW SOURCE TEXT THAT MUST SURVIVE" in rep["raw_sources"]
