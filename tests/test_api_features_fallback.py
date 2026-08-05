from fastapi import FastAPI
from fastapi.testclient import TestClient

import swarm_os.api.api_features as api_features


def _build_client():
    app = FastAPI()
    app.include_router(api_features.router)
    return TestClient(app)


def test_search_returns_ok_when_vector_search_has_candidates(monkeypatch):
    """When the dense vector search returns candidates, the endpoint should
    return them with status 'ok' (no fallback)."""
    async def fake_search(collection, query, top_k=5):
        return [{"id": 1, "score": 0.9, "payload": {"text": "hello world"}}]

    import swarm_os.lib.vector.qdrant_store as qstore
    monkeypatch.setattr(qstore, "search", fake_search)

    client = _build_client()
    res = client.post("/features/search", json={"query": "hello", "collection": "chat_archive", "top_k": 3})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["fallback"] is False
    assert body["results"][0]["id"] == 1


def test_local_file_fallback_returns_monkeypatched_results(monkeypatch):
    """Force dense search to return nothing and Qdrant scroll to yield no
    payloads, then assert the local-docs fallback result is actually returned."""
    async def fake_search(collection, query, top_k=5):
        return []

    async def fake_scroll(collection_name=None, limit=None, with_payload=None):
        # no matching payloads -> triggers the local-file fallback
        return [], None

    import swarm_os.lib.vector.qdrant_store as qstore
    monkeypatch.setattr(qstore, "search", fake_search)

    import qdrant_client
    monkeypatch.setattr(
        qdrant_client.AsyncQdrantClient,
        "scroll",
        fake_scroll,
    )

    import swarm_os.api._fallbacks as fb
    fake_local = [
        {"id": "local-doc", "score": 1.0,
         "payload": {"path": "AGENTS.md", "excerpt": "swarm doc"}}
    ]
    monkeypatch.setattr(fb, "local_docs_search", lambda repo_root, tokens, top_k=5: fake_local)

    client = _build_client()
    res = client.post("/features/search", json={"query": "swarm", "collection": "chat_archive", "top_k": 3})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "degraded"
    assert body["fallback"] is True
    assert isinstance(body["results"], list)
    assert body["results"] == fake_local
