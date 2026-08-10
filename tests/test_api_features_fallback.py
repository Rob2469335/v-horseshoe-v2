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


def test_keyword_fallback_follows_next_page_offset(monkeypatch):
    """#8 — _keyword_fallback scrolled only the first page (limit=200) and never
    followed next_page_offset, so a matching payload on a later page was silently
    missed and degraded search returned empty. This test pins that the scroll loop
    walks every page: page 1 holds only non-matching points plus a next_page_offset,
    page 2 holds the match. Pre-fix the match is never read and the call degrades
    to an empty result."""
    from types import SimpleNamespace

    def make_point(pid, text):
        return SimpleNamespace(id=pid, payload={"text": text})

    calls = []

    class FakeQdrantClient:
        closed = False

        def __init__(self, **_kwargs):
            pass

        async def scroll(self, **_kwargs):
            calls.append(_kwargs.get("offset"))
            if len(calls) == 1:
                # Page 1: 200 points that do NOT match the query, plus a
                # continuation offset so the loop must read page 2.
                page1 = [make_point(f"miss-{i}", "irrelevant content here") for i in range(200)]
                return (page1, "page-2-cursor")
            # Page 2: the only matching point. Terminates the pagination.
            return ([make_point("hit-on-page2", "zzqxvw target payload")], None)

        async def close(self):
            type(self).closed = True

    import qdrant_client
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", FakeQdrantClient)
    # Never let the local-doc scan mask a missed page: it must stay empty so the
    # only way the match surfaces is via the pagination loop.
    import swarm_os.api._fallbacks as fb
    monkeypatch.setattr(fb, "local_docs_search", lambda repo_root, tokens, top_k=5: [])

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api_features.router)

    client = TestClient(app)
    res = client.post(
        "/features/search",
        json={"query": "zzqxvw", "collection": "chat_archive", "top_k": 1},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "degraded"
    assert body["fallback"] is True
    ids = [r["id"] for r in body["results"]]
    assert "hit-on-page2" in ids
    # The loop must have issued a second scroll carrying the page-2 cursor.
    assert calls == [None, "page-2-cursor"]
    assert FakeQdrantClient.closed is True
