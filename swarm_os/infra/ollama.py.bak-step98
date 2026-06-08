from __future__ import annotations

import json
import logging

import httpx

from ..core.settings import get_settings

log = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.ollama_base_url.rstrip("/")

    def _extract_content(self, data: dict) -> str:
        if "message" in data and isinstance(data["message"], dict):
            return data["message"].get("content", "") or ""

        choices = data.get("choices", [])
        if not choices:
            return ""

        choice = choices[0]
        content_obj = choice.get("message") or choice.get("delta", {})

        if isinstance(content_obj, dict):
            return content_obj.get("content", "") or ""
        return ""

    def generate(self, model: str, prompt: str, timeout: int = 120) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }

        log.debug("ollama chat model=%s url=%s", model, url)

        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json=payload)
                r.raise_for_status()

                body = r.text

                try:
                    data = r.json()
                    text = self._extract_content(data)
                    if text:
                        return text
                except Exception:
                    pass

                if "data:" in body:
                    full_text = []
                    parts = [p.strip() for p in body.splitlines() if p.strip().startswith("data:")]
                    for part in parts:
                        raw = part[5:].strip()
                        if raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                            text = self._extract_content(event)
                            if text:
                                full_text.append(text)
                        except json.JSONDecodeError:
                            continue

                    if full_text:
                        return "".join(full_text)

                raise RuntimeError(f"Ollama returned an empty or unparseable response from {url}")

        except httpx.TimeoutException:
            raise RuntimeError(f"Ollama timeout after {timeout}s for model {model!r}")
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama error {e.response.status_code}: {e.response.text}")

    def is_reachable(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                data = r.json()
                return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception as e:
            log.warning("ollama list_models failed: %s", e)
            return []
