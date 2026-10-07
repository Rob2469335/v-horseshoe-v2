"""Experiment-J F2 host-side model gateway.

The F2 guest may cross the isolation boundary for EXACTLY ONE capability: the
local OpenAI-compatible model API. This gateway is that boundary. It is NOT a
proxy in the general sense:

* it forwards ONLY a fixed allow-list of OpenAI-compatible routes to a FIXED
  upstream destination (the local model router, default ``127.0.0.1:8080``);
* the client cannot supply a host, port, scheme, or absolute URL -- there is no
  CONNECT/proxy semantics and no arbitrary-URL surface;
* it does not follow redirects (a 3xx upstream is refused);
* it accepts connections only from an allow-listed client source (the guest);
* it is meant to bind ONLY to the dedicated Hyper-V Internal-switch host
  address, never ``0.0.0.0`` / the Wi-Fi / LAN / Default-Switch interface;
* it fails closed (503) when the upstream model is unavailable.

Qdrant, embedding, filesystem, control and evidence services are simply not
reachable through it: only the three allow-listed paths exist and they are
forwarded to the single fixed model destination.

Scope: F2 engineering isolation only. No F0 science is touched.
"""

from __future__ import annotations

import os
from email.utils import formatdate
from typing import Mapping, Sequence

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

#: The exact OpenAI-compatible surface the F2 production model client uses.
ALLOWED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/completions"),
        ("GET", "/v1/models"),
    }
)

#: HTTP methods the catch-all will consider before rejecting by route.
_CONSIDERED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

DEFAULT_UPSTREAM = "http://127.0.0.1:8080"
DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024  # 8 MiB
# Must exceed the model client streaming ceiling (900 s) so a long stream is not
# cut off by the gateway; see qwen_train/f2_isolation_contract.py.
DEFAULT_TIMEOUT_S = 960.0

#: Bind hosts the gateway must REFUSE (would expose it beyond the F2 boundary).
_UNSAFE_BIND_HOSTS = frozenset({"0.0.0.0", "::", "", "*"})


def http_date(now: "float | None" = None) -> str:
    """RFC 7231 IMF-fixdate (UTC) for the HTTP ``Date`` header.

    Explicit gateway behavior (not a framework side effect): the guest uses this
    as a FRESH host-UTC reference for its clock check, reusing the already
    authorized 8099 path. UTC; whole-second precision.
    """
    return formatdate(timeval=now, localtime=False, usegmt=True)


def _dated(response):
    response.headers["Date"] = http_date()
    return response


def _json(status_code: int, detail: str):
    return _dated(JSONResponse(status_code=status_code, content={"detail": detail}))


def create_app(
    *,
    upstream_base_url: str = DEFAULT_UPSTREAM,
    allowed_clients: Sequence[str] = ("127.0.0.1",),
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    request_timeout: float = DEFAULT_TIMEOUT_S,
) -> FastAPI:
    """Build the F2 model gateway ASGI app.

    ``upstream_transport`` is an injection seam for deterministic tests; in
    production it is None (a real HTTP transport). ``allowed_clients`` is the set
    of permitted peer IPs (the F2 guest address).
    """
    app = FastAPI(
        title="F2 model gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    allowed = frozenset(allowed_clients)

    @app.api_route("/{full_path:path}", methods=_CONSIDERED_METHODS, response_model=None)
    async def _forward(full_path: str, request: Request):
        client_host = request.client.host if request.client else None
        if client_host not in allowed:
            return _json(403, "F2 gateway: client source not allowed")

        path = "/" + full_path
        if (request.method, path) not in ALLOWED_ROUTES:
            return _json(404, "F2 gateway: path or method not allowed")

        body = await request.body()
        if len(body) > max_body_bytes:
            return _json(413, "F2 gateway: request body too large")

        url = f"{upstream_base_url.rstrip('/')}{path}"
        headers = {
            "content-type": request.headers.get("content-type", "application/json"),
            "accept": request.headers.get("accept", "*/*"),
        }
        client = httpx.AsyncClient(
            transport=upstream_transport,
            follow_redirects=False,
            timeout=request_timeout,
        )
        upstream_request = client.build_request(
            request.method, url, content=body, headers=headers
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            return _json(
                503, f"F2 gateway: upstream model unavailable ({type(exc).__name__})"
            )

        if upstream.status_code in (301, 302, 303, 307, 308):
            await upstream.aclose()
            await client.aclose()
            return _json(502, "F2 gateway: upstream redirect refused")

        media_type = upstream.headers.get("content-type", "application/json")

        async def _stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            media_type=media_type,
            headers={"Date": http_date()},
        )

    return app


def resolve_config(env: Mapping[str, str] | None = None) -> dict:
    """Resolve gateway configuration from the environment (no secrets)."""
    src = os.environ if env is None else env
    return {
        "bind_host": src.get("F2_GATEWAY_BIND_HOST", "").strip(),
        "bind_port": int(src.get("F2_GATEWAY_BIND_PORT", "8099")),
        "allowed_client": src.get("F2_GATEWAY_ALLOWED_CLIENT", "").strip(),
        "upstream_base_url": src.get(
            "F2_MODEL_UPSTREAM", DEFAULT_UPSTREAM
        ).strip(),
    }


def main() -> None:
    """Run the gateway. Refuses to start without an explicit safe bind/config."""
    import uvicorn

    cfg = resolve_config()
    if cfg["bind_host"] in _UNSAFE_BIND_HOSTS:
        raise SystemExit(
            "F2 gateway refusing to start: F2_GATEWAY_BIND_HOST must be the "
            "dedicated Internal-switch host address, not 0.0.0.0/::/empty."
        )
    if not cfg["allowed_client"]:
        raise SystemExit(
            "F2 gateway refusing to start: F2_GATEWAY_ALLOWED_CLIENT (the F2 "
            "guest address) must be set."
        )
    app = create_app(
        upstream_base_url=cfg["upstream_base_url"],
        allowed_clients=(cfg["allowed_client"],),
    )
    uvicorn.run(app, host=cfg["bind_host"], port=cfg["bind_port"], log_level="info")


if __name__ == "__main__":
    main()
