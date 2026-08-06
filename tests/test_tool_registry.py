from unittest.mock import AsyncMock, patch
import pytest
from swarm_os.services.tool_registry import SemanticToolRegistry, TOOL_SCHEMAS
import types

class MockScoredPoint:
    def __init__(self, score, payload, id_val="123"):
        self.score = score
        self.payload = payload
        self.id = id_val

class MockRecord:
    def __init__(self, id_val, payload):
        self.id = id_val
        self.payload = payload

@pytest.fixture
def mock_registry():
    with patch("swarm_os.services.tool_registry.AsyncQdrantClient") as mock_qdrant, \
         patch("swarm_os.services.tool_registry.EmbeddingService") as mock_embedder:
         
        mock_client = AsyncMock()
        mock_qdrant.return_value = mock_client
        
        mock_emb_inst = AsyncMock()
        mock_emb_inst.embed.return_value = [0.1] * 768
        mock_embedder.return_value = mock_emb_inst
        
        registry = SemanticToolRegistry(qdrant_url="http://mock:1234")
        return registry, mock_client

@pytest.mark.asyncio
async def test_discover_tools(mock_registry):
    registry, mock_client = mock_registry
    
    # qdrant-client >=1.18: registry uses query_points (returns {points:[...]}), not search()
    mock_client.query_points.return_value = types.SimpleNamespace(points=[
        MockScoredPoint(score=0.9, payload={"tool_name": "web_search", "schema": TOOL_SCHEMAS["web_search"], "pheromone_level": 1.5}),
        MockScoredPoint(score=0.8, payload={"tool_name": "filesystem", "schema": TOOL_SCHEMAS["filesystem"], "pheromone_level": 1.0}),
        MockScoredPoint(score=0.95, payload={"tool_name": "playwright", "schema": TOOL_SCHEMAS["playwright"], "pheromone_level": 0.5}), # high semantic score but low pheromone
    ])
    
    tools = await registry.discover_tools("search for something", top_k=2)
    
    assert "web_search" in tools
    assert "filesystem" in tools
    assert "playwright" not in tools

@pytest.mark.asyncio
async def test_update_tool_pheromone_success(mock_registry):
    registry, mock_client = mock_registry
    
    mock_client.retrieve.return_value = [MockRecord("uuid1", {"pheromone_level": 1.0})]
    
    await registry.update_tool_pheromone("web_search", success=True, alpha=0.15)
    
    mock_client.set_payload.assert_called_once()
    call_args = mock_client.set_payload.call_args[1]
    assert call_args["payload"]["pheromone_level"] == 1.15

@pytest.mark.asyncio
async def test_update_tool_pheromone_failure(mock_registry):
    registry, mock_client = mock_registry
    
    mock_client.retrieve.return_value = [MockRecord("uuid1", {"pheromone_level": 1.0})]
    
    await registry.update_tool_pheromone("web_search", success=False, decay=0.05)
    
    mock_client.set_payload.assert_called_once()
    call_args = mock_client.set_payload.call_args[1]
    assert call_args["payload"]["pheromone_level"] == 0.95
    
@pytest.mark.asyncio
async def test_update_tool_pheromone_bounds(mock_registry):
    registry, mock_client = mock_registry
    
    # Test upper bound
    mock_client.retrieve.return_value = [MockRecord("uuid1", {"pheromone_level": 1.95})]
    await registry.update_tool_pheromone("web_search", success=True, alpha=0.15)
    call_args = mock_client.set_payload.call_args_list[0][1]
    assert call_args["payload"]["pheromone_level"] == 2.0  # max 2.0
    
    # Test lower bound
    mock_client.retrieve.return_value = [MockRecord("uuid1", {"pheromone_level": 0.12})]
    await registry.update_tool_pheromone("web_search", success=False, decay=0.05)
    call_args = mock_client.set_payload.call_args_list[1][1]
    assert call_args["payload"]["pheromone_level"] == 0.1  # min 0.1
