# swarm_os/api/api_features.py
from __future__ import annotations

import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ..capabilities.models import VSCodeAutomationRequest
from .schemas import CreateApprovalRequest, ApprovalDecisionRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/features", tags=["features"])


def _extract_json_object(text: str) -> dict | None:
    """Robustly pull a single JSON object out of an LLM response.

    Handles fenced code blocks, leading prose, and nested braces. Returns None
    if no parseable object exists (fail-closed: the caller degrades rather than
    presenting an unverified answer).
    """
    import json as _json

    if not text:
        return None
    stripped = text.strip()
    # Prefer the largest balanced { ... } region.
    start = stripped.find("{")
    if start == -1:
        return None
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
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : i + 1]
                try:
                    parsed = _json.loads(candidate)
                except (ValueError, RecursionError):
                    # Try stripping a trailing comma before the closing brace.
                    try:
                        candidate2 = candidate[: candidate.rfind("}")].rstrip()
                        if candidate2.endswith(","):
                            candidate2 = candidate2[:-1] + "}"
                        parsed = _json.loads(candidate2)
                    except (ValueError, RecursionError):
                        return None
                return parsed if isinstance(parsed, dict) else None
    return None


async def _llm_complete(prompt: str, max_tokens: int = 800) -> str:
    """Analysis-cloud completion (deepseek-v4-flash by default). Never raises —
    an LLM outage degrades to an empty answer, never a fabricated one."""
    try:
        import litellm
        from ..core.settings import get_settings
        import os

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
            timeout=120,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("web-research synthesis failed: %s", exc)
        return ""


async def _llm_verdict(prompt: str, max_tokens: int = 900) -> dict | None:
    """Ask the model for the structured verdict JSON. Returns the parsed dict, or
    None when the model failed / returned unparseable output (fail-closed: the
    caller then degrades to the plain synthesis path — never fabricates)."""
    text = await _llm_complete(prompt, max_tokens=max_tokens)
    verdict = _extract_json_object(text)
    if verdict is None:
        log.warning("web-research verdict: model output not parseable as JSON")
    return verdict


class QueryRequest(BaseModel):
    query: str
    collection: str = "chat_archive"
    top_k: int = Field(default=5, le=50)


class WebResearchRequest(BaseModel):
    query: str
    max_results: int = 6
    deep_read: int = Field(default=3, le=10)
    synthesize: bool = True
    verdict: bool = True


class DeepResearchRequest(BaseModel):
    goal: str
    max_sub_questions: int = 5
    max_iterations: int = 2
    max_results_per_unit: int = 5
    follow_up_budget: int = 3


class NewsSubscriptionAdd(BaseModel):
    topic: str
    url: str


class NewsSubscriptionRemove(BaseModel):
    topic: str
    url: str | None = None


class NewsDigestRequest(BaseModel):
    topic: str | None = None
    max_items: int = 30


class IntelAddCompetitor(BaseModel):
    name: str
    url: str
    tier: str = "tier_2"
    targets: list[str] | None = Field(default=None, max_length=50)


class IntelUpdateCompetitor(BaseModel):
    name: str | None = None
    url: str | None = None
    tier: str | None = None
    targets: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None


class IntelRunRequest(BaseModel):
    channels: list[str] | None = Field(default=None, max_length=100)
    email_to: str | None = None
    webhook_url: str | None = None
    include: list[str] | None = Field(default=None, max_length=100)
    cap: int = 15


class IntelDeliverRequest(BaseModel):
    digest_id: str
    channels: list[str] | None = Field(default=None, max_length=100)
    email_to: str | None = None
    webhook_url: str | None = None


class BrowserTaskRequest(BaseModel):
    goal: str
    max_steps: int = 12
    confirm: bool = False


@router.post("/search")
async def semantic_search(req: QueryRequest):
    """Query Qdrant via the memory pipeline and return reranked results.

    Response shape: {"status": "ok"|"degraded", "fallback": bool, "results": [...]}.
    - status "ok": dense-vector search succeeded (embedding + Qdrant healthy).
    - status "degraded": vector search returned nothing (embedding/Qdrant blip) —
      the endpoint falls back to a lexical keyword scan over the collection's
      payloads so the caller still gets *some* results instead of an empty miss.
    - 503 only if the vector modules themselves are missing (should not happen).
    """
    try:
        from ..lib.vector.qdrant_store import search
        from ..lib.vector.reranker import rerank
        from ..core.settings import get_settings

        s = get_settings()
        top_k_qdrant = getattr(s, "qdrant_retrieve_top_k", 20)
        reranker_on = getattr(s, "reranker_enabled", True)

        candidates = await search(req.collection, req.query, top_k=top_k_qdrant)
        if candidates:
            if reranker_on:
                results = await rerank(req.query, candidates, top_k=req.top_k)
            else:
                results = candidates[: req.top_k]
            return {"status": "ok", "fallback": False, "results": results}

        # Degraded path: vector search returned nothing (embedding service down or
        # empty collection). Fall back to a lexical keyword scan over payloads so
        # the caller still receives relevant content rather than an empty result.
        try:
            results = await _keyword_fallback(req)
            return {"status": "degraded", "fallback": True, "results": results}
        except Exception as exc:
            log.debug("Keyword fallback failed: %s", exc)
            return {"status": "degraded", "fallback": True, "results": []}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Vector search not yet configured. lib/vector modules are empty stubs.",
        )


