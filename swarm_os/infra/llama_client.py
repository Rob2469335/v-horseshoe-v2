import httpx
import os
import logging
import json
from typing import AsyncGenerator

log = logging.getLogger(__name__)

_glm_client: httpx.AsyncClient | None = None


def _get_glm_client() -> httpx.AsyncClient:
    global _glm_client
    if _glm_client is None:
        _glm_client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _glm_client


class LlamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self.base_url = base_url
        limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
        timeout = httpx.Timeout(600.0, connect=5.0)
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            limits=limits,
            headers={"Authorization": "Bearer llama"},
        )

    async def generate(
        self,
        model: str,
        messages: list[dict],
        *,
        chat_template_kwargs: dict | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if "glm" in model.lower():
            try:
                api_key = os.environ.get("GLM_API_KEY", "")
                base_url = os.environ.get(
                    "GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4"
                )
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                }
                if chat_template_kwargs:
                    payload["chat_template_kwargs"] = chat_template_kwargs
                if max_tokens:
                    payload["max_tokens"] = max_tokens
                cloud_client = _get_glm_client()
                resp = await cloud_client.post(
                    f"{base_url}/chat/completions", json=payload, headers=headers
                )
                if resp.status_code == 200:
                    res_data = resp.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "").strip()
                    return f"[System Note: Cloud response missing expected structure. Raw: {res_data}]"
                raise RuntimeError(f"GLM cloud error {resp.status_code}: {resp.text}")
            except Exception as e:
                log.error(f"Error in LlamaClient.generate (GLM fork): {e}")
                raise
        else:
            payload: dict = {"model": model, "messages": messages, "stream": False}
            if chat_template_kwargs:
                payload["chat_template_kwargs"] = chat_template_kwargs
            if max_tokens:
                payload["max_tokens"] = max_tokens
            try:
                resp = await self.client.post("/v1/chat/completions", json=payload)
                if resp.status_code == 200:
                    res_data = resp.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "").strip()
                    return ""
                raise RuntimeError(f"llama.cpp error {resp.status_code}: {resp.text}")
            except Exception as e:
                log.error(f"Error in LlamaClient.generate: {e}")
                raise

    async def stream_generate(
        self, model: str, messages: list[dict]
    ) -> AsyncGenerator[str, None]:
        if "glm" in model.lower():
            try:
                api_key = os.environ.get("GLM_API_KEY", "")
                base_url = os.environ.get(
                    "GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4"
                )
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {"model": model, "messages": messages, "stream": True}
                cloud_client = _get_glm_client()
                async with cloud_client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"GLM cloud stream error {response.status_code}"
                        )
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            line = line[6:].strip()
                        if not line or line == "[DONE]":
                            continue
                        try:
                            data = json.loads(line)
                            choices = data.get("choices", [])
                            if choices:
                                chunk = choices[0].get("delta", {}).get("content", "")
                                if chunk:
                                    yield chunk
                        except Exception as exc:
                            log.debug(
                                "LLM stream chunk parse skipped (GLM fork): %s", exc
                            )
            except Exception as e:
                log.error(f"Error in LlamaClient.stream_generate (GLM fork): {e}")
                raise
        else:
            payload = {"model": model, "messages": messages, "stream": True}
            try:
                async with self.client.stream(
                    "POST", "/v1/chat/completions", json=payload
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"llama.cpp stream error {response.status_code}"
                        )
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            line = line[6:].strip()
                        if not line or line == "[DONE]":
                            continue
                        try:
                            data = json.loads(line)
                            choices = data.get("choices", [])
                            if choices:
                                chunk = choices[0].get("delta", {}).get("content", "")
                                if chunk:
                                    yield chunk
                        except Exception as exc:
                            log.debug("LLM stream chunk parse skipped: %s", exc)
            except Exception as e:
                log.error(f"Error in LlamaClient.stream_generate: {e}")
                raise

    async def is_reachable(self) -> bool:
        try:
            resp = await self.client.get("/health")
            if resp.status_code == 200:
                return True
            resp2 = await self.client.get("/v1/models")
            return resp2.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            resp = await self.client.get("/v1/models")
            if resp.status_code == 200:
                return [
                    m.get("id", m.get("name", ""))
                    for m in resp.json().get("data", [])
                    if m.get("id") or m.get("name")
                ]
            return []
        except Exception:
            return []

    async def aclose(self) -> None:
        """Close the underlying httpx client to release connection pool."""
        await self.client.aclose()


OllamaClient = LlamaClient  # Backward compatibility alias
