"""Persistent per-model token & cost telemetry.

The in-memory `token_tracker.py` loses all counters on restart and never touches
disk. This module is the durable record: one JSON line per LLM completion in
``data/usage/usage.jsonl`` (gitignored), appended under a threading lock so
concurrent async callers (and the stream path) never interleave writes.

Design rules:
- Record ONLY real ``usage`` dicts from litellm responses — never content-length
  estimates. If a response carries no usage, the call is skipped.
- Best-effort only: a write failure logs at debug level and never raises, so
  telemetry can never break a generation.
- Cost is estimated from a small per-provider pricing table (per 1M tokens,
  input-miss / cache-hit-input / output). Models not in the table report
  ``cost: null`` (honest "unknown") rather than a guessed number. Local
  llama.cpp models are ``0.0``.

Public API: ``record_usage(...)``, ``extract_usage(resp)``, ``usage_report(days)``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_USAGE_PATH = Path(os.getenv("SWARM_USAGE_LOG", "data/usage/usage.jsonl"))
_write_lock = threading.Lock()

# Local llama.cpp models (openai/<name> not gpt/o1/o3/deepseek) and bare local
# names are $0. Cloud entries use rates researched 2026-08.
_PRICING = {
    "deepseek/deepseek-v4-flash": (0.14, 0.0028, 0.28),
    "deepseek/deepseek-v4-pro": (0.435, 0.003625, 0.87),
    "deepseek/deepseek-chat": (0.14, 0.0028, 0.28),
    "deepseek-r1": (0.14, 0.0028, 0.28),
    "deepseek/deepseek-r1": (0.14, 0.0028, 0.28),
    "openrouter/deepseek/deepseek-v4-flash-0731": (0.09, 0.09, 0.18),
    "openrouter/deepseek/deepseek-v4-flash": (0.14, 0.14, 0.28),
    "openrouter/deepseek/deepseek-chat": (0.0896, 0.0896, 0.1792),
    "openrouter/deepseek/deepseek-chat:free": (0.0, 0.0, 0.0),
    "nvidia_nim/deepseek-ai/deepseek-v4-flash": (0.0, 0.0, 0.0),
    # InclusionAI Ling — ultra-cheap worker tier (256K ctx, 104B MoE / 7.4B active).
    "openrouter/inclusionai/ling-2.6-flash": (0.01, 0.003, 0.03),
    "openrouter/inclusionai/ling-3.0-flash:free": (0.0, 0.0, 0.0),
    # OpenCode Go (paid subscription, https://opencode.ai/zen/go/v1) — billed
    # against the monthly dollar cap, so marginal cost per call is $0.
    "openai/deepseek-v4-flash": (0.0, 0.0, 0.0),
    "openai/zen/deepseek-v4-flash": (0.0, 0.0, 0.0),
}

# Model-string prefixes whose provider we can name but whose price we do NOT
# hardcode — report null cost rather than guessing (Gemini/Groq/NVIDIA/OpenAI
# paid vary by model and change often).
_UNKNOWN_CLOUD_PREFIXES = ("openrouter/", "groq/", "nvidia", "gemini/", "openai/gpt-", "openai/o1", "openai/o3")


def _provider_of(model: str) -> str:
    m = (model or "").lower()
    if not m:
        return "unknown"
    if m.startswith("deepseek/"):
        return "deepseek_direct"
    if m.startswith("openrouter/"):
        return "openrouter"
    if m.startswith("groq/"):
        return "groq"
    if m.startswith("nvidia"):
        return "nvidia"
    if m.startswith("gemini/"):
        return "gemini"
    if m.startswith("openai/"):
        name = m.replace("openai/", "")
        if name.startswith("zen/"):
            return "opencode_go"
        if name.startswith("gpt") or name.startswith("o1") or name.startswith("o3") or "deepseek" in name:
            return "openai_paid"
        return "local"
    return "local"


def _is_local(model: str) -> bool:
    return _provider_of(model) == "local"


def _unit_cost(model: str) -> tuple | None:
    """Return (input_miss, input_cache_hit, output) per 1M tokens, or None if unknown."""
    m = (model or "").lower()
    if _is_local(model):
        return (0.0, 0.0, 0.0)
    exact = _PRICING.get(m)
    if exact is not None:
        return exact
    if any(m.startswith(p) for p in _UNKNOWN_CLOUD_PREFIXES):
        return None
    return None


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float | None:
    """Estimated USD for one call. ``cached_tokens`` is the DeepSeek cache-hit
    portion of prompt_tokens (billed ~98% cheaper than a miss)."""
    rates = _unit_cost(model)
    if rates is None:
        return None
    in_miss, in_hit, out = rates
    prompt_miss = max(0, int(prompt_tokens) - int(cached_tokens))
    prompt_hit = min(int(cached_tokens), int(prompt_tokens))
    return (
        prompt_miss / 1_000_000 * in_miss
        + prompt_hit / 1_000_000 * in_hit
        + int(completion_tokens) / 1_000_000 * out
    )


def _coerce_usage(usage: Any) -> dict | None:
    """Normalize a litellm Usage object / dict / pydantic model into a plain dict."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        u = usage
    elif hasattr(usage, "model_dump"):
        u = usage.model_dump()
    elif hasattr(usage, "__dict__"):
        u = vars(usage)
    else:
        try:
            u = dict(usage)
        except Exception:
            return None
    if not isinstance(u, dict) or not u:
        return None
    return u


