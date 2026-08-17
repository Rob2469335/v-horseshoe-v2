import requests
import logging
import urllib3
from typing import Any
from typing import Optional
from organism_console.config import BACKEND_URL
from swarm_os.config.settings import settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("zenith_cli")


def _auth_headers() -> dict:
    """Attach the loopback API token if SWARM_API_TOKEN is set (mirrors the
    backend's opt-in token guard in swarm_os/app/main.py)."""
    import os

    token = os.getenv("SWARM_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def call_api(
    endpoint: str,
    method: str = "GET",
    payload: Any = None,
    stream: bool = False,
    timeout: float = 15.0,
    read_timeout: float = 600.0,
) -> Optional[requests.Response]:
    """Robust API communication layer for ZENITH OS."""
    try:
        url = f"{BACKEND_URL}{endpoint}"
        headers = _auth_headers()
        if method == "GET":
            return requests.get(
                url,
                timeout=(timeout, read_timeout),
                verify=settings.ssl_verify,
                headers=headers,
            )

        if stream:
            return requests.post(
                url,
                json=payload,
                timeout=(timeout, None),
                stream=True,
                verify=settings.ssl_verify,
                headers=headers,
            )
        return requests.post(
            url,
            json=payload,
            timeout=(timeout, read_timeout),
            verify=settings.ssl_verify,
            headers=headers,
        )
    except requests.exceptions.RequestException as e:
        log.debug(f"API call failed: {e}")
        return None


import httpx

_async_client: httpx.AsyncClient | None = None


def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=15.0),
            verify=settings.ssl_verify,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _async_client


async def call_api_async_stream(
    endpoint: str,
    method: str = "POST",
    payload: Any = None,
):
    url = f"{BACKEND_URL}{endpoint}"
    client = _get_async_client()
    request = client.build_request(method, url, json=payload, headers=_auth_headers())
    try:
        response = await client.send(request, stream=True)
        return response
    except httpx.RequestError as e:
        log.debug(f"Async API stream failed: {e}")
        return None
