"""Parallel fan-out + iterative deep research.

Research-grounded in the two patterns that the 2026 agent landscape converged
on:

  * Manus "Wide Research": a main planner decomposes a goal into independent
    sub-questions; each sub-question runs in its OWN research unit with a fresh
    context (search -> fetch -> cited sub-synthesis); the units never talk to
    each other; a main synthesizer merges them. Context isolation is the entire
    point — a single agent at scale degrades to "generic filler by sub-task
    #50".
  * Perplexity/OpenAI "Deep Research": an iterative loop that runs several
    passes, evaluates what is still missing, and issues follow-up questions
    before the final synthesis.

This service is deterministic orchestration + LLM calls only. It reuses the
existing web_search_handler / web_fetch_handler primitives and the
analysis-cloud model (deepseek-v4-flash by default). Fail-closed: any failing
stage degrades to the evidence that did arrive, never fabricates sources.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

# Bounded fan-out: cap the number of concurrent sub-research units so a 30-sub
# question goal doesn't slam the search providers or the local model slot.
_MAX_CONCURRENCY = 4
_DEFAULT_SUB_QUESTIONS = 5
_DEFAULT_MAX_ITERATIONS = 2
_FETCH_CHARS = 4000


# ---------------------------------------------------------------------------
# LLM helpers (mirror the analysis-cloud pattern in api_features.web_research)
# ---------------------------------------------------------------------------
async def _complete(prompt: str, max_tokens: int = 800, timeout: float = 120.0) -> str:
    import litellm

    from ..core.settings import get_settings

    s = get_settings()
    model = getattr(s, "analysis_cloud_model", None) or "openai/deepseek-v4-flash"
    base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    key = os.getenv("OPENAI_API_KEY", "")
    resp = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        api_base=base,
        api_key=key,
        custom_llm_provider="openai",
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""


def _extract_json_array(text: str) -> list | None:
    """Robustly pull a JSON array out of an LLM response.

    Handles fenced code blocks, leading prose, and a trailing comma. Returns
    None if no parseable array exists (fail-closed: caller falls back)."""
    if not text:
        return None
    stripped = text.strip()
    # Prefer the largest balanced [ ... ] region.
    start = stripped.find("[")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(stripped)):
            c = stripped[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        try:
                            return json.loads(re.sub(r",\s*([\]}])", r"\1", candidate))
                        except json.JSONDecodeError:
                            return None
    return None


def _clean_sub_questions(items: list, goal: str) -> list[str]:
    """Normalize planner output into a list of non-empty sub-question strings."""
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            q = it.strip()
        elif isinstance(it, dict):
            q = str(
                it.get("question") or it.get("query") or it.get("sub_question") or ""
            ).strip()
        else:
            continue
        if q and q not in out:
            out.append(q)
    if not out:
        out = [goal]
    return out


def _compact_questions(questions: list[str], limit: int) -> list[str]:
    """Deduplicate + cap sub-questions so a greedy planner can't explode the
    fan-out (match the budget the caller asked for)."""
    seen: list[str] = []
    for q in questions:
        low = q.lower()
        if any(low == o.lower() or low in o.lower() or o.lower() in low for o in seen):
            continue
        seen.append(q)
        if len(seen) >= limit:
            break
    return seen or questions[:1]


# ---------------------------------------------------------------------------
# Research stages
# ---------------------------------------------------------------------------
async def _search_and_fetch(query: str, max_results: int) -> list[dict]:
    """One research unit's evidence: search then deep-read the top results."""
    from ..lib.mcp.web_search import web_fetch_handler, web_search_handler

    sources: list[dict] = []
    try:
        async with asyncio.timeout(30.0):
            search = await web_search_handler(
                {"query": query, "max_results": max_results}
            )
        results = search.get("results", [])[:max_results] if search.get("ok") else []
    except Exception as exc:
        log.warning("sub-search failed for %r: %s", query, exc)
        results = []
    for i, r in enumerate(results[:max_results]):
        url = r.get("url", "")
        text = r.get("snippet", "")
        if url:
            try:
                async with asyncio.timeout(30.0):
                    fetched = await web_fetch_handler(
                        {"url": url, "max_chars": _FETCH_CHARS}
                    )
                if fetched.get("ok"):
                    text = fetched.get("text") or fetched.get("content") or text
            except Exception as exc:
                log.warning("sub-fetch failed for %s: %s", url, exc)
        if url:
            sources.append(
                {
                    "n": i + 1,
                    "title": r.get("title", ""),
                    "url": url,
                    "text": (text or "")[:_FETCH_CHARS],
                }
            )
    return sources


