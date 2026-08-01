"""LLM deep-dive — the prompt is an artifact, the LLM is an adapter.

Cloud DeepSeek (OpenRouter) is attempted first, with the local qwen3.5-9b as a
fallback. Both run inside the same try/except chain so a failed provider
degrades to the next instead of failing the whole search.
"""
from __future__ import annotations

import asyncio
import logging
import os

from .models import RVListing

logger = logging.getLogger(__name__)

_LLM_ANALYSIS_PROMPT = """You are a ruthless used-RV deal analyst. Below are the top candidate listings a buyer
found (all under a ${budget} budget) for two people to live in full-time (with a shower). Score each on
value-for-money, condition, and fit for two-person living, then pick the single best deal.

Candidates (score is the heuristic Deal Score out of 100; fields: year make model | type | price | score (verdict) |
fair value | location | engine | mpg | solar | 2-person livability | life-ease score/15 | known weak spots | red flags):
{candidates}

Produce a markdown report with:
1. A one-paragraph overall market read (prices, value range for the budget, whether buyers have leverage).
2. A per-listing verdict (Best Deal / Strong Candidate / Risky / Pass) with 2-3 concrete reasons each. Call out
   solar hookup effort, fuel economy, known weak spots to inspect, and whether two people can realistically live in it
   (shower, walk-around bed, lithium/solar off-grid capability).
3. A clear final "BEST DEAL" pick with your reasoning and a suggested opening offer.
Be specific and skeptical. Call out water-damage/leak risks. Keep the whole report under 900 words."""


def _build_deep_dive_prompt(listings: list[RVListing], budget: int) -> str:
    lines = []
    for lst in listings[:8]:
        s = lst.analysis.get("score") or {}
        eng = lst.analysis.get("engine") or {}
        mpg = lst.analysis.get("mpg") or {}
        solar = lst.analysis.get("solar") or {}
        liv = lst.analysis.get("livability") or {}
        le = lst.analysis.get("life_ease") or {}
        ws = lst.analysis.get("weak_spots") or {}
        flags = ", ".join((lst.analysis.get("red_flags") or [])[:3]) or "none"
        lines.append(
            f"- {lst.year} {lst.make} {lst.model} | {lst.rv_type} | ${int(lst.price):,} | "
            f"score {s.get('score', 'n/a')} ({s.get('verdict', 'n/a')}) | "
            f"fair ${lst.analysis.get('fair_value', {}).get('fair', 0):,} | {lst.location or 'loc n/a'} | "
            f"engine: {eng.get('engine', 'n/a')} | mpg: {mpg.get('mpg_estimate', 'n/a')} | "
            f"solar: {'yes' if solar.get('has_solar') else 'no'} | "
            f"2-people: {liv.get('verdict', 'unknown')} | "
            f"life-ease: {le.get('present_count', '?')}/{le.get('total_count', '?')} "
            f"({le.get('score', '?')}/100) | "
            f"weak spots: {len(ws.get('weak_spots') or [])} | flags: {flags}"
        )
    return _LLM_ANALYSIS_PROMPT.format(budget=int(budget), candidates="\n".join(lines))


async def _llm_deep_dive(listings: list[RVListing], budget: int) -> str:
    prompt = _build_deep_dive_prompt(listings, budget)

    try:
        from litellm import acompletion

        attempts = []
        if os.environ.get("OPENROUTER_API_KEY"):
            attempts.append({
                "model": "openrouter/deepseek/deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
                "timeout": 60.0,
                "num_retries": 0,
            })
        attempts.append({
            "model": "qwen3.5-9b",
            "messages": [{"role": "system", "content": "/no_think\n\n"},
                         {"role": "user", "content": prompt}],
            "api_base": "http://127.0.0.1:8080/v1",
            "api_key": "llama",
            "custom_llm_provider": "openai",
            "max_tokens": 1200,
            "timeout": 300.0,
            "num_retries": 0,
        })
        for cfg in attempts:
            try:
                res = await asyncio.wait_for(acompletion(**cfg), timeout=cfg["timeout"])
                content = res.choices[0].message.content or ""
                if content.strip():
                    return content.strip()
            except Exception as e:
                logger.warning("deep-dive via %s failed: %s", cfg.get("model"), e)
    except Exception as e:
        logger.warning("deep-dive failed: %s", e)
    return ""