@router.post("/web-research")
async def web_research(req: WebResearchRequest):
    """Perplexity-style web research: search the live web, deep-read the top
    results, then synthesize a cited answer via the model.

    Pipeline: web_search_handler (parallel fan-out across every configured engine
    — Tavily/Serper/Brave/Exa/SerpAPI/TinyFish run concurrently and merge by RRF
    consensus ranking, each result tagged with its provider(s)) ->
    web_fetch_handler (Crawl4AI deep-read) on the top `deep_read` results ->
    a synthesis prompt to the analysis-cloud model (deepseek-v4-flash by default)
    that MUST cite sources as [1][2]... against the fetched list.

    When `verdict=true` (default) the synthesis is a single JSON object carrying
    the cited `answer`, a fail-closed `sufficiency` judgment (SURE-RAG: an answer
    the sources can't ground is flagged `insufficient`, never silently asserted),
    and a `conflicts` list (EvidentialRAG: contradictions between sources are
    surfaced, not papered over). If the model output isn't parseable, the route
    fails closed to the deterministic plain synthesis — it never fabricates a
    verdict.

    Response: {"status": "ok"|"degraded", "results": [...], "answer": "...",
               "citations": [{n, title, url}], "verdict": {...}|absent}
    """
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    try:
        from ..lib.mcp.web_search import web_search_handler, web_fetch_handler

        # 1) Search.
        async with asyncio.timeout(30.0):
            search = await web_search_handler(
                {"query": query, "max_results": req.max_results}
            )
        if not search.get("ok"):
            return {
                "status": "degraded",
                "results": [],
                "answer": "",
                "citations": [],
                "error": search.get("error", "search failed"),
            }
        results = search.get("results", [])[: req.max_results]

        # 2) Deep-read the top N (best effort — a fetch failure keeps the snippet).
        sources = []
        for i, r in enumerate(results[: req.deep_read]):
            url = r.get("url", "")
            text = r.get("snippet", "")
            if url:
                try:
                    async with asyncio.timeout(30.0):
                        fetched = await web_fetch_handler(
                            {"url": url, "max_chars": 4000}
                        )
                    if fetched.get("ok"):
                        text = fetched.get("text") or fetched.get("content") or text
                except Exception as e:
                    log.warning("web_fetch_handler failed for %s: %s", url, e)
            sources.append(
                {
                    "n": i + 1,
                    "title": r.get("title", ""),
                    "url": url,
                    "text": text[:4000],
                }
            )

        if not req.synthesize:
            return {
                "status": "ok",
                "results": results,
                "answer": "",
                "citations": sources,
                "note": "synthesis disabled (synthesize=false)",
            }

        # 3) Synthesize a cited answer. When `verdict=true` the model also emits
        # an explicit sufficiency judgment (fail-closed per SURE-RAG: an answer
        # that can't be grounded is flagged, never silently asserted) and a
        # conflict list (per EvidentialRAG: contradictions between sources are
        # surfaced, not papered over).
        sources_block = "\n\n".join(
            f"[{s['n']}] {s['title']} — {s['url']}\n{s['text']}"
            for s in sources
            if s.get("text")
        )
        verdict: dict | None = None
        if req.verdict:
            verdict_prompt = (
                "You are a web researcher. Answer the user's question using ONLY the sources below. "
                "Cite each claim with the source number in brackets, e.g. [1]. Be precise and concise. "
                "If the sources don't contain the answer, say so and note what's missing.\n\n"
                "Respond with a single JSON object (no prose outside it) of this exact shape:\n"
                '{"answer": "your cited answer", '
                '"sufficiency": "sufficient" | "insufficient", '
                '"sufficiency_note": "one sentence on whether the sources are enough to answer confidently; if not, what is missing", '
                '"conflicts": [{"claim_a": "claim as stated in one source", '
                '"claim_b": "contradictory claim as stated in another", '
                '"sources": [1, 3]}]}\n'
                "`conflicts` must be [] when the sources agree.\n\n"
                f"QUESTION: {query}\n\nSOURCES:\n{sources_block}"
            )
            verdict = await _llm_verdict(verdict_prompt)
        if verdict is None:
            # Deterministic fallback: no verifiable synthesis — never fabricate.
            prompt = (
                "You are a web researcher. Answer the user's question using ONLY the sources below. "
                "Cite each claim with the source number in brackets, e.g. [1]. Be precise and concise. "
                "If the sources don't contain the answer, say so and note what's missing.\n\n"
                f"QUESTION: {query}\n\nSOURCES:\n{sources_block}"
            )
            answer = await _llm_complete(prompt)
        else:
            answer = verdict.get("answer", "")

        return {
            "status": "ok",
            "results": results,
            "answer": answer,
            "citations": [{k: v for k, v in s.items() if k != "text"} for s in sources]
            if sources
            else [],
            **({"verdict": verdict} if verdict else {}),
        }
    except Exception:
        raise HTTPException(status_code=503, detail="web research modules unavailable")
    except Exception as exc:
        log.warning("web-research failed: %s", exc)
        return {
            "status": "degraded",
            "results": [],
            "answer": "",
            "citations": [],
            "error": str(exc),
        }


