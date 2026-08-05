from __future__ import annotations

import requests


class EmbeddingClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8081", model: str = "nomic-embed-text-v1.5"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def embed(self, text: str) -> list[float]:
        response = requests.post(
            f"{self.base_url}/v1/embeddings",
            json={"input": text},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("data") or []
        if not embeddings:
            raise RuntimeError("No embeddings returned from Llama.cpp")
        vector = embeddings[0].get("embedding", [])
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("Invalid embedding vector returned from Llama.cpp")
        return [float(x) for x in vector]
