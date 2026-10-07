"""Tests for the F2 host-side model gateway.

The gateway is exercised through httpx ASGI transports (no real sockets, no
services): a fake upstream stands in for the local model, and the gateway's
client-source is injected. This proves the gateway is NOT an open proxy.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from qwen_train.f2_model_gateway import create_app

GUEST = "10.10.0.2"
OTHER = "10.10.0.9"


def _upstream_app() -> FastAPI:
    up = FastAPI()
    seen: list[str] = []

    @up.post("/v1/chat/completions")
    async def chat():
        return JSONResponse({"choices": [{"message": {"content": "ok"}}]})

    @up.post("/v1/completions")
    async def comp():
        return JSONResponse({"choices": []})

    @up.get("/v1/models")
    async def models():
        return JSONResponse({"data": [{"id": "robs4b"}]})

    @up.post("/v1/stream")
    async def stream():
        return JSONResponse({"seen": seen})

    return up


def _make(**kw):
    up = _upstream_app()
    kw.setdefault("upstream_base_url", "http://127.0.0.1:8080")
    kw.setdefault("allowed_clients", (GUEST,))
    kw.setdefault("upstream_transport", httpx.ASGITransport(app=up))
    return create_app(**kw)


def _client(app, ip: str = GUEST) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 44444)),
        base_url="http://gw",
    )


async def test_valid_chat_forwards():
    app = _make()
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={"model": "robs4b", "messages": []})
    assert r.status_code == 200
    assert r.json()["choices"]


async def test_models_forwards():
    app = _make()
    async with _client(app) as c:
        r = await c.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["data"][0]["id"] == "robs4b"


async def test_completions_forwards():
    app = _make()
    async with _client(app) as c:
        r = await c.post("/v1/completions", json={"prompt": "hi"})
    assert r.status_code == 200


async def test_disallowed_source_rejected():
    app = _make()
    async with _client(app, ip=OTHER) as c:
        r = await c.post("/v1/chat/completions", json={})
    assert r.status_code == 403


async def test_arbitrary_path_rejected():
    app = _make()
    async with _client(app) as c:
        r = await c.post("/v1/embeddings", json={})
    assert r.status_code == 404


async def test_qdrant_style_path_rejected():
    app = _make()
    async with _client(app) as c:
        r = await c.post("/collections/swarm_memory/points/scroll", json={})
    assert r.status_code == 404


async def test_disallowed_method_rejected():
    app = _make()
    async with _client(app) as c:
        r = await c.put("/v1/models")
    assert r.status_code == 404


async def test_oversized_body_rejected():
    app = _make(max_body_bytes=16)
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={"pad": "x" * 200})
    assert r.status_code == 413


async def test_upstream_unavailable_fails_closed():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app = _make(upstream_transport=httpx.MockTransport(handler))
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={})
    assert r.status_code == 503


async def test_redirect_refused():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/v1/models"})

    app = _make(upstream_transport=httpx.MockTransport(handler))
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={})
    assert r.status_code == 502


async def test_destination_is_fixed_and_not_client_controlled():
    """No client-supplied host/port/URL can change the upstream destination."""
    seen: dict[str, str] = {}

    up = FastAPI()

    @up.post("/v1/chat/completions")
    async def chat(request: Request):
        seen["path"] = request.url.path
        seen["host"] = request.headers.get("host", "")
        return JSONResponse({"ok": True})

    app = create_app(
        upstream_base_url="http://127.0.0.1:8080",
        allowed_clients=(GUEST,),
        upstream_transport=httpx.ASGITransport(app=up),
    )
    async with _client(app) as c:
        # A client trying to redirect the gateway at Qdrant (via Host header) must
        # NOT change the destination.
        await c.post(
            "/v1/chat/completions",
            json={},
            headers={"Host": "127.0.0.1:6333"},
        )
    assert seen["path"] == "/v1/chat/completions"
    assert seen["host"] == "127.0.0.1:8080"


async def test_streaming_response_content_type_preserved():
    up = FastAPI()

    @up.post("/v1/chat/completions")
    async def chat():
        async def gen():
            yield b'data: {"delta":"hi"}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream")

    app = create_app(
        upstream_base_url="http://127.0.0.1:8080",
        allowed_clients=(GUEST,),
        upstream_transport=httpx.ASGITransport(app=up),
    )
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert b"delta" in r.content