@router.post("/deep-research")
async def deep_research(req: DeepResearchRequest):
    """Fan-out + iterative deep research (Manus Wide Research / Perplexity Deep
    Research pattern): the planner decomposes the goal into independent
    sub-questions; each runs its own isolated search -> fetch -> cited
    sub-synthesis in parallel; a gap evaluator issues follow-ups; a final
    synthesis merges everything into one cited report.

    Response: {"status": "ok"|"degraded", "goal", "iterations",
               "sub_questions": [...], "sub_reports": [{question, answer,
               citations}], "answer", "citations": [{n,title,url}], "degraded"}
    """
    from ..services.deep_research import deep_research as run_deep_research

    return await run_deep_research(
        goal=req.goal,
        max_sub_questions=req.max_sub_questions,
        max_iterations=req.max_iterations,
        max_results_per_unit=req.max_results_per_unit,
        follow_up_budget=req.follow_up_budget,
    )


@router.get("/news/subscriptions")
async def news_subscriptions():
    """List news topics -> feed URLs (and the allowed-feed-host guard)."""
    from ..services.news_digest import list_subscriptions

    return list_subscriptions()


@router.post("/news/subscriptions/add")
async def news_subscription_add(req: NewsSubscriptionAdd):
    """Subscribe a feed URL to a topic (http(s) + allowlisted host required)."""
    from ..services.news_digest import add_subscription

    return add_subscription(req.topic, req.url)


@router.post("/news/subscriptions/remove")
async def news_subscription_remove(req: NewsSubscriptionRemove):
    """Remove a topic (or one URL within it)."""
    from ..services.news_digest import remove_subscription

    return remove_subscription(req.topic, req.url)


@router.post("/news/ingest")
async def news_ingest(limit_per_feed: int = 10):
    """Fetch + parse every subscribed feed, persist NEW items, return the diff."""
    from ..services.news_digest import ingest_feeds

    return await ingest_feeds(limit_per_feed=limit_per_feed)


@router.post("/news/digest")
async def news_digest(req: NewsDigestRequest):
    """LLM digest of the recent items, grouped by topic, flagging evolving
    stories — the 'custom news digest / follow how they evolve' capability."""
    from ..services.news_digest import digest

    return await digest(topic=req.topic, max_items=req.max_items)


# ---------------------------------------------------------------------------
# Competitive Intelligence — the paid service (registry -> collect -> digest -> deliver)
# ---------------------------------------------------------------------------
@router.get("/intel/competitors")
async def intel_competitors():
    """List monitored competitors (registry)."""
    from ..services.competitive_intel import list_competitors

    return {"ok": True, "competitors": list_competitors()}


@router.post("/intel/competitors")
async def intel_add_competitor(req: IntelAddCompetitor):
    """Register a competitor to monitor."""
    from ..services.competitive_intel import add_competitor

    return add_competitor(req.name, req.url, req.tier, req.targets)


@router.patch("/intel/competitors/{competitor_id}")
async def intel_update_competitor(competitor_id: str, req: IntelUpdateCompetitor):
    """Update a competitor (name/url/tier/targets/enabled)."""
    from ..services.competitive_intel import update_competitor

    return update_competitor(
        competitor_id,
        name=req.name,
        url=req.url,
        tier=req.tier,
        targets=req.targets,
        enabled=req.enabled,
    )


@router.delete("/intel/competitors/{competitor_id}")
async def intel_remove_competitor(competitor_id: str):
    """Remove a competitor from monitoring."""
    from ..services.competitive_intel import remove_competitor

    return remove_competitor(competitor_id)


@router.post("/intel/run")
async def intel_run(req: IntelRunRequest):
    """Run a full monitor cycle: scan all competitors, generate a digest, deliver it."""
    from ..services.competitive_intel import run_intel

    include = set(req.include) if req.include else None
    return await run_intel(
        channels=req.channels,
        email_to=req.email_to,
        webhook_url=req.webhook_url,
        include=include,
        cap=req.cap,
    )


@router.post("/intel/scan")
async def intel_scan(req: IntelRunRequest):
    """Scan-only: fetch + diff all targets, persist change events. No digest/delivery."""
    from ..services.competitive_intel import scan_all

    include = set(req.include) if req.include else None
    return await scan_all(include=include)


@router.post("/intel/digest")
async def intel_digest(cap: int = 15):
    """Build the curated digest from stored changes (no scan, no delivery)."""
    from ..services.competitive_intel import generate_digest

    return await generate_digest(cap=cap)


@router.post("/intel/deliver")
async def intel_deliver(req: IntelDeliverRequest):
    """Deliver an existing digest to configured channels."""
    from ..services.competitive_intel import get_digest, deliver_digest

    digest = get_digest(req.digest_id)
    if not digest:
        return {"ok": False, "error": "digest not found"}
    return await deliver_digest(
        digest,
        channels=req.channels,
        email_to=req.email_to,
        webhook_url=req.webhook_url,
    )


@router.get("/intel/history")
async def intel_history(limit: int = 10):
    """Recent digests."""
    from ..services.competitive_intel import list_digests

    return {"ok": True, "digests": list_digests(limit=limit)}