async def _run_sub_unit(question: str, max_results: int, max_tokens: int) -> dict:
    """One isolated sub-researcher: search -> fetch -> cited sub-synthesis.

    The sub-synthesis is conditioned strictly on THIS unit's sources so context
    never leaks across units."""
    sources = await _search_and_fetch(question, max_results)
    if not sources:
        return {
            "question": question,
            "answer": "",
            "citations": [],
            "degraded": True,
            "note": "no sources found",
        }
    block = "\n\n".join(
        f"[{s['n']}] {s['title']} — {s['url']}\n{s['text']}"
        for s in sources
        if s.get("text")
    )
    prompt = (
        "You are a focused research analyst. Answer the sub-question using ONLY the sources below. "
        "Cite each claim with its source number in brackets, e.g. [1]. Be precise and concise. "
        "If the sources don't contain the answer, say so explicitly and note what is missing.\n\n"
        f"SUB-QUESTION: {question}\n\nSOURCES:\n{block}"
    )
    answer = ""
    try:
        answer = await _complete(prompt, max_tokens=max_tokens)
    except Exception as exc:
        log.warning("sub-synthesis failed for %r: %s", question, exc)
    return {
        "question": question,
        "answer": answer,
        "citations": [{k: v for k, v in s.items() if k != "text"} for s in sources],
        "degraded": not answer,
        "note": "" if answer else "synthesis failed; sources returned raw",
    }


async def _plan_sub_questions(goal: str, count: int) -> list[str]:
    """Planner: decompose the goal into `count` independent sub-questions."""
    prompt = (
        "You are a research director. Decompose the research goal into "
        f"{count} independent, answerable sub-questions that together fully cover it. "
        "Each must be self-contained (a separate researcher will work on it alone). "
        'Return ONLY a JSON array of strings, e.g. ["sub-question 1", "sub-question 2"].\n\n'
        f"GOAL: {goal}"
    )
    try:
        text = await _complete(prompt, max_tokens=1200)
        arr = _extract_json_array(text)
        if arr is None:
            return [goal]
        questions = _clean_sub_questions(arr, goal)
        return _compact_questions(questions, count)
    except Exception as exc:
        log.warning("planning failed for %r: %s", goal, exc)
        return [goal]


async def _evaluate_gaps(
    goal: str, sub_reports: list[dict], follow_up_budget: int
) -> list[str]:
    """Gap evaluator: given the partial findings, what is still missing?"""
    digest = "\n\n".join(
        f"SUB-QUESTION: {r['question']}\nANSWER: {r['answer'][:1500]}"
        for r in sub_reports
    )
    prompt = (
        "You are reviewing a partially-completed research task. Based on the findings below, "
        "identify the most important GAPS — facts the goal needs but the findings do not yet "
        f"cover. Return up to {follow_up_budget} follow-up questions as a JSON array of strings "
        '("[]" if nothing critical is missing). Each must be self-contained.\n\n'
        f"GOAL: {goal}\n\nFINDINGS SO FAR:\n{digest}"
    )
    try:
        text = await _complete(prompt, max_tokens=800)
        arr = _extract_json_array(text)
        if not arr:
            return []
        questions = _clean_sub_questions(arr, goal)
        return _compact_questions(questions, follow_up_budget)
    except Exception as exc:
        log.warning("gap evaluation failed for %r: %s", goal, exc)
        return []


