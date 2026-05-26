from __future__ import annotations

import requests
from ..core.settings import get_settings


class QdrantClient:
    def __init__(self) -> None:
        self.base_url = get_settings().qdrant_url.rstrip('/')

    def health(self) -> bool:
        url = f'{self.base_url}/healthz'
        r = requests.get(url, timeout=30)
        return r.ok