@router.get("/intel/changes")
async def intel_changes(limit: int = 100):
    """Recent change events (raw, deduplicated on scan)."""
    from ..services.competitive_intel import list_changes

    return {"ok": True, "changes": list_changes(limit=limit)}


@router.get("/intel/change/{change_id}")
async def intel_change(change_id: str):
    """Inspect a single change event."""
    from ..services.competitive_intel import get_change

    c = get_change(change_id)
    if not c:
        return {"ok": False, "error": "change not found"}
    return {"ok": True, "change": c}


@router.get("/intel/deliveries")
async def intel_deliveries(limit: int = 50):
    """Recent delivery records (observable failures)."""
    from ..services.competitive_intel import _load_deliveries

    return {"ok": True, "deliveries": _load_deliveries(limit=limit)}


@router.post("/browser-task")
async def browser_task(req: BrowserTaskRequest):
    """Perplexity-style agentic browsing: drive the persistent browser toward a
    goal (fill a form, navigate, do a task). Returns per-step history; critical
    actions (submit/purchase/login) return approval_requested unless confirmed
    via /features/browser-task/confirm."""
    from ..services.browser_task import run_browser_task

    goal = req.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal is required")
    return await run_browser_task(goal, max_steps=req.max_steps, confirm=req.confirm)


async def _keyword_fallback(req: QueryRequest) -> list:
    """Lexical fallback: scroll the collection's payloads and keyword-match the
    query tokens. Qdrant itself is usually still up even when the embedding server
    is down, so this yields real memory content without any embedding call. If the
    requested collection is empty, falls back to swarm_memory (the general memory
    store) so a degraded search still returns content instead of an empty miss."""
    import re
    from qdrant_client import AsyncQdrantClient
    from ..core.settings import get_settings

    s = get_settings()
    qdrant_url = getattr(s, "qdrant_url", "http://127.0.0.1:6333")
    tokens = {t for t in re.split(r"\W+", req.query.lower()) if len(t) > 2}
    if not tokens:
        return []

    client = AsyncQdrantClient(url=qdrant_url)
    candidates = [req.collection, "swarm_memory"]
    seen_ids = set()
    results: list = []
    try:
        for collection in candidates:
            if len(results) >= req.top_k:
                break
            try:
                next_offset = None
                while True:
                    async with asyncio.timeout(30.0):
                        scroll = await client.scroll(
                            collection_name=collection,
                            offset=next_offset,
                            limit=200,
                            with_payload=True,
                        )
                    points = (
                        scroll[0]
                        if isinstance(scroll, tuple)
                        else getattr(scroll, "points", scroll)
                    )
                    scored = []
                    for p in points or []:
                        pid = str(getattr(p, "id", ""))
                        if pid and pid in seen_ids:
                            continue
                        payload = getattr(p, "payload", None) or {}
                        haystack = " ".join(str(v) for v in payload.values()).lower()
                        score = sum(1 for t in tokens if t in haystack)
                        if score:
                            seen_ids.add(pid)
                            scored.append(
                                (
                                    score,
                                    {
                                        "id": getattr(p, "id", None),
                                        "score": float(score),
                                        "payload": payload,
                                    },
                                )
                            )
                    scored.sort(key=lambda x: -x[0])
                    results.extend(
                        item for _, item in scored[: req.top_k - len(results)]
                    )
                    if len(results) >= req.top_k:
                        break
                    next_offset = (
                        scroll[1]
                        if isinstance(scroll, tuple)
                        else getattr(scroll, "next_page_offset", None)
                    )
                    if not next_offset:
                        break
            except Exception as exc:
                log.debug("Fallback collection missing or unreadable: %s", exc)
                continue  # collection missing / unreadable — try next
    except Exception as e:
        log.warning("Qdrant fallback search failed, continuing to local docs: %s", e)
        # Do not return early — fallback to local-file search below on any collection-level error
    finally:
        try:
            await client.close()
        except Exception:
            log.debug("Failed to close degraded-search Qdrant client", exc_info=True)

    # If Qdrant collections exist but contain no matching payloads, fall back
    # to scanning repository markdown files (AGENTS.md, README.md, docs/*.md)
    # so degraded search still returns useful context in test/dev environments
    # where embeddings or Qdrant points may be absent.
    if results:
        return results[: req.top_k]

    # Local-file fallback moved to helper to centralize safety and reuse.
    try:
        from ..api._fallbacks import local_docs_search
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        # if repo_root seems unexpected in test environments, fall back to cwd
        if not (repo_root / "AGENTS.md").exists():
            repo_root = Path.cwd()
        results = await asyncio.to_thread(
            local_docs_search, repo_root, tokens, req.top_k
        )
        if results:
            return results
    except Exception as e:
        log.warning("Local docs fallback search failed: %s", e)
        # If helper fails for any reason, fall through to return []

    return results[: req.top_k]


@router.get("/chat-search")
async def chat_search_status():
    return {"status": "available", "message": "chat_search handler is available"}


