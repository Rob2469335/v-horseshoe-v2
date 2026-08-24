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
    skewed by it. All provider search tests here participate in the shared store.

    Keyless providers (openalex/gdelt) do REAL GETs — they are gated behind
    SWARM_SEARCH_KEYLESS, so this fixture turns them OFF by default (tests mock
    only the POST path); a keyless-provider test re-enables it explicitly."""
    monkeypatch.setenv("SWARM_SEARCH_QUOTA_FILE", str(tmp_path / "quota.json"))
    monkeypatch.setenv("SWARM_SEARCH_KEYLESS", "0")
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
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
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
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
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
    for k in (
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
    ):
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
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
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
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
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
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
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
async def test_web_search_keyless_providers_join_fanout(monkeypatch):
    """OpenAlex + GDELT are keyless (env-key=None): they join the fan-out with NO
    configured key, merge/dedup like any provider, and are labeled in the result.
    They fire GETs — re-enable the keyless gate this fixture turned off."""
    for k in (
        "TAVILY_API_KEY",
        "SERPER_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SWARM_SEARCH_KEYLESS", "1")

    async def _get(self, url, *args, **kwargs):
        url = str(url)
        if "openalex.org" in url:
            return _g(
                {
                    "meta": {"count": 2},
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "title": "OpenAlex Paper",
                            "doi": "10.1000/x1",
                            "publication_year": 2026,
                        }
                    ],
                }
            )
        if "gdeltproject.org" in url:
            return _g(
                {
                    "articles": [
                        {
                            "url": "https://shared.example/s",
                            "title": "GDELT",
                            "sourcecountry": "US",
                        },
                        {
                            "url": "https://news.example/n2",
                            "title": "Second",
                            "language": "en",
                        },
                    ]
                }
            )
        raise AssertionError(f"unexpected get URL: {url}")

    def _g(payload):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = payload
        return m

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)

    res = await web_search_handler({"query": "q", "max_results": 3})
    assert res["ok"] is True
    assert res["providers"] == ["openalex", "gdelt"]
    assert res["provider"] == "multi:openalex,gdelt"
    urls = [r["url"] for r in res["results"]]
    assert "https://doi.org/10.1000/x1" in urls
    assert "https://news.example/n2" in urls
    merged = {r["provider"] for r in res["results"]}
    assert {"openalex", "gdelt"} <= merged


@pytest.mark.anyio
async def test_web_search_keyless_gate_off_is_hermetic(monkeypatch):
    """SWARM_SEARCH_KEYLESS=0 (the fixture default) excludes keyless providers
    entirely — with no keyed provider configured, the handler falls to DDG and
    never fires an OpenAlex/GDELT GET."""
    for k in (
        "TAVILY_API_KEY",
        "SERPER_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)

    called_get = []

    async def _get(self, url, *args, **kwargs):
        called_get.append(str(url))
        return _g({"meta": {"count": 0}, "results": []})

    def _g(payload):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = payload
        return m

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)

    await web_search_handler({"query": "q", "max_results": 2})
    assert called_get == []  # no keyless GET fired (OpenAlex/GDELT hermetic)


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


def _mock_resp(payload):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = payload
    return m


@pytest.mark.anyio
async def test_web_search_evidence_layer_near_dup_collapse_and_agreement(monkeypatch):
    """DIVERSITY + EVIDENCE LAYER: same story at different URLs (syndicated copy)
    collapses to one canonical result tagged with its echo count; exact-URL
    corroboration across providers is attributed as consensus; unique-to-provider
    findings are counted per provider. The existing result-shape contract
    (title/url/snippet/provider/providers) is preserved."""
    for k in (
        "TAVILY_API_KEY",
        "SERPER_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SWARM_SEARCH_KEYLESS", "1")

    async def _get(self, url, *args, **kwargs):
        url = str(url)
        if "openalex.org" in url:
            return _mock_resp(
                {
                    "meta": {"count": 2},
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "title": "Rates fall on new policy",
                            "doi": None,
                            "publication_year": 2026,
                        },
                        {
                            "id": "https://openalex.org/W2",
                            "title": "Only openalex story",
                            "doi": "10.1000/x1",
                            "publication_year": 2025,
                        },
                    ],
                }
            )
        if "gdeltproject.org" in url:
            return _mock_resp(
                {
                    "articles": [
                        {
                            "url": "https://a.example/story",
                            "title": "Rates fall on new policy",
                            "sourcecountry": "US",
                        },
                        {
                            "url": "https://b.example/story",
                            "title": "Rates fall on new policy (update)",
                            "sourcecountry": "US",
                        },
                        {
                            "url": "https://c.example/unique",
                            "title": "Only gdelt story",
                            "language": "en",
                        },
                    ]
                }
            )
        raise AssertionError(f"unexpected get URL: {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)

    res = await web_search_handler({"query": "q", "max_results": 5})
    assert res["ok"] is True
    # Backward-compatible contract: label + provider fields still present.
    assert res["provider"] == "multi:openalex,gdelt"
    assert res["providers"] == ["openalex", "gdelt"]

    # Four engine hits collapse to three unique sources: two near-duplicate wire
    # stories (openalex W1 title matches the gdelt syndicate) fold into ONE
    # canonical result (first-seen = openalex W1), while the openalex-only W2
    # (its doi mapped to doi.org) and the gdelt-only story survive.
    assert len(res["results"]) == 3
    rrf_keys = {r["url"] for r in res["results"]}
    assert rrf_keys == {
        "https://openalex.org/W1",
        "https://doi.org/10.1000/x1",
        "https://c.example/unique",
    }
    # The syndicated "Rates fall on new policy" folded into ONE canonical result
    # carrying the near-dup echoes (a.example + b.example variants).
    fall = next(r for r in res["results"] if r["url"] == "https://openalex.org/W1")
    assert fall.get("echoes", 0) == 2
    assert "https://a.example/story" in fall.get("variants", [])
    assert "https://b.example/story" in fall.get("variants", [])
    assert set(fall.get("providers") or []) == {"openalex", "gdelt"}

    ev = res["evidence"]
    assert ev["raw_results"] == 5
    assert ev["unique_results"] == 3
    assert ev["deduped"] == 2
    assert ev["clusters"] >= 1
    # Exact-URL corroboration: none here (openalex W2 and the gdelt hits are all
    # distinct URLs) — so consensus is 0 and everything is unique-to-provider.
    assert ev["consensus"] == 0
    assert "openalex" in ev["unique_to_provider"]
    assert "gdelt" in ev["unique_to_provider"]
    assert "EVIDENCE POOL" in res["evidence_text"]


@pytest.mark.anyio
async def test_web_search_evidence_layer_conflicting_claims_flagged(monkeypatch):
    """DIVERSITY + EVIDENCE LAYER: opposing claims on the same topic (shared
    content words + opposite direction lexis) are surfaced as CONFLICTING CLAIMS
    in the evidence context the LLM receives."""
    for k in (
        "TAVILY_API_KEY",
        "SERPER_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "SERPAPI_KEY",
        "TINYFISH_API_KEY",
        "SCAVIO_API_KEY",
        "FIRECRAWL_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SWARM_SEARCH_KEYLESS", "1")

    async def _get(self, url, *args, **kwargs):
        url = str(url)
        if "openalex.org" in url:
            return _mock_resp(
                {
                    "meta": {"count": 1},
                    "results": [
                        {
                            "id": "https://openalex.org/C1",
                            "title": "Study: red wine raises blood pressure",
                            "doi": None,
                            "publication_year": 2026,
                        }
                    ],
                }
            )
        if "gdeltproject.org" in url:
            return _mock_resp(
                {
                    "articles": [
                        {
                            "url": "https://d.example/wine",
                            "title": "Red wine lowers blood pressure, finds study",
                            "sourcecountry": "US",
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected get URL: {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)

    res = await web_search_handler(
        {"query": "red wine blood pressure", "max_results": 3}
    )
    assert res["ok"] is True
    ev = res["evidence"]
    assert ev["conflicts"] == 1
    assert "CONFLICTING CLAIMS" in res["evidence_text"]
    # The opposing signal must be lexically real: the neg-direction word "raises"
    # in one source vs the pos-direction "lowers" in the other.
    extra = [r["title"] for r in res["results"]]
    assert any("raises" in t for t in extra)
    assert any("lowers" in t for t in extra)
