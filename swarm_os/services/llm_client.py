import os
import json
import logging
import httpx

log = logging.getLogger(__name__)

# Reusing the orchestrator's global client pattern
global_httpx_client = httpx.AsyncClient(timeout=120.0)


async def close_global_client() -> None:
    """Close the module-level shared httpx client on shutdown."""
    await global_httpx_client.aclose()

class CloudLLMClient:
    """Service to handle Cloud LLM providers (OpenRouter, NVIDIA)."""

    @staticmethod
    def detect_provider(model_name: str) -> str:
        model_name = model_name.lower()
        if model_name.startswith("openrouter/") or "/" in model_name:
            if "nvidia" in model_name and "openrouter" not in model_name:
                return "nvidia"
            return "openrouter"
        if model_name.startswith("nvidia/"):
            return "nvidia"
        
        # Models known to be local via llama.cpp
        local_prefixes = ["qwen", "llama", "phi", "mistral", "gemma", "llava", "moondream", "deepseek", "nomic"]
        if any(model_name.startswith(p) for p in local_prefixes):
            return "llama"
        return "llama"

    @staticmethod
    async def generate(model: str, messages: list[dict], provider: str, stream: bool = False):
        if provider == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
            base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/v-horseshoe-v2",
                "X-Title": "Swarm OS",
            }
        elif provider == "nvidia":
            api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
            base_url = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        else:
            raise ValueError(f"Unsupported cloud provider: {provider}")

        # Strip prefixes from model name for the cloud API
        clean_model = model.replace("openrouter/", "").replace("nvidia/", "")
        if provider == "openrouter" and any(forbidden in clean_model.lower() for forbidden in ("claude", "anthropic", "sonnet", "opus", "gpt-4")):
            clean_model = "deepseek/deepseek-v4-flash"
        
        payload = {
            "model": clean_model,
            "messages": messages,
            "stream": stream,
            "temperature": 0.7,
            "max_tokens": 1500,
        }

        if stream:
            return CloudLLMClient.stream_generate(url, payload, headers)

        response = await global_httpx_client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    @staticmethod
    async def stream_generate(url: str, payload: dict, headers: dict):
        async with global_httpx_client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    line = line[6:].strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    data = json.loads(line)
                    delta = data["choices"][0].get("delta", {})
                    if "content" in delta:
                        yield delta["content"]
                except Exception:
                    continue
import time
import random
from typing import Any, Dict, List, Optional

class SwarmBrainClient:
    def __init__(self, swarm_url: str = "http://127.0.0.1:8000/generate"):
        self.swarm_url = swarm_url

    @staticmethod
    def parse_sse_stream(
        body_text: str,
        requested_model: Optional[str],
        default_model: str,
        active_tools: List[str],
        elapsed: float,
        top_k: int,
        system_prompt_len: int,
    ) -> Dict[str, Any]:
        content_parts = []
        finish_reason = ""
        total_tokens = 0
        prompt_tokens = 0

        for raw_line in body_text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                evt = json.loads(chunk)
            except Exception:
                continue

            choices = evt.get("choices", [])
            if not choices:
                continue

            choice0 = choices[0]
            delta = choice0.get("delta", {})
            piece = delta.get("content", "")
            if piece:
                content_parts.append(piece)

            if choice0.get("finish_reason"):
                finish_reason = choice0.get("finish_reason") or ""

            usage = evt.get("usage", {})
            total_tokens = usage.get("total_tokens", total_tokens)
            prompt_tokens = usage.get("prompt_tokens", prompt_tokens)

        return {
            "content": "".join(content_parts),
            "model": requested_model or default_model,
            "tools_used": active_tools,
            "elapsed": elapsed,
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "finish_reason": finish_reason,
            "tool_calls": [],
            "cost": total_tokens / 1000.0 if total_tokens else 0.0,
            "retrieval_top_k": top_k,
            "system_prompt_len": system_prompt_len,
        }

    def generate(
        self,
        org_id: str,
        requested_model: Optional[str],
        default_model: str,
        payload: Dict[str, Any],
        top_k: int,
        active_tools: List[str],
        system_prompt_len: int,
        timeout_budget: float = 300.0,
    ) -> Dict[str, Any]:
        timeout = httpx.Timeout(max(30.0, float(timeout_budget)), connect=10.0)
        attempts = 3
        resp = None
        elapsed = 0.0
        status = 0
        content_type = ""
        body_text = ""
        t0 = time.perf_counter()

        with httpx.Client(timeout=timeout, headers={"Authorization": "Bearer llama"}) as client:
            for attempt in range(1, attempts + 1):
                resp = client.post(self.swarm_url, json=payload)
                elapsed = time.perf_counter() - t0
                status = resp.status_code
                content_type = resp.headers.get("content-type", "")
                body_text = resp.text or ""

                if status != 429:
                    break

                if attempt < attempts:
                    delay = min(2.0, 0.35 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.15)
                    log.warning("brain rate limited org=%s model=%s attempt=%d/%d", org_id, requested_model, attempt, attempts)
                    time.sleep(delay)

        if status >= 400:
            log.error("brain http error org=%s status=%s", org_id, status)
            return {
                "error": f"http_{status}", "cost": 5.0, "elapsed": elapsed, "content": "",
                "model": requested_model or default_model, "tools_used": active_tools,
                "finish_reason": f"http_{status}", "response_preview": body_text[:1000],
            }

        if not body_text.strip():
            return {
                "error": "empty_response", "cost": 5.0, "elapsed": elapsed, "content": "",
                "model": requested_model or default_model, "tools_used": active_tools,
                "finish_reason": "empty_response", "response_preview": "",
            }

        if "text/event-stream" in content_type.lower():
            return self.parse_sse_stream(body_text, requested_model, default_model, active_tools, elapsed, top_k, system_prompt_len)

        try:
            data = resp.json()
        except Exception:
            return {
                "error": "invalid_json", "cost": 5.0, "elapsed": elapsed, "content": "",
                "model": requested_model or default_model, "tools_used": active_tools,
                "finish_reason": "invalid_json", "response_preview": body_text[:1000],
            }

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        return {
            "content": message.get("content", ""),
            "model": requested_model or default_model,
            "tools_used": active_tools,
            "elapsed": elapsed,
            "total_tokens": total_tokens,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "finish_reason": choice.get("finish_reason", ""),
            "tool_calls": message.get("tool_calls", []),
            "cost": total_tokens / 1000.0,
            "retrieval_top_k": top_k,
            "system_prompt_len": system_prompt_len,
        }
