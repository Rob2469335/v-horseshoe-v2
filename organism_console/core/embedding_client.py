from __future__ import annotations

import requests


class EmbeddingClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen3-embedding:8b"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def embed(self, text: str) -> list[float]:
        response = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": text},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings") or []
        if not embeddings:
            raise RuntimeError("No embeddings returned from Ollama")
        vector = embeddings[0]
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("Invalid embedding vector returned from Ollama")
        return [float(x) for x in vector]
