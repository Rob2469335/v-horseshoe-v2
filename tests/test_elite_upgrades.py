from __future__ import annotations

import json
import os
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from swarm_os.lib.mcp.web_search import web_search_handler
from swarm_os.lib.mcp.registry import registry
from swarm_os.capabilities.subagent import SubagentHandler
from swarm_os.lib.mcp.mcp_client import ExternalMCPClientManager


@pytest.mark.anyio
async def test_web_search_handler_tavily(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"title": "Tavily Title", "url": "https://tavily.com", "content": "Tavily Snippet"}
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


@pytest.mark.anyio
async def test_web_search_handler_serper(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "organic": [
            {"title": "Serper Title", "link": "https://serper.dev", "snippet": "Serper Snippet"}
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
async def test_qdrant_recall_handler():
    # Mock EmbeddingService and VectorStore
    mock_emb = MagicMock()
    mock_emb.embed.return_value = [0.1] * 768
    
    mock_store = MagicMock()
    mock_store.search.return_value = [
        {"id": "doc1", "score": 0.95, "payload": {"text": "Codebase snippet content"}}
    ]
    
    with patch("swarm_os.services.embedding_service.EmbeddingService", return_value=mock_emb):
        with patch("swarm_os.services.vector_store.VectorStore", return_value=mock_store):
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
    res = await handler.execute({
        "agent_id": "coder",
        "prompt": "write a print function",
        "history": []
    })
    
    assert res["status"] == "success"
    assert res["agent_id"] == "coder"
    assert res["content"] == "First thought. Second action."


@pytest.mark.anyio
async def test_mcp_client_manager_nonexistent_config():
    manager = ExternalMCPClientManager(config_path="nonexistent_config.json")
    tools = await manager.start()
    assert tools == []


@pytest.mark.anyio
async def test_web_search_handler_serpapi(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "serpapi-test-key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "organic_results": [
            {"title": "SerpApi Title", "link": "https://serpapi.com", "snippet": "SerpApi Snippet"}
        ]
    }
    
    async def mock_get(*args, **kwargs):
        return mock_response
        
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    
    res = await web_search_handler({"query": "test query", "max_results": 1})
    assert res["ok"] is True
    assert res["provider"] == "serpapi"
    assert len(res["results"]) == 1
    assert res["results"][0]["title"] == "SerpApi Title"


@pytest.mark.anyio
async def test_web_search_handler_exa(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"title": "Exa Title", "url": "https://exa.ai", "text": "Exa Text"}
        ]
    }
    
    async def mock_post(*args, **kwargs):
        return mock_response
        
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    
    res = await web_search_handler({"query": "test query", "max_results": 1})
    assert res["ok"] is True
    assert res["provider"] == "exa"
    assert len(res["results"]) == 1
    assert res["results"][0]["title"] == "Exa Title"


@pytest.mark.anyio
async def test_web_search_handler_tinyfish(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("TINYFISH_API_KEY", "tinyfish-test-key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"title": "Tinyfish Title", "url": "https://tinyfish.ai", "snippet": "Tinyfish Snippet"}
    ]
    
    async def mock_get(*args, **kwargs):
        return mock_response
        
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    
    res = await web_search_handler({"query": "test query", "max_results": 1})
    assert res["ok"] is True
    assert res["provider"] == "tinyfish"
    assert len(res["results"]) == 1
    assert res["results"][0]["title"] == "Tinyfish Title"


@pytest.mark.anyio
async def test_model_fallback_chain(monkeypatch):
    from swarm_os.core.orchestrator import Orchestrator
    from swarm_os.services.agent_service import AgentService
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    
    import swarm_os.services.agent_service as agent_service_mod
    agent_service_mod._LIVE_OPENROUTER_MODELS = None
    agent_service_mod._LIVE_NVIDIA_MODELS = None
    agent_service_mod._LAST_FETCH_TIME = 0.0

    class MockStreamResponse:
        def __init__(self, url, payload):
            self.url = url
            self.payload = payload
        async def __aenter__(self):
            model_in_payload = (self.payload or {}).get("model", "")
            if "127.0.0.1:11434" in self.url and model_in_payload in ("qwen3:14b", "qwen2.5-coder:14b"):
                return self
            raise httpx.HTTPStatusError("API Error", request=MagicMock(), response=MagicMock(status_code=402))
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def raise_for_status(self):
            pass
        async def aiter_lines(self):
            yield json.dumps({"message": {"content": "Resolved local fallback answer"}, "done": False})
            yield json.dumps({"done": True})

    class MockGetResponse:
        def __init__(self, url, headers=None):
            self.url = url
            self.status_code = 200
        def json(self):
            if "openrouter.ai" in self.url:
                return {"data": [{"id": "deepseek/deepseek-chat-v3-5:free"}]}
            elif "nvidia.com" in self.url:
                return {"data": [{"id": "meta/llama-3.3-70b-instruct"}]}
            return {"data": []}

    async def mock_get(client_self, url, *args, **kwargs):
        return MockGetResponse(url, kwargs.get("headers"))
        
    def mock_stream(client_self, method, url, *args, **kwargs):
        return MockStreamResponse(url, kwargs.get("json"))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(httpx.AsyncClient, "stream", mock_stream)

    orchestrator = Orchestrator()
    service = AgentService(orchestrator=orchestrator)
    
    chunks = []
    async for chunk in service.step_agent_stream(
        agent_id="coordinator",
        prompt="Test fallback escalation",
        history=[]
    ):
        chunks.append(chunk)

    model_selected_events = [c for c in chunks if c.get("type") == "model_selected"]
    escalation_events = [c for c in chunks if c.get("type") == "model_escalation"]
    final_events = [c for c in chunks if c.get("type") == "final"]

    assert len(model_selected_events) > 0
    assert model_selected_events[0]["model"] == "qwen3-coder:480b-cloud"
    assert len(escalation_events) > 0
    assert len(final_events) == 1
    assert final_events[0]["content"] == "Resolved local fallback answer"