@router.post("/chat-search")
async def chat_search_stream(req: QueryRequest, request: Request):
    import json
    from fastapi.responses import StreamingResponse
    from .dependencies import get_orchestrator
    from ..capabilities.chat_search import ChatSearchHandler
    from ..capabilities.models import ChatSearchRequest

    try:
        handler = ChatSearchHandler()
        search_req = ChatSearchRequest(query=req.query, max_results=10)
        search_res = await handler.execute(search_req)
        results = search_res.results
    except Exception:
        log.exception("ChatSearchHandler execution failed")
        results = []

    context_lines = []
    for r in results:
        context_lines.append(f"[{r.timestamp}] {r.sender}: {r.message}")
    context = "\n".join(context_lines)

    prompt = (
        "You are Librarian, a specialized Swarm OS search agent. Your job is to answer the user's question "
        "using the retrieved event logs from the system database. "
        "If the logs don't contain the answer, use your own knowledge about Swarm OS, but prefer the logs if they are relevant.\n\n"
        f"Retrieved Event Logs:\n{context}\n\n"
        f"User Question: {req.query}\n\n"
        "Provide a helpful, precise, and concise explanation."
    )

    orch = get_orchestrator(request)

    async def sse_generator():
        try:
            async for chunk, _, _ in orch.stream_generate(model=None, prompt=prompt):
                if chunk:
                    clean_chunk = chunk.replace("Assistant: <tool>", "").strip()
                    if clean_chunk:
                        yield f"data: {json.dumps({'content': clean_chunk})}\n\n"
        except Exception:
            log.exception("stream_generate failed in chat-search")
            yield f"data: {json.dumps({'content': '[Error: stream generation failed]'})}\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@router.get("/upwork")
async def upwork_status():
    return {"status": "stub", "message": "upwork_analyzer handler not yet implemented"}


@router.post("/upwork")
async def upwork_stream(req: QueryRequest, request: Request):
    import json
    from fastapi.responses import StreamingResponse
    from .dependencies import get_orchestrator
    from ..capabilities.upwork_analyzer import UpworkAnalyzerHandler
    from ..capabilities.models import UpworkAnalysisRequest

    try:
        handler = UpworkAnalyzerHandler()
        analysis_req = UpworkAnalysisRequest(job_description=req.query)
        res = await handler.analyze_job(analysis_req)

        context = (
            f"Job Analysis Result:\n"
            f"- Primary Domain: {res.primary_domain}\n"
            f"- Match Score: {res.match_score}\n"
            f"- Fit Metrics: {res.fit_metrics}\n"
            f"- Should Bid: {res.should_bid}\n"
        )
        if res.recommended_bid:
            context += f"- Recommended Bid Rate: {res.recommended_bid.projected_rate}\n"
    except Exception:
        log.exception("UpworkAnalyzerHandler execution failed")
        context = "No pre-computed analysis available."

    prompt = (
        "You are Scout, a specialized Swarm OS Upwork bidding agent. "
        "Your task is to analyze the job description, review the computed match metrics, "
        "and draft a compelling bid pitch and strategy for the job posting.\n\n"
        f"Job Description: {req.query}\n\n"
        f"Computed Analysis Metrics:\n{context}\n\n"
        "Draft a professional bid proposal strategy, recommending why we should (or should not) bid, "
        "the reasoning behind the bid rate, and 2-3 cover letter bullet highlights."
    )

    orch = get_orchestrator(request)

    async def sse_generator():
        try:
            async for chunk, _, _ in orch.stream_generate(model=None, prompt=prompt):
                if chunk:
                    clean_chunk = chunk.replace("Assistant: <tool>", "").strip()
                    if clean_chunk:
                        yield f"data: {json.dumps({'content': clean_chunk})}\n\n"
        except Exception:
            log.exception("stream_generate failed in upwork")
            yield f"data: {json.dumps({'content': '[Error: stream generation failed]'})}\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@router.get("/vscode")
async def vscode_status():
    """Describe the workspace-safe VS Code automation capability."""
    return {
        "status": "available",
        "commands": ["cat", "find_symbol", "grep", "lint", "list_files", "ls", "scout"],
    }


@router.post("/vscode")
async def vscode_execute(req: VSCodeAutomationRequest):
    """Execute an allowlisted workspace operation through the capability handler."""
    from ..capabilities.vscode_automation import VSCodeAutomationHandler

    return await VSCodeAutomationHandler().execute(req)


# ---------------------------------------------------------------------------
# Module-level variables (can be monkeypatched by tests)
# ---------------------------------------------------------------------------
approvals = None
metrics = None
audit = None
policy = None
healing = None
approval_execution = None
learning = None
research = None


def get_approvals_service(request: Request = None):
    global approvals
    if approvals is not None:
        return approvals
    if request:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime and getattr(runtime, "approval_queue", None) is not None:
            return runtime.approval_queue
        if getattr(request.app.state, "approval_queue", None) is not None:
            return request.app.state.approval_queue
    from swarm_os.adaptation.approval.approval_queue import ApprovalQueue

    approvals = ApprovalQueue()
    return approvals


def get_metrics_service(request: Request = None):
    global metrics
    if metrics is not None:
        return metrics
    if request:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime and getattr(runtime, "metrics", None) is not None:
            return runtime.metrics
        if getattr(request.app.state, "healing_metrics", None) is not None:
            return request.app.state.healing_metrics
    from swarm_os.adaptation.observability.healing_metrics import HealingMetrics

    metrics = HealingMetrics()
    return metrics


