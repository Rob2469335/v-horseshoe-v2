# swarm_os/infra/ollama.py
# Upgraded: httpx instead of requests (matches rest of stack),
# health check method, proper timeout handling, logging.
from __future__ import annotations

import logging

import httpx

from ..core.settings import get_settings

log = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self) -> None:
        self.base_url = get_settings().ollama_base_url.rstrip("/")

    def generate(self, model: str, prompt: str, timeout: int = 120) -> str:
        url     = f"{self.base_url}/api/generate"
        payload = {"model": model, "prompt": prompt, "stream": False}
        log.debug("ollama generate model=%s url=%s", model, url)
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
                return data.get("response", "")
        except httpx.TimeoutException:
            log.warning("ollama timeout model=%s", model)
            raise RuntimeError(f"Ollama timeout after {timeout}s for model {model!r}")
        except httpx.HTTPStatusError as e:
            log.error("ollama http error %s model=%s", e.response.status_code, model)
            raise RuntimeError(f"Ollama error {e.response.status_code}: {e.response.text}")

    def is_reachable(self) -> bool:
        """Quick health check — returns True if Ollama is responding."""
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return list of available model names."""
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
        except Exception as e:
            log.warning("ollama list_models failed: %s", e)
            return []