async def _final_synthesis(
    goal: str, sub_reports: list[dict]
) -> tuple[str, list[dict]]:
    """Merge all sub-reports into one cited final answer, renumbering citations
    across units so [1..K] references a single source list."""
    # Renumber sources across all units into one flat list.
    flat: list[dict] = []
    renumber: list[dict] = []
    for r in sub_reports:
        mapping: dict[int, int] = {}
        for c in r.get("citations", []):
            src_n = c.get("n")
            if src_n is None:
                continue
            flat.append({k: v for k, v in c.items() if k != "n"})
            mapping[src_n] = len(flat)
        renumber.append({"question": r["question"], "mapping": mapping})

    digest = []
    for r, remap in zip(sub_reports, renumber):
        # Rewrite [N] tokens to the global numbers.
        answer = r["answer"]
        for local, global_n in remap["mapping"].items():
            answer = re.sub(rf"\[{local}\]", f"[{global_n}]", answer)
        digest.append(f"SUB-QUESTION: {r['question']}\nANSWER: {answer}")

    block = "\n\n".join(
        f"[{i + 1}] {c.get('title', '')} — {c.get('url', '')}"
        for i, c in enumerate(flat)
    )
    prompt = (
        "You are writing the final research report. Combine the sub-research findings below "
        "into ONE coherent answer to the goal. Cite each claim with its source number in "
        "brackets, e.g. [1], referencing the source list at the end. Flag any conflicts "
        "between findings. Be thorough and precise.\n\n"
        f"GOAL: {goal}\n\nFINDINGS:\n{'\n\n'.join(digest)}\n\nSOURCES:\n{block}"
    )
    answer = ""
    try:
        answer = await _complete(prompt, max_tokens=2000, timeout=180.0)
    except Exception as exc:
        log.warning("final synthesis failed for %r: %s", goal, exc)
    return answer, flat


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
async def deep_research(
    goal: str,
    max_sub_questions: int = _DEFAULT_SUB_QUESTIONS,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    max_results_per_unit: int = 5,
    follow_up_budget: int = 3,
) -> dict[str, Any]:
    """Run fan-out + iterative deep research on a goal.

    Returns:
      {status, goal, iterations, sub_questions, sub_reports, answer,
       citations, degraded}
    """
    goal = goal.strip()
    if not goal:
        return {"status": "error", "error": "goal is required"}
    try:
        max_sub_questions = max(1, min(int(max_sub_questions), 20))
        max_iterations = max(0, min(int(max_iterations), 3))
    except TypeError, ValueError:
        return {"status": "error", "error": "invalid research parameters"}

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _bounded_unit(q: str) -> dict:
        async with sem:
            return await _run_sub_unit(q, max_results_per_unit, max_tokens=900)

    sub_reports: list[dict] = []
    all_questions: list[str] = []

    iteration_plan = await _plan_sub_questions(goal, max_sub_questions)
    all_questions.extend(iteration_plan)

    for iteration in range(max_iterations + 1):
        if iteration > 0:
            gaps = await _evaluate_gaps(goal, sub_reports, follow_up_budget)
            if not gaps:
                break
            all_questions.extend(gaps)
            iteration_plan = gaps
        reports = await asyncio.gather(*(_bounded_unit(q) for q in iteration_plan))
        sub_reports.extend(reports)
        if iteration >= max_iterations:
            break

    answer, flat_citations = await _final_synthesis(goal, sub_reports)
    if not answer:
        return {
            "status": "degraded",
            "goal": goal,
            "iterations": max_iterations + 1,
            "sub_questions": all_questions,
            "sub_reports": sub_reports,
            "answer": "",
            "citations": flat_citations,
            "degraded": True,
            "error": "synthesis failed; sub-reports returned raw",
        }
    return {
        "status": "ok",
        "goal": goal,
        "iterations": max_iterations + 1,
        "sub_questions": all_questions,
        "sub_reports": sub_reports,
        "answer": answer,
        "citations": flat_citations,
        "degraded": any(r.get("degraded") for r in sub_reports),
    }
