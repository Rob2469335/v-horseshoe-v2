"""LLM deep-dive — the prompt is an artifact, the LLM is an adapter.

Cloud DeepSeek (OpenRouter) is attempted first, with the local qwen3.5-4b as a
fallback. Both run inside the same try/except chain so a failed provider
degrades to the next instead of failing the whole search.
"""

from __future__ import annotations

import asyncio
import logging
import os

from swarm_os.lib.opencode_session import opencode_headers
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
        # DeepSeek direct is the reliable prose provider (the OpenCode Go proxy
        # sends long prompts into reasoning_content and returns empty content).
        # NOTE: DeepSeek V4 flash reasons BEFORE answering (~8k reasoning tokens
        # for this prompt), so max_tokens must be high enough to leave room for
        # the actual report — 1200 gets eaten entirely by reasoning.
        if os.environ.get("DEEPSEEK_API_KEY"):
            attempts.append(
                {
                    "model": "deepseek/deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 3000,
                    "timeout": 120.0,
                    "num_retries": 0,
                }
            )
        if os.environ.get("OPENAI_API_KEY"):
            attempts.append(
                {
                    "model": "openai/deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 3000,
                    "timeout": 90.0,
                    "num_retries": 0,
                    "api_base": os.getenv(
                        "OPENAI_API_BASE", "https://opencode.ai/zen/go/v1"
                    ),
                    "api_key": os.getenv("OPENAI_API_KEY"),
                    "extra_headers": opencode_headers(),
                }
            )
        attempts.append(
            {
                "model": "qwen3.5-4b",
                "messages": [
                    {"role": "system", "content": "/no_think\n\n"},
                    {"role": "user", "content": prompt},
                ],
                "api_base": "http://127.0.0.1:8080/v1",
                "api_key": "llama",
                "custom_llm_provider": "openai",
                "max_tokens": 1200,
                "timeout": 300.0,
                "num_retries": 0,
            }
        )
        # Overall budget across the whole provider chain: a deep-dive is an
        # enhancement, not the search. Without a cap, 120+90+300s of sequential
        # provider timeouts could hang the API for ~8.5 minutes. 180s bounds the
        # cloud pair generously and still leaves the local fallback headroom.
        async with asyncio.timeout(180.0):
            for cfg in attempts:
                try:
                    async with asyncio.timeout(cfg["timeout"]):
                        res = await acompletion(**cfg)
                    content = res.choices[0].message.content or ""
                    if content.strip():
                        try:
                            from runtime_v2.services.usage_log import record_response

                            record_response(
                                res, cfg.get("model", ""), source="rv_finder_deep_dive"
                            )
                        except Exception as usage_err:  # noqa: BLE001
                            logger.debug("usage log skipped: %s", usage_err)
                        return content.strip()
                except Exception as e:
                    logger.warning("deep-dive via %s failed: %s", cfg.get("model"), e)
    except TimeoutError:
        logger.warning("rv_finder deep-dive exceeded overall 180s budget")
    except Exception as e:
        logger.warning("deep-dive failed: %s", e)
    return ""