def get_audit_service(request: Request = None):
    global audit
    if audit is not None:
        return audit
    if request:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime and getattr(runtime, "audit", None) is not None:
            return runtime.audit
        if getattr(request.app.state, "healing_audit", None) is not None:
            return request.app.state.healing_audit
    from swarm_os.adaptation.observability.healing_audit import HealingAudit

    audit = HealingAudit()
    return audit


def get_escalation_service(request: Request = None):
    if request:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime and getattr(runtime, "escalation", None) is not None:
            return runtime.escalation
        if getattr(request.app.state, "escalation_service", None) is not None:
            return request.app.state.escalation_service
    from swarm_os.adaptation.escalation.escalation_service import EscalationService

    return EscalationService()


def get_approval_execution_service(request: Request = None):
    global approval_execution
    if approval_execution is not None:
        return approval_execution
    if request:
        if getattr(request.app.state, "approval_execution_service", None) is not None:
            return request.app.state.approval_execution_service
    from swarm_os.adaptation.approval.approval_execution import ApprovalExecutionService

    queue = get_approvals_service(request)
    executor = None
    if request:
        runtime = getattr(request.app.state, "runtime", None)
        executor = getattr(runtime, "executor", None)
    approval_execution = ApprovalExecutionService(queue=queue, executor=executor)
    return approval_execution


def get_learning_service(request: Request = None):
    global learning
    if learning is not None:
        return learning
    if request:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime and getattr(runtime, "learning", None) is not None:
            return runtime.learning
        if getattr(request.app.state, "learning", None) is not None:
            return request.app.state.learning
    from swarm_os.app.services.learning_service import LearningService

    return LearningService()


def get_policy_engine(request: Request = None):
    global policy
    if policy is not None:
        return policy
    if request:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime and getattr(runtime, "policy_engine", None) is not None:
            return runtime.policy_engine
        if getattr(request.app.state, "policy_engine", None) is not None:
            return request.app.state.policy_engine
    from swarm_os.adaptation.policy.policy_engine import RemediationPolicyEngine

    return RemediationPolicyEngine()


# ---------------------------------------------------------------------------
# Healing Observability & Approvals Router Endpoints
# ---------------------------------------------------------------------------
@router.get("/healing-status")
def get_healing_status(request: Request):
    met = get_metrics_service(request)
    aud = get_audit_service(request)
    esc_service = get_escalation_service(request)

    return {
        "status": "ok",
        "metrics": met.snapshot(),
        "recent_audit": aud.recent(),
        "recent_escalations": esc_service.recent(),
    }


@router.get("/healing-overview")
def get_healing_overview(request: Request):
    met = get_metrics_service(request)
    aud = get_audit_service(request)
    appr = get_approvals_service(request)
    esc_service = get_escalation_service(request)

    policy_eng = get_policy_engine(request)

    all_reqs = appr.list_requests()
    executed_reqs = [
        r
        for r in all_reqs
        if r.get("status") == "executed" or r.get("executed_at") is not None
    ]

    return {
        "status": "ok",
        "overview_status": "healthy",
        "summary": {
            "status": "ok",
            "recent_failures": len(
                [
                    a
                    for a in aud.recent()
                    if not a.get("verification", {}).get("verified", True)
                ]
            ),
            "active_incidents": len(esc_service.recent()),
        },
        "readiness": 100,
        "actions": aud.recent(),
        "approvals": {
            "counts": {
                "total": len(all_reqs),
                "executed": len(executed_reqs),
                "pending": len([r for r in all_reqs if r.get("status") == "pending"]),
            },
            "requests": all_reqs,
        },
        "metrics": met.snapshot(),
        "audit": aud.recent(),
        "escalations": esc_service.recent(),
        "components": {},
        "runbooks": [],
        "policy": {
            "rules_count": len(policy_eng.list_policies())
            if hasattr(policy_eng, "list_policies")
            else 0,
            "rules": policy_eng.list_policies()
            if hasattr(policy_eng, "list_policies")
            else {},
        },
    }


@router.post("/healing-approvals")
def create_approval_request(request: Request, payload: CreateApprovalRequest):
    appr = get_approvals_service(request)
    req = appr.create_request(
        component=payload.component, action=payload.action, reason=payload.reason
    )
    return {"status": "ok", "request": req}


@router.get("/healing-approvals")
def list_approval_requests(request: Request):
    appr = get_approvals_service(request)
    return {"status": "ok", "requests": appr.list_requests()}


@router.post("/healing-approvals/{request_id}/approve")
def approve_request(
    request: Request, request_id: str, body: ApprovalDecisionRequest = None
):
    appr = get_approvals_service(request)
    note = (body.note if body else None) or "approved"
    req = appr.decide(request_id=request_id, approved=True, note=note)
    return {"status": "ok", "request": req}


@router.post("/healing-approvals/{request_id}/reject")
def reject_request(
    request: Request, request_id: str, body: ApprovalDecisionRequest = None
):
    appr = get_approvals_service(request)
    note = (body.note if body else None) or "rejected"
    req = appr.decide(request_id=request_id, approved=False, note=note)
    return {"status": "ok", "request": req}


