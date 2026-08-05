from __future__ import annotations

import httpx
from ..core.settings import get_settings

_qdrant_health_client: httpx.AsyncClient | None = None


def _get_qdrant_health_client() -> httpx.AsyncClient:
    global _qdrant_health_client
    if _qdrant_health_client is None:
        _qdrant_health_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=10),
        )
    return _qdrant_health_client


class QdrantClient:
    def __init__(self) -> None:
        self.base_url = get_settings().qdrant_url.rstrip('/')

    async def health(self) -> bool:
        url = f'{self.base_url}/healthz'
        try:
            client = _get_qdrant_health_client()
            r = await client.get(url)
            return r.status_code == 200
        except Exception:
            return False

