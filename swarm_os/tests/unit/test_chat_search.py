
import pytest

from swarm_os.capabilities.chat_search import ChatSearchHandler
from swarm_os.capabilities.models import ChatSearchRequest
from swarm_os.events.envelope import EventEnvelope
from swarm_os.events.store import EventStore

pytestmark = pytest.mark.anyio

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def _write_event(store: EventStore, sender: str, message: str):
    envelope = EventEnvelope.create(
        event_type="agent_action",
        source=sender,
        payload={"content": message},
    )
    store.append(envelope)


@pytest.fixture
def seeded_handler(tmp_path):
    """Hermetic ChatSearchHandler backed by a temp EventStore with known content —
    NOT the live data/events dir (which made the old test data-dependent)."""
    events_root = tmp_path / "events"
    events_root.mkdir(parents=True, exist_ok=True)
    store = EventStore(events_root)
    _write_event(store, "code_analyzer", "pytest coding engine audit completed")
    _write_event(store, "reviewer", "coding review found engine bug")

    handler = ChatSearchHandler()
    handler.store = store
    return handler


async def test_chat_search_finds_matches(seeded_handler):
    req = ChatSearchRequest(query="pytest coding engine", max_results=2)
    response = await seeded_handler.execute(req)
    assert response.status == "success"
    assert len(response.results) > 0
    assert response.results[0].sender
    assert response.results[0].score > 0.0


async def test_chat_search_empty_query_returns_nothing(seeded_handler):
    req = ChatSearchRequest(query="", max_results=5)
    response = await seeded_handler.execute(req)
    assert response.status == "success"
    assert len(response.results) == 0


async def test_chat_search_no_match_returns_empty(seeded_handler):
    req = ChatSearchRequest(query="nonexistentwordxyz", max_results=5)
    response = await seeded_handler.execute(req)
    assert response.status == "success"
    assert len(response.results) == 0
