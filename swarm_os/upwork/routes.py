from fastapi import APIRouter
from pydantic import BaseModel
from swarm_os.upwork.engine import run_upwork_task

router = APIRouter(prefix="/upwork", tags=["upwork"])

class TaskRequest(BaseModel):
    input: str

@router.post("/propose")
def propose(req: TaskRequest):
    return run_upwork_task("propose", req.input)

@router.post("/rate")
def rate(req: TaskRequest):
    return run_upwork_task("rate", req.input)

@router.post("/pitch")
def pitch(req: TaskRequest):
    return run_upwork_task("pitch", req.input)

@router.post("/scope")
def scope(req: TaskRequest):
    return run_upwork_task("scope", req.input)

@router.post("/invoice")
def invoice(req: TaskRequest):
    return run_upwork_task("invoice", req.input)

@router.post("/skills-gap")
def skills_gap(req: TaskRequest):
    return run_upwork_task("skills_gap", req.input)
