# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx

_SEMAPHORE_LIMIT = max(1, int(os.environ.get("SWARMMODELCONCURRENCY", "4")))
MODEL_SEMAPHORE = asyncio.Semaphore(_SEMAPHORE_LIMIT)

_http_client = httpx.AsyncClient(
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
    timeout=httpx.Timeout(120.0)
)

SWARM_MODEL = os.environ.get("SWARMMODEL", "qwen3.5-4b")
EMBED_MODEL = os.environ.get("EMBEDMODEL", "nomic-embed-text-v1.5")
FAST_MODEL = os.environ.get("FASTMODEL", "qwen3.5-4b")
AGENT_MODEL = os.environ.get("SWARMAGENTMODEL", os.environ.get("ROUTEAGENTMODEL", SWARM_MODEL))
CODE_MODEL = os.environ.get("SWARMCODEMODEL", os.environ.get("ROUTECODEMODEL", "qwen3.5-4b"))
CHAT_MODEL = os.environ.get("SWARMCHATMODEL", os.environ.get("ROUTECHATMODEL", "qwen3.5-4b"))

async def safe_generate_async(prompt: str, model: str, num_predict: int = 200, keep_alive: str = "15m", timeout: float = 120.0) -> str:
    async with MODEL_SEMAPHORE:
        try:
            if "glm" in model.lower():
                api_key = os.environ.get("GLM_API_KEY", "")
                base_url = os.environ.get("GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": num_predict
                }
                resp = await _http_client.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                return f"GLM Error {resp.status_code}: {resp.text}"
            else:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": num_predict
                }
                resp = await _http_client.post("http://127.0.0.1:8080/v1/chat/completions", headers={"Authorization": "Bearer llama"}, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                return f"Llama Error {resp.status_code}: {resp.text}"
        except asyncio.TimeoutError:
            return ""
        except Exception:
            return ""

async def safe_chat(messages: List[Dict[str, Any]], model: str, tools: Optional[List[Dict[str, Any]]] = None, num_predict: int = 4096, keep_alive: str = "15m", timeout: float = 120.0) -> Dict[str, Any]:
    async with MODEL_SEMAPHORE:
        try:
            if "glm" in model.lower():
                api_key = os.environ.get("GLM_API_KEY", "")
                base_url = os.environ.get("GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": num_predict
                }
                if tools:
                    payload["tools"] = tools
                resp = await _http_client.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    res_json = resp.json()
                    msg = res_json["choices"][0]["message"]
                    return {
                        "response": msg.get("content") or "",
                        "done": True,
                        "tool_calls": msg.get("tool_calls") or []
                    }
                return {"response": f"GLM Error {resp.status_code}: {resp.text}", "done": True, "tool_calls": []}
            else:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": num_predict
                }
                if tools:
                    payload["tools"] = tools
                    
                resp = await _http_client.post("http://127.0.0.1:8080/v1/chat/completions", headers={"Authorization": "Bearer llama"}, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    res_json = resp.json()
                    msg = res_json["choices"][0]["message"]
                    return {
                        "response": msg.get("content") or "",
                        "done": True,
                        "tool_calls": msg.get("tool_calls") or []
                    }
                return {"response": f"Llama Error {resp.status_code}: {resp.text}", "done": True, "tool_calls": []}
        except asyncio.TimeoutError:
            return {"response": "", "done": True, "tool_calls": []}
        except Exception:
            return {"response": "", "done": True, "tool_calls": []}

async def check_llm_connectivity() -> None:
    try:
        await _http_client.get("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer llama"}, timeout=5.0)
    except Exception:
        return

check_ollama_connectivity = check_llm_connectivity  # Backward compatibility alias


async def preload_models() -> None:
    pass

async def model_heartbeat() -> None:
    pass

def get_cloud_client(config: dict):
    from openai import OpenAI
    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        raise ValueError(f"Missing API key: {config['api_key_env']}")
    return OpenAI(api_key=api_key, base_url=config["base_url"])
