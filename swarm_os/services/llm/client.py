# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx
import ollama

_SEMAPHORE_LIMIT = max(1, int(os.environ.get("SWARMMODELCONCURRENCY", "4")))
MODEL_SEMAPHORE = asyncio.Semaphore(_SEMAPHORE_LIMIT)

SWARM_MODEL = os.environ.get("SWARMMODEL", "qwen-tuned:latest")
EMBED_MODEL = os.environ.get("EMBEDMODEL", "qwen3-embedding:8b")
FAST_MODEL = os.environ.get("FASTMODEL", "qwen-tuned:latest")
AGENT_MODEL = os.environ.get("SWARMAGENTMODEL", os.environ.get("ROUTEAGENTMODEL", SWARM_MODEL))
CODE_MODEL = os.environ.get("SWARMCODEMODEL", os.environ.get("ROUTECODEMODEL", "qwen-tuned:latest"))
CHAT_MODEL = os.environ.get("SWARMCHATMODEL", os.environ.get("ROUTECHATMODEL", "qwen-tuned:latest"))

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
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()["choices"][0]["message"]["content"].strip()
                    return f"GLM Error {resp.status_code}: {resp.text}"
            else:
                client = ollama.AsyncClient(host="http://127.0.0.1:11434")
                r = await asyncio.wait_for(
                    client.generate(model=model, prompt=prompt, keep_alive=keep_alive, options={"num_predict": num_predict}),
                    timeout=timeout
                )
                if isinstance(r, dict):
                    return (r.get("response") or r.get("text") or "").strip()
                return getattr(r, "response", str(r)).strip()
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
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
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
                client = ollama.AsyncClient(host="http://127.0.0.1:11434")
                options = {"num_predict": num_predict}
                think = True if "qwen3" in model.lower() else None
                chat_kwargs = dict(model=model, messages=messages, options=options, keep_alive=keep_alive)
                if think is not None:
                    chat_kwargs["think"] = think
                if tools and model != "phi4-mini:latest":
                    chat_kwargs["tools"] = tools
                
                response = await asyncio.wait_for(
                    client.chat(**chat_kwargs),
                    timeout=timeout
                )
                if isinstance(response, dict):
                    msg = response.get("message") or {}
                    content = msg.get("content") or response.get("response") or response.get("text") or ""
                    tool_calls = msg.get("tool_calls") or []
                    return {"response": content, "done": response.get("done", True), "tool_calls": tool_calls}
                if hasattr(response, "message"):
                    message = getattr(response, "message")
                    content = getattr(message, "content", "") or getattr(response, "response", "") or getattr(response, "text", "")
                    tool_calls = getattr(message, "tool_calls", []) or []
                    return {"response": content, "done": getattr(response, "done", True), "tool_calls": tool_calls}
                return {"response": str(response).strip(), "done": getattr(response, "done", True), "tool_calls": []}
        except asyncio.TimeoutError:
            return {"response": "", "done": True, "tool_calls": []}
        except Exception:
            return {"response": "", "done": True, "tool_calls": []}

async def check_ollama_connectivity() -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.get("http://127.0.0.1:11434/api/tags")
    except Exception:
        return

async def preload_models() -> None:
    client = ollama.AsyncClient(host="http://127.0.0.1:11434")
    for model in {SWARM_MODEL, EMBED_MODEL}:
        try:
            if model == EMBED_MODEL:
                await client.embeddings(model=model, prompt="warmup", keep_alive="15m")
            else:
                await client.generate(model=model, prompt="Hi", options={"num_predict": 1}, keep_alive="15m")
        except Exception:
            continue

async def model_heartbeat() -> None:
    client = ollama.AsyncClient(host="http://127.0.0.1:11434")
    hot_models = [AGENT_MODEL, CODE_MODEL, CHAT_MODEL, FAST_MODEL, EMBED_MODEL]
    await asyncio.sleep(30)
    while True:
        for model in hot_models:
            try:
                await client.generate(model=model, prompt=".", options={"num_predict": 1}, keep_alive="15m")
            except Exception:
                continue
        await asyncio.sleep(int(os.environ.get("SWARMHEARTBEATINTERVAL", "480")))

def get_cloud_client(config: dict):
    from openai import OpenAI
    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        raise ValueError(f"Missing API key: {config['api_key_env']}")
    return OpenAI(api_key=api_key, base_url=config["base_url"])
