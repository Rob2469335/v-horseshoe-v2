from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from swarm_os.services.orchestrator import Orchestrator


app = FastAPI(title="Organism Console", version="0.1.0")
orchestrator = Orchestrator()


class TaskRequest(BaseModel):
    task: str
    context: dict[str, Any] | None = None


class GenerateRequest(BaseModel):
    prompt: str
    model: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "ollama_reachable": orchestrator.ollama.is_reachable(),
        "models": orchestrator.ollama.list_models(),
    }


@app.get("/traces")
def traces(limit: int = 50) -> dict[str, Any]:
    return {
        "count": max(0, limit),
        "items": orchestrator.get_recent_traces(limit=limit),
    }


@app.post("/traces/clear")
def clear_traces() -> dict[str, Any]:
    orchestrator.clear_traces()
    return {"status": "cleared"}


@app.post("/plan")
def plan(req: TaskRequest) -> dict[str, Any]:
    return {
        "task": req.task,
        "plan": orchestrator.plan_task(task=req.task, context=req.context),
    }


@app.post("/route")
def route(req: TaskRequest) -> dict[str, Any]:
    return orchestrator.route_task(task=req.task, context=req.context)


@app.post("/generate")
def generate(req: GenerateRequest) -> dict[str, Any]:
    response, chosen_model = orchestrator.generate(model=req.model, prompt=req.prompt)
    return {
        "model": chosen_model,
        "response": response,
    }
@app.get("/traces/summary")
def traces_summary(limit: int = 50) -> list[dict]:
    from collections import defaultdict
    events = orchestrator.trace.events()
    sliced = events[-limit:]
    grouped: dict[str, list] = defaultdict(list)
    for e in sliced:
        grouped[e.trace_id].append(e)

    out: list[dict] = []
    for tid, evts in grouped.items():
        evts.sort(key=lambda e: getattr(e, "timestamp_ms", 0))
        first = evts[0]
        last = evts[-1]
        total_ms = (getattr(last, "duration_ms", 0) or 0) + (getattr(first, "duration_ms", 0) or 0)
        for e in evts[1:]:
            total_ms += getattr(e, "duration_ms", 0) or 0
        out.append({
            "trace_id": tid,
            "first_phase": first.phase,
            "last_status": last.status,
            "total_duration_ms": total_ms,
            "summary": last.summary or first.summary or "",
            "action_count": len(evts),
        })
    out.sort(key=lambda x: x["trace_id"])
    return out

