import pytest
from pathlib import Path
from swarm_os.capabilities.chat_search import ChatSearchHandler
from swarm_os.capabilities.models import ChatSearchRequest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


async def test_chat_search_finds_matches(tmp_path):
    # Self-contained unit test: seed the EventStore with known records so the
    # matching logic is exercised deterministically (no reliance on runtime-produced
    # data/events, which is absent in CI and differs across environments).
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "events.jsonl").write_text(
        "\n".join(
            [
                '{"payload": "the swarm uses a pytest coding engine for review", "sender": "organism_0", "timestamp": "t1"}',
                '{"payload": "rust llm memory notes", "sender": "worker_1", "timestamp": "t2"}',
            ]
        ),
        encoding="utf-8",
    )

    handler = ChatSearchHandler(events_root=events_dir)
    req = ChatSearchRequest(query="pytest coding engine", max_results=2)
    response = await handler.execute(req)
    assert response.status == "success"
    assert len(response.results) > 0
    assert "organism_0" in response.results[0].sender
    assert response.results[0].score > 0.0


async def test_chat_search_returns_nothing_when_no_match(tmp_path):
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "events.jsonl").write_text(
        '{"payload": "rust llm memory notes", "sender": "worker_1", "timestamp": "t1"}\n',
        encoding="utf-8",
    )

    handler = ChatSearchHandler(events_root=events_dir)
    req = ChatSearchRequest(query="python dataframes", max_results=5)
    response = await handler.execute(req)
    assert response.status == "success"
    assert len(response.results) == 0


async def test_chat_search_empty_query_returns_nothing():
    handler = ChatSearchHandler()
    req = ChatSearchRequest(query="", max_results=5)
    response = await handler.execute(req)
    assert response.status == "success"
    assert len(response.results) == 0
