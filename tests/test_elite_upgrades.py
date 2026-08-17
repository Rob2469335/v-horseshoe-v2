from __future__ import annotations

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from swarm_os.lib.mcp.web_search import web_search_handler
from swarm_os.lib.mcp.web_search import _QUOTA_STORE  # noqa: F401
from swarm_os.lib.mcp.registry import registry
from swarm_os.capabilities.subagent import SubagentHandler
from swarm_os.lib.mcp.mcp_client import ExternalMCPClientManager


@pytest.fixture(autouse=True)
def _isolate_search_quota(monkeypatch, tmp_path):
    """Search-quota counters are module-global and file-backed (data/search_quota.json);
    redirect + reset them so a test can never spend the real monthly budget or be
    skewed by it. All provider search tests here participate in the shared store."""
    monkeypatch.setenv("SWARM_SEARCH_QUOTA_FILE", str(tmp_path / "quota.json"))
    _QUOTA_STORE.reset_for_tests()
    yield
    _QUOTA_STORE.reset_for_tests()


@pytest.mark.anyio
async def test_web_search_handler_tavily(monkeypatch):
    # Isolate: no other provider key may leak in — the fan-out fires every
    # configured provider in parallel, so a stray key would hit real GETs.
    for k in (
        "SERPER_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {
                "title": "Tavily Title",
                "url": "https://tavily.com",
                "content": "Tavily Snippet",
            }
        ]
    }

    # Mock httpx client post
    async def mock_post(*args, **kwargs):
        return mock_response

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    res = await web_search_handler({"query": "test query", "max_results": 1})
    assert res["ok"] is True
    assert res["provider"] == "tavily"
    assert len(res["results"]) == 1
    assert res["results"][0]["title"] == "Tavily Title"
    assert res["results"][0]["provider"] == "tavily"


@pytest.mark.anyio
async def test_web_search_handler_serper(monkeypatch):
    for k in (
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "organic": [
            {
                "title": "Serper Title",
                "link": "https://serper.dev",
                "snippet": "Serper Snippet",
            }
        ]
    }

    # Mock httpx client post
    async def mock_post(*args, **kwargs):
        return mock_response

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    res = await web_search_handler({"query": "test query", "max_results": 1})
    assert res["ok"] is True
    assert res["provider"] == "serper"
    assert len(res["results"]) == 1
    assert res["results"][0]["title"] == "Serper Title"


@pytest.mark.anyio
async def test_web_search_handler_fanout_merges_and_dedups(monkeypatch):
    """The parallel fan-out queries EVERY configured provider and merges the
    results (deduped by URL), rather than returning the first that answers."""
    for k in ("BRAVE_API_KEY", "EXA_API_KEY", "SERPAPI_KEY", "TINYFISH_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")

    async def _post(self, url, *args, **kwargs):
        if "tavily.com" in str(url):
            return _payload(
                {
                    "results": [
                        {
                            "title": "T1",
                            "url": "https://shared.example",
                            "content": "first",
                        }
                    ]
                }
            )
        if "serper.dev" in str(url):
            return _payload(
                {
                    "organic": [
                        {
                            "title": "S-dupe",
                            "link": "https://shared.example",
                            "snippet": "same url",
                        },
                        {
                            "title": "S2",
                            "link": "https://s2.example",
                            "snippet": "second",
                        },
                    ]
                }
            )
        raise AssertionError(f"unexpected post URL: {url}")

    def _payload(data):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = data
        return m

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    res = await web_search_handler({"query": "q", "max_results": 2})
    assert res["ok"] is True
    assert res["provider"] == "multi:tavily,serper"
    # shared.example appears in BOTH providers but must be merged once
    urls = [r["url"] for r in res["results"]]
    assert urls.count("https://shared.example") == 1
    assert "https://s2.example" in urls
    assert len(res["results"]) == 2
    merged = {r["provider"] for r in res["results"]}
    assert "tavily" in merged and "serper" in merged


