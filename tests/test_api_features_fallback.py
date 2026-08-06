from fastapi.testclient import TestClient

import swarm_os.api.api_features as api_features


def test_local_file_fallback(monkeypatch):
    # Force the dense vector search to return no candidates so the lexical
    # fallback (including local-file scan) is exercised.
    async def fake_search(collection, query, top_k=5):
        return []

    # The api_features module imports search from swarm_os.lib.vector.qdrant_store
    # inside the handler, so patch that symbol instead.
    import swarm_os.lib.vector.qdrant_store as qstore
    monkeypatch.setattr(qstore, "search", fake_search)
    # Also patch the local_docs_search helper to return a deterministic result
    import swarm_os.api._fallbacks as fb
    def fake_local(repo_root, tokens, top_k=5):
        return [{"id": "local-doc", "score": 1.0, "payload": {"path": "AGENTS.md", "excerpt": "doc"}}]
    monkeypatch.setattr(fb, "local_docs_search", fake_local)

    class FakeQdrantClient:
        closed = False

        def __init__(self, **_kwargs):
            pass

        async def scroll(self, **_kwargs):
            return ([], None)

        async def close(self):
            type(self).closed = True

    import qdrant_client
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", FakeQdrantClient)

    # Create a minimal FastAPI app mounting the router.
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api_features.router)

    client = TestClient(app)
    res = client.post("/features/search", json={"query": "swarm", "collection": "chat_archive", "top_k": 3})
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "status": "degraded",
        "fallback": True,
        "results": [
            {
                "id": "local-doc",
                "score": 1.0,
                "payload": {"path": "AGENTS.md", "excerpt": "doc"},
            }
        ],
    }
    assert FakeQdrantClient.closed is True
