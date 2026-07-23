import requests
import logging
import urllib3
from typing import Any
from typing import Any, Optional
from organism_console.config import BACKEND_URL
from swarm_os.config.settings import settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("zenith_cli")

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
        if method == "GET":
            return requests.get(url, timeout=(timeout, read_timeout), verify=settings.ssl_verify)

        if stream:
            return requests.post(url, json=payload, timeout=(timeout, read_timeout), stream=True, verify=settings.ssl_verify)
        return requests.post(url, json=payload, timeout=(timeout, read_timeout), verify=settings.ssl_verify)
    except requests.exceptions.RequestException as e:
        log.debug(f"API call failed: {e}")
        return None

import httpx

async def call_api_async_stream(
    endpoint: str,
    method: str = "POST",
    payload: Any = None,
    timeout: float = 15.0,
    read_timeout: float = 600.0,
):
    url = f"{BACKEND_URL}{endpoint}"
    timeout_config = httpx.Timeout(timeout, read=read_timeout)
    client = httpx.AsyncClient(timeout=timeout_config, verify=settings.ssl_verify)
    try:
        request = client.build_request(method, url, json=payload)
        response = await client.send(request, stream=True)
        return client, response
    except httpx.RequestError as e:
        log.debug(f"Async API stream failed: {e}")
        await client.aclose()
        return None, None

