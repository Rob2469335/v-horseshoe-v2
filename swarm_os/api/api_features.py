# swarm_os/api/api_features.py
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/features", tags=["features"])

class QueryRequest(BaseModel):
    query: str
    collection: str = "chat_archive"
    top_k: int = 5

@router.post("/search")
async def semantic_search(req: QueryRequest):
    """Query Qdrant via the memory pipeline and return reranked results."""
    try:
        from ..lib.vector.qdrant_store import search
        from ..lib.vector.reranker import rerank
        from ..core.settings import get_settings

        s = get_settings()
        top_k_qdrant = getattr(s, "qdrant_retrieve_top_k", 20)
        reranker_on = getattr(s, "reranker_enabled", True)

        candidates = await search(req.collection, req.query, top_k=top_k_qdrant)
        if reranker_on and candidates:
            results = await rerank(req.query, candidates, top_k=req.top_k)
        else:
            results = candidates[:req.top_k]
        return {"results": results}
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Vector search not yet configured. lib/vector modules are empty stubs."
        )

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
    except Exception as e:
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
        except Exception as e:
            log.exception("stream_generate failed in chat-search")
            yield f"data: {json.dumps({'content': f'[Error: {e}]'})}\n\n"
            
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
    except Exception as e:
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
        except Exception as e:
            log.exception("stream_generate failed in upwork")
            yield f"data: {json.dumps({'content': f'[Error: {e}]'})}\n\n"
            
    return StreamingResponse(sse_generator(), media_type="text/event-stream")

@router.get("/vscode")
async def vscode_status():
    return {"status": "stub", "message": "vscode_automation handler not yet implemented"}

@router.post("/vscode")
async def vscode_stream(req: QueryRequest, request: Request):
    return {"status": "stub", "message": "POST /vscode not implemented yet"}

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
def create_approval_request(request: Request, component: str, action: str, reason: str):
    appr = get_approvals_service(request)
    req = appr.create_request(component=component, action=action, reason=reason)
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
def approve_request(request: Request, request_id: str, body: dict = None):
    appr = get_approvals_service(request)
    note = (body or {}).get("note", "approved")
    req = appr.decide(request_id=request_id, approved=True, note=note)
    return {
        "status": "ok",
        "request": req
    }

@router.post("/healing-approvals/{request_id}/reject")
def reject_request(request: Request, request_id: str, body: dict = None):
    appr = get_approvals_service(request)
    note = (body or {}).get("note", "rejected")
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
    from pathlib import Path
    import json
    pending_root = Path(__file__).parent.parent.parent / ".data" / "pending_mutations"
    if not pending_root.exists():
        return {"status": "ok", "mutations": []}
    mutations = []
    for meta_file in pending_root.glob("*/metadata.json"):
        try:
            mutations.append(json.loads(meta_file.read_text(encoding="utf-8")))
        except Exception:
            continue
    return {"status": "ok", "mutations": mutations}

@router.post("/mutation-approvals/{mutation_id}/approve")
def approve_mutation(mutation_id: str):
    from pathlib import Path
    from swarm_os.services.genetic_mutation_loop import approve_pending_mutation
    pending_root = Path(__file__).parent.parent.parent / ".data" / "pending_mutations"
    meta_path = pending_root / mutation_id / "metadata.json"
    if not meta_path.exists():
        return {"status": "error", "error": f"Mutation {mutation_id} not found"}
    result = approve_pending_mutation(str(meta_path))
    return {"status": "ok", "result": result}

@router.post("/mutation-approvals/{mutation_id}/reject")
def reject_mutation(mutation_id: str):
    from pathlib import Path
    import shutil
    pending_root = Path(__file__).parent.parent.parent / ".data" / "pending_mutations"
    target_dir = pending_root / mutation_id
    if not target_dir.exists():
        return {"status": "error", "error": f"Mutation {mutation_id} not found"}
    shutil.rmtree(target_dir)
    return {"status": "ok", "rejected": mutation_id}

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
