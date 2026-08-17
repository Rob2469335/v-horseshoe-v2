from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import swarm_os.api.api_features as api_features


def _app(monkeypatch, search_results, llm_text=None, llm_exc=None):
    """Minimal app with the features router, web-search + fetch mocked, and the
    LLM seam patched at `litellm.acompletion` so the real `_llm_complete` /
    `_llm_verdict` fail-closed guards run under test."""

    async def fake_search(params, trace_hook=None):
        return {
            "ok": True,
            "provider": "multi:tavily,serper",
            "providers": ["tavily", "serper"],
            "query": params["query"],
            "results": search_results,
        }

    async def fake_fetch(params, trace_hook=None):
        return {"ok": True, "url": params["url"], "text": "fetched body"}

    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_search_handler", fake_search)
    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_fetch_handler", fake_fetch)

    import litellm

    if llm_exc is not None:

        async def raise_exc(*args, **kwargs):
            raise llm_exc

        monkeypatch.setattr(litellm, "acompletion", raise_exc)
    elif llm_text is not None:

        async def fake_acompletion(*args, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=llm_text))]
            )

        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    app = FastAPI()
    app.include_router(api_features.router)
    return TestClient(app)


def test_web_research_verdict_sufficient(monkeypatch):
    client = _app(
        monkeypatch,
        search_results=[
            {
                "title": "S1",
                "url": "https://a.example/1",
                "snippet": "s1",
                "provider": "tavily",
            },
            {
                "title": "S2",
                "url": "https://a.example/2",
                "snippet": "s2",
                "provider": "serper",
            },
        ],
        llm_text=(
            '{"answer": "Horseshoe crabs swarm. [1] [2]", '
            '"sufficiency": "sufficient", '
            '"sufficiency_note": "Two independent sources cover the question.", '
            '"conflicts": []}'
        ),
    )
    res = client.post(
        "/features/web-research", json={"query": "swarm", "verdict": True}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "Horseshoe crabs swarm" in body["answer"]
    assert body["verdict"]["sufficiency"] == "sufficient"
    assert body["verdict"]["conflicts"] == []
    assert len(body["citations"]) == 2


def test_web_research_verdict_conflict_surfaced(monkeypatch):
    client = _app(
        monkeypatch,
        search_results=[
            {
                "title": "S1",
                "url": "https://a.example/1",
                "snippet": "s1",
                "provider": "tavily",
            },
            {
                "title": "S2",
                "url": "https://a.example/2",
                "snippet": "s2",
                "provider": "serper",
            },
        ],
        llm_text=(
            '{"answer": "Sources disagree on the cause. [1] [2]", '
            '"sufficiency": "insufficient", '
            '"sufficiency_note": "The two sources directly contradict each other.", '
            '"conflicts": [{"claim_a": "cause is A [1]", '
            '"claim_b": "cause is B [2]", "sources": [1, 2]}]}'
        ),
    )
    res = client.post(
        "/features/web-research", json={"query": "swarm", "verdict": True}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["verdict"]["sufficiency"] == "insufficient"
    assert len(body["verdict"]["conflicts"]) == 1
    assert body["verdict"]["conflicts"][0]["sources"] == [1, 2]


def test_web_research_verdict_unparseable_falls_back(monkeypatch):
    """A model response that isn't a parseable verdict must NOT fabricate one —
    it degrades to the plain synthesis path without a verdict key."""
    client = _app(
        monkeypatch,
        search_results=[
            {
                "title": "S1",
                "url": "https://a.example/1",
                "snippet": "s1",
                "provider": "tavily",
            }
        ],
        llm_text="I found the answer: horseshoe crabs swarm. [1]",
    )
    res = client.post(
        "/features/web-research", json={"query": "swarm", "verdict": True}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "horseshoe crabs swarm" in body["answer"]
    assert "verdict" not in body


def test_web_research_llm_outage_never_fabricates(monkeypatch):
    client = _app(
        monkeypatch,
        search_results=[
            {
                "title": "S1",
                "url": "https://a.example/1",
                "snippet": "s1",
                "provider": "tavily",
            }
        ],
        llm_exc=RuntimeError("provider down"),
    )
    res = client.post(
        "/features/web-research", json={"query": "swarm", "verdict": True}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["answer"] == ""
    assert "verdict" not in body
    # citations still reflect the real retrieved sources (never fabricated)
    assert len(body["citations"]) == 1


def test_web_research_verdict_disabled(monkeypatch):
    """verdict=false keeps the legacy plain-answer shape (no verdict key)."""
    client = _app(
        monkeypatch,
        search_results=[
            {
                "title": "S1",
                "url": "https://a.example/1",
                "snippet": "s1",
                "provider": "tavily",
            }
        ],
        llm_text="plain cited answer [1]",
    )
    res = client.post(
        "/features/web-research", json={"query": "swarm", "verdict": False}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["answer"] == "plain cited answer [1]"
    assert "verdict" not in body