@router.post("/healing-approvals/{request_id}/execute")
def execute_approved_request(request: Request, request_id: str):
    exec_service = get_approval_execution_service(request)
    res = exec_service.execute_approved(request_id)
    if res.get("status") == "ok" and not res.get("idempotent", False):
        req = res.get("request", {})
        component = req.get("component", "system")
        action = req.get("action", "restart_component")

        # Determine verified status using verifier
        verifier = None
        global healing
        if healing is not None:
            verifier = getattr(healing, "verifier", None)
        elif request:
            runtime = getattr(request.app.state, "runtime", None)
            if runtime and getattr(runtime, "healing", None) is not None:
                verifier = getattr(runtime.healing, "verifier", None)

        verified = True
        verification_detail = "verified"
        if verifier:
            try:
                verify_res = verifier.verify(component)
                if isinstance(verify_res, dict):
                    verified = verify_res.get("verified", True)
                    verification_detail = verify_res.get("detail", "ok")
                else:
                    verified = bool(verify_res)
            except Exception as e:
                verified = False
                verification_detail = str(e)

        # Record metrics
        met = get_metrics_service(request)
        if met:
            met.record(
                component=component,
                action=action,
                executed=True,
                verified=verified,
                escalated=False,
            )

        # Record audit
        aud = get_audit_service(request)
        if aud:
            from datetime import datetime, timezone

            aud.record(
                {
                    "component": component,
                    "action": action,
                    "executed": True,
                    "repair": {"status": "success", "detail": "executed"},
                    "verification": {
                        "verified": verified,
                        "detail": verification_detail,
                    },
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        # Record learning
        learn = get_learning_service(request)
        if learn:
            learn.record_repair(component=component, action=action, success=verified)
    return res


@router.get("/mutation-approvals")
def list_pending_mutations():
    from swarm_os.repositories.mutation_repo import MutationRepository

    repo = MutationRepository()
    mutations = repo.list_pending()
    return {"status": "ok", "mutations": mutations}


@router.post("/mutation-approvals/{mutation_id}/approve")
def approve_mutation(mutation_id: str):
    from swarm_os.repositories.mutation_repo import MutationRepository

    repo = MutationRepository()
    try:
        result = repo.approve(mutation_id)
        return {"status": "ok", "result": result}
    except Exception:
        log.exception("Mutation approve failed")
        return {"status": "error", "error": f"Failed to approve mutation {mutation_id}"}


@router.post("/mutation-approvals/{mutation_id}/reject")
def reject_mutation(mutation_id: str):
    from swarm_os.repositories.mutation_repo import MutationRepository

    repo = MutationRepository()
    if repo.reject(mutation_id):
        return {"status": "ok", "rejected": mutation_id}
    return {"status": "error", "error": f"Mutation {mutation_id} not found"}


@router.get("/healing-policy")
def get_healing_policy(request: Request):
    engine = get_policy_engine(request)
    return {"status": "ok", "policies": engine.list_policies()}


@router.get("/healing-policy/{component}")
def get_healing_policy_for_component(request: Request, component: str):
    engine = get_policy_engine(request)
    return {
        "status": "ok",
        "component": component,
        "policy": engine.get_policy(component),
    }


@router.get("/healing-policy-check")
def check_healing_policy(
    request: Request, component: str, action: str, attempt_count: int = 1
):
    engine = get_policy_engine(request)
    decision = engine.evaluate(
        component=component, action=action, attempt_count=attempt_count
    )
    return {"status": "ok", "decision": decision}


# ---------------------------------------------------------------------------
# Readiness, Drills, and Runbook Endpoints
# ---------------------------------------------------------------------------
def get_readiness_service(request: Request = None):
    from swarm_os.adaptation.readiness.readiness_service import HealingReadinessService
    from swarm_os.adaptation.runbooks.runbook_service import RunbookService

    met = get_metrics_service(request)
    aud = get_audit_service(request)
    esc = get_escalation_service(request)
    runbooks = RunbookService()
    return HealingReadinessService(
        metrics=met, audit=aud, escalation=esc, runbooks=runbooks
    )


def get_chaos_drill_service():
    from swarm_os.adaptation.drills.chaos_drill_service import ChaosDrillService

    return ChaosDrillService()


def get_runbook_service():
    from swarm_os.adaptation.runbooks.runbook_service import RunbookService

    return RunbookService()


def get_incident_summary_service(request: Request = None):
    from swarm_os.adaptation.incident.incident_summary import IncidentSummaryService

    met = get_metrics_service(request)
    aud = get_audit_service(request)
    esc = get_escalation_service(request)
    return IncidentSummaryService(metrics=met, audit=aud, escalation=esc)


@router.get("/healing-readiness")
def get_healing_readiness(request: Request):
    svc = get_readiness_service(request)
    res = svc.calculate()
    return res


@router.get("/healing-drills")
def get_healing_drills():
    svc = get_chaos_drill_service()
    res = svc.summary()
    return res


@router.get("/healing-runbook/{component}")
def get_healing_runbook(component: str):
    svc = get_runbook_service()
    res = svc.get_runbook(component)
    return {"status": "ok", **res}


@router.get("/healing-incidents")
def get_healing_incidents(request: Request):
    svc = get_incident_summary_service(request)
    res = svc.build_summary()
    return res


class DebateRequest(BaseModel):
    goal: str


@router.post("/debate")
async def swarm_debate(req: DebateRequest, request: Request):
    """Stream a Planner→Reviewer→Coordinator debate for a development goal.

    SSE stream with phases: status → proposal → critique → synthesis → done.
    Each phase content is generated by the corresponding agent via AgentServiceV2.
    """
    import asyncio, json
    from fastapi.responses import StreamingResponse

    async def event_stream():
        async def emit(phase: str, message: str, content: str = ""):
            yield f"data: {json.dumps({'phase': phase, 'message': message, 'content': content})}\n\n"

        _runtime = getattr(request.app.state, "runtime", None)
        agent_svc = getattr(_runtime, "agents", None) if _runtime is not None else None
        if agent_svc is None:
            yield f"data: {json.dumps({'phase': 'error', 'message': 'Agent service unavailable', 'content': ''})}\n\n"
            return

        async def run_agent(agent_id: str, task: str) -> str:
            out = []
            try:
                async for chunk in agent_svc.step_agent_stream(agent_id, task):
                    if chunk.get("type") == "final":
                        out.append(str(chunk.get("content", "")))
            except Exception as exc:
                out.append(f"[{agent_id} error: {exc}]")
            return " ".join(out) if out else f"[{agent_id} produced no output]"

        yield (
            "data: "
            + json.dumps(
                {
                    "phase": "status",
                    "message": f"Planner drafting proposal for: {req.goal}",
                    "content": "",
                }
            )
            + "\n\n"
        )
        proposal = await run_agent(
            "planner", f"Draft a detailed implementation proposal for: {req.goal}"
        )
        for part in proposal.split(" "):
            yield f"data: {json.dumps({'phase': 'proposal', 'message': 'streaming', 'content': part + ' '})}\n\n"
            await asyncio.sleep(0.01)

        yield (
            "data: "
            + json.dumps(
                {
                    "phase": "status",
                    "message": "Reviewer critiquing the proposal...",
                    "content": "",
                }
            )
            + "\n\n"
        )
        critique = await run_agent(
            "reviewer",
            f"Critique the following proposal strictly, identifying risks and weaknesses:\n\n{proposal}",
        )
        for part in critique.split(" "):
            yield f"data: {json.dumps({'phase': 'critique', 'message': 'streaming', 'content': part + ' '})}\n\n"
            await asyncio.sleep(0.01)

        yield (
            "data: "
            + json.dumps(
                {
                    "phase": "status",
                    "message": "Coordinator synthesizing final decision...",
                    "content": "",
                }
            )
            + "\n\n"
        )
        synthesis = await run_agent(
            "coordinator",
            f"Synthesize the proposal and critique into a final recommended approach:\n\nPROPOSAL:\n{proposal}\n\nCRITIQUE:\n{critique}",
        )
        for part in synthesis.split(" "):
            yield f"data: {json.dumps({'phase': 'synthesis', 'message': 'streaming', 'content': part + ' '})}\n\n"
            await asyncio.sleep(0.01)

        yield (
            "data: "
            + json.dumps(
                {"phase": "done", "message": "Debate complete.", "content": ""}
            )
            + "\n\n"
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


class OmniDevRequest(BaseModel):
    task: str
    organismId: str = "default"


@router.post("/omnidev/run")
async def omnidev_run(req: OmniDevRequest, request: Request):
    """Run an OmniDev task through the coordinator agent (delegates to the swarm).

    Returns the coordinator's final response after the agent loop completes.
    """

    _runtime = getattr(request.app.state, "runtime", None)
    agent_svc = getattr(_runtime, "agents", None) if _runtime is not None else None
    if agent_svc is None:
        raise HTTPException(status_code=503, detail="Agent service unavailable")

    final_content = ""
    try:
        async for chunk in agent_svc.step_agent_stream("coordinator", req.task):
            if chunk.get("type") == "final":
                final_content = str(chunk.get("content", ""))
        if not final_content:
            final_content = (
                "OmniDev completed the task without producing a final response."
            )
        return {"result": final_content}
    except Exception:
        log.exception("OmniDev task failed")
        raise HTTPException(status_code=500, detail="OmniDev task failed")


class RvFinderRequest(BaseModel):
    budget: int = 30000
    rv_type: str = "all"
    max_results: int = Field(default=40, le=100)
    deep_dive: int = Field(default=5, le=10)
    use_ppl: bool = True
    use_web: bool = True
    location: str = ""
    radius_miles: int = 0


@router.post("/rv-finder/search")
async def rv_finder_search(req: RvFinderRequest):
    """Find and analyze used RVs under a budget across PPL + web sources.

    Discovers real listings, fetches detail pages, builds a per-listing deal
    analysis (fair-value range, condition scan, Deal Score, verdict, negotiation
    tip), ranks them, and runs an optional LLM deep-dive on the top candidates.
    """
    from ..services.rv_finder import find_best_rv_deals

    try:
        result = await find_best_rv_deals(
            budget=req.budget,
            rv_type=req.rv_type,
            max_results=req.max_results,
            deep_dive=req.deep_dive,
            use_ppl=req.use_ppl,
            use_web=req.use_web,
            location=req.location,
            radius_miles=req.radius_miles,
        )
        return result
    except Exception:
        log.exception("RV finder search failed")
        raise HTTPException(status_code=500, detail="RV finder search failed")
