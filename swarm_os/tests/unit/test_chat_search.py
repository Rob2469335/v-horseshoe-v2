import pytest
from swarm_os.capabilities.chat_search import ChatSearchHandler
from swarm_os.capabilities.models import ChatSearchRequest

pytestmark = pytest.mark.anyio

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

async def test_chat_search_finds_matches():
    handler = ChatSearchHandler()
    req = ChatSearchRequest(query="pytest coding engine", max_results=2)
    response = await handler.execute(req)
    assert response.status == "success"
    assert len(response.results) > 0
    assert "organism_0" in response.results[0].sender
    assert response.results[0].score > 0.0

async def test_chat_search_empty_query_returns_nothing():
    handler = ChatSearchHandler()
    req = ChatSearchRequest(query="", max_results=5)
    response = await handler.execute(req)
    assert response.status == "success"
    assert len(response.results) == 0
