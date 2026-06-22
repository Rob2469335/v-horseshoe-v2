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
