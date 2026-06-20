import httpx
import os
import logging
import json
from typing import AsyncGenerator, Any

log = logging.getLogger(__name__)

class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=90.0)

    async def generate(self, model: str, messages: list[dict]) -> str:
        if "glm" in model.lower():
            try:
                api_key = os.environ.get("GLM_API_KEY", "")
                base_url = os.environ.get("GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": model,
                    "messages": messages,
                    "stream": False
                }
                async with httpx.AsyncClient(timeout=60.0) as cloud_client:
                    resp = await cloud_client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                    if resp.status_code == 200:
                        res_data = resp.json()
                        choices = res_data.get("choices", [])
                        if choices:
                            return choices[0].get("message", {}).get("content", "").strip()
                        return f"[System Note: Cloud response missing expected structure. Raw: {res_data}]"
                    raise RuntimeError(f"GLM cloud error {resp.status_code}: {resp.text}")
            except Exception as e:
                log.error(f"Error in OllamaClient.generate (GLM fork): {e}")
                raise
        else:
            payload = {"model": model, "messages": messages, "stream": False}
            try:
                resp = await self.client.post("/api/chat", json=payload)
                if resp.status_code == 200:
                    return resp.json().get("message", {}).get("content", "").strip()
                raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text}")
            except Exception as e:
                log.error(f"Error in OllamaClient.generate: {e}")
                raise

    async def stream_generate(self, model: str, messages: list[dict]) -> AsyncGenerator[str, None]:
        if "glm" in model.lower():
            try:
                api_key = os.environ.get("GLM_API_KEY", "")
                base_url = os.environ.get("GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": model,
                    "messages": messages,
                    "stream": True
                }
                async with httpx.AsyncClient(timeout=60.0) as cloud_client:
                    async with cloud_client.stream("POST", f"{base_url}/chat/completions", json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            raise RuntimeError(f"GLM cloud stream error {response.status_code}")
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
                            except Exception:
                                pass
            except Exception as e:
                log.error(f"Error in OllamaClient.stream_generate (GLM fork): {e}")
                raise
        else:
            payload = {"model": model, "messages": messages, "stream": True}
            try:
                async with self.client.stream("POST", "/api/chat", json=payload) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"Ollama stream error {response.status_code}")
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                chunk = data.get("message", {}).get("content", "")
                                if chunk:
                                    yield chunk
                            except Exception:
                                pass
            except Exception as e:
                log.error(f"Error in OllamaClient.stream_generate: {e}")
                raise

    async def is_reachable(self) -> bool:
        try:
            resp = await self.client.get("/")
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            resp = await self.client.get("/api/tags")
            if resp.status_code == 200:
                return [m["name"] for m in resp.json().get("models", [])]
            return []
        except Exception:
            return []