@pytest.mark.anyio
async def test_web_search_handler_fanout_two_providers(monkeypatch):
    for k in (
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")

    async def _post(self, url, *args, **kwargs):
        if "tavily.com" in str(url):
            return _ok(
                {
                    "results": [
                        {"title": "T", "url": "https://t.example", "content": "c"}
                    ]
                }
            )
        if "serper.dev" in str(url):
            return _ok(
                {
                    "organic": [
                        {"title": "S", "url": "https://s.example", "snippet": "x"}
                    ]
                }
            )
        raise AssertionError(f"unexpected post URL: {url}")

    def _ok(payload):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = payload
        return m

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    res = await web_search_handler({"query": "q", "max_results": 3})
    assert res["ok"] is True
    assert res["providers"] == ["tavily", "serper"]
    assert res["provider"] == "multi:tavily,serper"
    assert len(res["results"]) >= 1


@pytest.mark.anyio
async def test_web_search_fanout_rrf_consensus_boost(monkeypatch):
    """RRF consensus: a URL surfaced in MULTIPLE engines ranks ABOVE any URL
    surfaced in only one — the strength of the merged result is cross-engine
    agreement, not a single engine's preference."""
    for k in (
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")

    async def _post(self, url, *args, **kwargs):
        if "tavily.com" in str(url):
            return _ok(
                {
                    "results": [
                        {
                            "title": "consensus",
                            "url": "https://agreed.example/a",
                            "content": "c",
                        },
                        {
                            "title": "t-only",
                            "url": "https://singular.example/t",
                            "content": "c",
                        },
                    ]
                }
            )
        if "serper.dev" in str(url):
            return _ok(
                {
                    "organic": [
                        {
                            "title": "consensus-again",
                            "url": "https://agreed.example/a",
                            "snippet": "same doc",
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected post URL: {url}")

    def _ok(payload):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = payload
        return m

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    res = await web_search_handler({"query": "q", "max_results": 5})
    assert res["ok"] is True
    urls = [r["url"] for r in res["results"]]
    assert urls == ["https://agreed.example/a", "https://singular.example/t"]
    for r in res["results"]:
        if r["url"] == "https://agreed.example/a":
            assert r["providers"] == ["tavily", "serper"]
            assert r["provider"] == "tavily"
            assert r["rrf_score"] > 1.0 / 61  # boosted by consensus, not single-hit
        else:
            assert r["providers"] == ["tavily"]
            assert r["rrf_score"] <= 1.0 / 61


@pytest.mark.anyio
async def test_web_search_quota_exclusion(monkeypatch):
    """A provider past its monthly free-tier quota is EXCLUDED from the fan-out
    (logged, not failed) — the search still returns the surviving providers."""
    for k in (
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")
    # serper's budget is already spent this month (env override forces it)
    monkeypatch.setenv("SWARM_SEARCH_QUOTA_SERPER", "1")
    _QUOTA_STORE.reset_for_tests()
    _QUOTA_STORE.record("tavily")
    _QUOTA_STORE.record("serper")

    async def _post(self, url, *args, **kwargs):
        if "tavily.com" in str(url):
            return _ok(
                {
                    "results": [
                        {"title": "T", "url": "https://t.example", "content": "c"}
                    ]
                }
            )
        raise AssertionError(f"unexpected post URL: {url}")

    def _ok(payload):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = payload
        return m

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    res = await web_search_handler({"query": "q", "max_results": 3})
    assert res["ok"] is True
    assert res["providers"] == ["tavily"]
    # serper was excluded by quota, so Tavily is the sole provider label
    assert res["provider"] == "tavily"


@pytest.mark.anyio
async def test_qdrant_recall_handler():
    # Clear any lazily cached instances on the singleton to avoid test order pollution
    if hasattr(registry, "_qdrant"):
        delattr(registry, "_qdrant")
    if hasattr(registry, "_embedding"):
        delattr(registry, "_embedding")

    # Mock EmbeddingService and VectorStore
    mock_emb = AsyncMock()
    mock_emb.embed.return_value = [0.1] * 768

    mock_store = AsyncMock()
    mock_store.search.return_value = [
        {"id": "doc1", "score": 0.95, "payload": {"text": "Codebase snippet content"}}
    ]

    with patch(
        "swarm_os.services.embedding_service.EmbeddingService", return_value=mock_emb
    ):
        with patch(
            "swarm_os.services.vector_store.VectorStore", return_value=mock_store
        ):
            res = await registry.call("qdrant_recall", {"query": "how to load RAG"})
            assert res["ok"] is True
            assert len(res["results"]) == 1
            assert res["results"][0]["payload"]["text"] == "Codebase snippet content"


@pytest.mark.anyio
async def test_subagent_capability_handler(monkeypatch):
    # Mock httpx post response for the subagent API endpoint
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"content": "First thought. "},
        {"content": "Second action."},
    ]

    async def mock_post(*args, **kwargs):
        return mock_response

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    handler = SubagentHandler()
    res = await handler.execute(
        {"agent_id": "coder", "prompt": "write a print function", "history": []}
    )

    assert res["status"] == "success"
    assert res["agent_id"] == "coder"
    assert res["content"] == "First thought. Second action."


@pytest.mark.anyio
async def test_mcp_client_manager_nonexistent_config():
    manager = ExternalMCPClientManager(config_path="nonexistent_config.json")
    tools = await manager.start()
    assert tools == []
