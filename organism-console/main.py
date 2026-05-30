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