def extract_usage(resp: Any) -> dict | None:
    """Pull ``{prompt_tokens, completion_tokens, cached_tokens}`` from a litellm
    completion response (object or dict). Works for non-streaming responses and
    for the final chunk of a stream (which carries the aggregate usage)."""
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    u = _coerce_usage(usage)
    if u is None:
        return None
    prompt = int(u.get("prompt_tokens") or 0)
    completion = int(u.get("completion_tokens") or u.get("completion_tokens_details") and 0 or 0)
    if not completion:
        completion = int(u.get("total_tokens", 0)) - prompt
        if completion < 0:
            completion = 0
    cached = 0
    details = u.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    cached = cached or int(u.get("prompt_cache_hit_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "cached_tokens": cached}


def record_usage(
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    source: str = "",
    agent_id: str = "",
    ok: bool = True,
) -> None:
    """Append one usage record. Never raises — telemetry must not break calls."""
    if not model and not (prompt_tokens or completion_tokens):
        return
    row = {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "provider": _provider_of(model),
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "cached_tokens": int(cached_tokens),
        "cost": estimate_cost(model, prompt_tokens, completion_tokens, cached_tokens),
        "source": source,
        "agent_id": agent_id,
        "ok": bool(ok),
    }
    line = json.dumps(row, ensure_ascii=False) + "\n"
    try:
        with _write_lock:
            _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _USAGE_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
    except Exception as e:  # noqa: BLE001
        log.debug("usage log write failed: %s", e)


def record_response(resp: Any, model: str, source: str = "", agent_id: str = "", ok: bool = True) -> None:
    """Record usage from a litellm response if it carries one."""
    u = extract_usage(resp)
    if u is None:
        return
    record_usage(
        model=model,
        prompt_tokens=u["prompt_tokens"],
        completion_tokens=u["completion_tokens"],
        cached_tokens=u["cached_tokens"],
        source=source,
        agent_id=agent_id,
        ok=ok,
    )


def usage_report(days: int = 30) -> dict[str, Any]:
    """Aggregate the persisted log into per-model + total cost over the window.

    Returns ``{days, total_cost, known_cost, unknown_cost, per_model: {...}}``.
    ``known_cost`` sums only records with a price; ``unknown_cost`` is null-priced
    traffic (openrouter variants not in the pricing table, etc.)."""
    if not _USAGE_PATH.exists():
        return {"days": days, "total_cost": None, "known_cost": 0.0, "unknown_cost": 0.0, "per_model": {}, "rows": 0}
    cutoff = time.time() - days * 86400
    per_model: dict[str, dict] = {}
    known = 0.0
    unknown = 0.0
    rows = 0
    with _USAGE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if float(row.get("ts", 0)) < cutoff:
                continue
            rows += 1
            model = str(row.get("model") or "?")
            cost = row.get("cost")
            m = per_model.setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": None, "provider": row.get("provider")})
            m["calls"] += 1
            m["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
            m["completion_tokens"] += int(row.get("completion_tokens") or 0)
            if cost is None:
                unknown += 0.0
            else:
                cost = float(cost)
                known += cost
                m["cost"] = (m["cost"] or 0.0) + cost
    return {
        "days": days,
        "total_cost": known if unknown == 0.0 else None,
        "known_cost": known,
        "unknown_cost": unknown,
        "per_model": per_model,
        "rows": rows,
    }
