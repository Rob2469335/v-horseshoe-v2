# swarm_os/api/api_features.py
from __future__ import annotations

import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from ..capabilities.models import VSCodeAutomationRequest
from .schemas import CreateApprovalRequest, ApprovalDecisionRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/features", tags=["features"])

class QueryRequest(BaseModel):
    query: str
    collection: str = "chat_archive"
    top_k: int = 5

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
                results = candidates[:req.top_k]
            return {"status": "ok", "fallback": False, "results": results}

        # Degraded path: vector search returned nothing (embedding service down or
        # empty collection). Fall back to a lexical keyword scan over payloads so
        # the caller still receives relevant content rather than an empty result.
        try:
            results = await _keyword_fallback(req)
            return {"status": "degraded", "fallback": True, "results": results}
        except Exception:
            return {"status": "degraded", "fallback": True, "results": []}
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Vector search not yet configured. lib/vector modules are empty stubs."
        )


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
                scroll = await client.scroll(
                    collection_name=collection,
                    limit=200,
                    with_payload=True,
                )
            except Exception:
                continue  # collection missing / unreadable — try next
            points = scroll[0] if isinstance(scroll, tuple) else getattr(scroll, "points", scroll)
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
                    scored.append((score, {"id": getattr(p, "id", None), "score": float(score), "payload": payload}))
            scored.sort(key=lambda x: -x[0])
            results.extend(item for _, item in scored[: req.top_k - len(results)])
    except Exception:
        # Do not return early — fallback to local-file search below on any collection-level error
        pass
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
        results = await asyncio.to_thread(local_docs_search, repo_root, tokens, req.top_k)
        if results:
            return results
    except Exception:
        # If helper fails for any reason, fall through to return []
        pass

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
    executed_reqs = [r for r in all_reqs if r.get("status") == "executed" or r.get("executed_at") is not None]

    return {
        "status": "ok",
        "overview_status": "healthy",
        "summary": {
            "status": "ok",
            "recent_failures": len([a for a in aud.recent() if not a.get("verification", {}).get("verified", True)]),
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
            "requests": all_reqs
        },
        "metrics": met.snapshot(),
        "audit": aud.recent(),
        "escalations": esc_service.recent(),
        "components": {},
        "runbooks": [],
        "policy": {
            "rules_count": len(policy_eng.list_policies()) if hasattr(policy_eng, "list_policies") else 0,
            "rules": policy_eng.list_policies() if hasattr(policy_eng, "list_policies") else {}
        }
    }

@router.post("/healing-approvals")
def create_approval_request(request: Request, payload: CreateApprovalRequest):
    appr = get_approvals_service(request)
    req = appr.create_request(component=payload.component, action=payload.action, reason=payload.reason)
    return {
        "status": "ok",
        "request": req
    }

@router.get("/healing-approvals")
def list_approval_requests(request: Request):
    appr = get_approvals_service(request)
    return {
        "status": "ok",
        "requests": appr.list_requests()
    }

@router.post("/healing-approvals/{request_id}/approve")
def approve_request(request: Request, request_id: str, body: ApprovalDecisionRequest = None):
    appr = get_approvals_service(request)
    note = (body.note if body else None) or "approved"
    req = appr.decide(request_id=request_id, approved=True, note=note)
    return {
        "status": "ok",
        "request": req
    }

@router.post("/healing-approvals/{request_id}/reject")
def reject_request(request: Request, request_id: str, body: ApprovalDecisionRequest = None):
    appr = get_approvals_service(request)
    note = (body.note if body else None) or "rejected"
    req = appr.decide(request_id=request_id, approved=False, note=note)
    return {
        "status": "ok",
        "request": req
    }

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
                escalated=False
            )
            
        # Record audit
        aud = get_audit_service(request)
        if aud:
            from datetime import datetime, timezone
            aud.record({
                "component": component,
                "action": action,
                "executed": True,
                "repair": {"status": "success", "detail": "executed"},
                "verification": {"verified": verified, "detail": verification_detail},
                "escalated": False,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
        # Record learning
        learn = get_learning_service(request)
        if learn:
            learn.record_repair(
                component=component,
                action=action,
                success=verified
            )
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
    return {
        "status": "ok",
        "policies": engine.list_policies()
    }

@router.get("/healing-policy/{component}")
def get_healing_policy_for_component(request: Request, component: str):
    engine = get_policy_engine(request)
    return {
        "status": "ok",
        "component": component,
        "policy": engine.get_policy(component)
    }

@router.get("/healing-policy-check")
def check_healing_policy(
    request: Request,
    component: str,
    action: str,
    attempt_count: int = 1
):
    engine = get_policy_engine(request)
    decision = engine.evaluate(
        component=component,
        action=action,
        attempt_count=attempt_count
    )
    return {
        "status": "ok",
        "decision": decision
    }

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
    return HealingReadinessService(metrics=met, audit=aud, escalation=esc, runbooks=runbooks)

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
    return {
        "status": "ok",
        **res
    }

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

        agent_svc = getattr(request.app.state, "agent_service", None)
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

        yield "data: " + json.dumps({"phase": "status", "message": f"Planner drafting proposal for: {req.goal}", "content": ""}) + "\n\n"
        proposal = await run_agent("planner", f"Draft a detailed implementation proposal for: {req.goal}")
        for part in proposal.split(" "):
            yield f"data: {json.dumps({'phase': 'proposal', 'message': 'streaming', 'content': part + ' '})}\n\n"
            await asyncio.sleep(0.01)

        yield "data: " + json.dumps({"phase": "status", "message": "Reviewer critiquing the proposal...", "content": ""}) + "\n\n"
        critique = await run_agent("reviewer", f"Critique the following proposal strictly, identifying risks and weaknesses:\n\n{proposal}")
        for part in critique.split(" "):
            yield f"data: {json.dumps({'phase': 'critique', 'message': 'streaming', 'content': part + ' '})}\n\n"
            await asyncio.sleep(0.01)

        yield "data: " + json.dumps({"phase": "status", "message": "Coordinator synthesizing final decision...", "content": ""}) + "\n\n"
        synthesis = await run_agent("coordinator", f"Synthesize the proposal and critique into a final recommended approach:\n\nPROPOSAL:\n{proposal}\n\nCRITIQUE:\n{critique}")
        for part in synthesis.split(" "):
            yield f"data: {json.dumps({'phase': 'synthesis', 'message': 'streaming', 'content': part + ' '})}\n\n"
            await asyncio.sleep(0.01)

        yield "data: " + json.dumps({"phase": "done", "message": "Debate complete.", "content": ""}) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


class OmniDevRequest(BaseModel):
    task: str
    organismId: str = "default"


@router.post("/omnidev/run")
async def omnidev_run(req: OmniDevRequest, request: Request):
    """Run an OmniDev task through the coordinator agent (delegates to the swarm).

    Returns the coordinator's final response after the agent loop completes.
    """

    agent_svc = getattr(request.app.state, "agent_service", None)
    if agent_svc is None:
        raise HTTPException(status_code=503, detail="Agent service unavailable")

    final_content = ""
    try:
        async for chunk in agent_svc.step_agent_stream("coordinator", req.task):
            if chunk.get("type") == "final":
                final_content = str(chunk.get("content", ""))
        if not final_content:
            final_content = "OmniDev completed the task without producing a final response."
        return {"result": final_content}
    except Exception:
        log.exception("OmniDev task failed")
        raise HTTPException(status_code=500, detail="OmniDev task failed")


class RvFinderRequest(BaseModel):
    budget: int = 30000
    rv_type: str = "all"
    max_results: int = 40
    deep_dive: int = 5
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
