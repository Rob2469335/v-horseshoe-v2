from __future__ import annotations

import requests
from ..core.settings import get_settings


class QdrantClient:
    def __init__(self) -> None:
        self.base_url = get_settings().qdrant_url.rstrip('/')

    async def health(self) -> bool:
        url = f'{self.base_url}/healthz'
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(url)
            return r.status_code == 200
        except Exception:
            return False

