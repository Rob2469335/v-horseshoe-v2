# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import httpx
import ollama

_SEMAPHORE_LIMIT = max(1, int(os.environ.get("SWARMMODELCONCURRENCY", "2")))
_THREAD_POOL = ThreadPoolExecutor(max_workers=_SEMAPHORE_LIMIT)
MODEL_SEMAPHORE = asyncio.Semaphore(_SEMAPHORE_LIMIT)

SWARM_MODEL = os.environ.get("SWARMMODEL", "qwen3:14b")
EMBED_MODEL = os.environ.get("EMBEDMODEL", "nomic-embed-text:latest")
FAST_MODEL = os.environ.get("FASTMODEL", "mistral-nemo:12b")
AGENT_MODEL = os.environ.get("SWARMAGENTMODEL", os.environ.get("ROUTEAGENTMODEL", SWARM_MODEL))
CODE_MODEL = os.environ.get("SWARMCODEMODEL", os.environ.get("ROUTECODEMODEL", "qwen2.5-coder:14b"))
CHAT_MODEL = os.environ.get("SWARMCHATMODEL", os.environ.get("ROUTECHATMODEL", "qwen2.5-coder:7b"))

def _sync_generate(prompt: str, model: str, num_predict: int = 200, keep_alive: str = "3m") -> str:
    try:
        r = ollama.generate(model=model, prompt=prompt, keep_alive=keep_alive, options={"num_predict": num_predict})
        if isinstance(r, dict):
            return (r.get("response") or r.get("text") or "").strip()
        return getattr(r, "response", str(r)).strip()
    except Exception:
        return ""

async def safe_generate_async(prompt: str, model: str, num_predict: int = 200, keep_alive: str = "3m", timeout: float = 90.0) -> str:
    async with MODEL_SEMAPHORE:
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_THREAD_POOL, _sync_generate, prompt, model, num_predict, keep_alive),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return ""

def _sync_chat(messages: List[Dict[str, Any]], model: str, tools: Optional[List[Dict[str, Any]]], num_predict: int, keep_alive: str) -> Dict[str, Any]:
    try:
        options = {"num_predict": num_predict}
        think = False if "qwen3" in model.lower() else None
        chat_kwargs = dict(model=model, messages=messages, options=options, keep_alive=keep_alive)
        if think is not None:
            chat_kwargs["think"] = think
        if tools:
            chat_kwargs["tools"] = tools
        response = ollama.chat(**chat_kwargs)
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
    except Exception:
        return {"response": "", "done": True, "tool_calls": []}

async def safe_chat(messages: List[Dict[str, Any]], model: str, tools: Optional[List[Dict[str, Any]]] = None, num_predict: int = 200, keep_alive: str = "3m", timeout: float = 90.0) -> Dict[str, Any]:
    async with MODEL_SEMAPHORE:
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_THREAD_POOL, _sync_chat, messages, model, tools, num_predict, keep_alive),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return {"response": "", "done": True, "tool_calls": []}

async def check_ollama_connectivity() -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.get("http://127.0.0.1:11434/api/tags")
    except Exception:
        return

async def preload_models() -> None:
    for model in {SWARM_MODEL, EMBED_MODEL}:
        try:
            loop = asyncio.get_running_loop()
            if model == EMBED_MODEL:
                await loop.run_in_executor(None, lambda m=model: ollama.embeddings(model=m, prompt="warmup"))
            else:
                await loop.run_in_executor(None, lambda m=model: ollama.generate(model=m, prompt="Hi", options={"num_predict": 1}, keep_alive="10m"))
        except Exception:
            continue

async def model_heartbeat() -> None:
    hot_models = [AGENT_MODEL, CODE_MODEL, CHAT_MODEL, FAST_MODEL, EMBED_MODEL]
    await asyncio.sleep(30)
    while True:
        for model in hot_models:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    _THREAD_POOL,
                    lambda m=model: ollama.generate(model=m, prompt=".", options={"num_predict": 1}, keep_alive="10m"),
                )
            except Exception:
                continue
        await asyncio.sleep(int(os.environ.get("SWARMHEARTBEATINTERVAL", "480")))