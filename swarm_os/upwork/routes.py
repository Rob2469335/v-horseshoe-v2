from fastapi import APIRouter
from pydantic import BaseModel
from swarm_os.upwork.engine import run_upwork_task

router = APIRouter(prefix="/upwork", tags=["upwork"])

class TaskRequest(BaseModel):
    input: str

@router.post("/propose")
async def propose(req: TaskRequest):
    return await run_upwork_task("propose", req.input)

@router.post("/rate")
async def rate(req: TaskRequest):
    return await run_upwork_task("rate", req.input)

@router.post("/pitch")
async def pitch(req: TaskRequest):
    return await run_upwork_task("pitch", req.input)

@router.post("/scope")
async def scope(req: TaskRequest):
    return await run_upwork_task("scope", req.input)

@router.post("/invoice")
async def invoice(req: TaskRequest):
    return await run_upwork_task("invoice", req.input)

@router.post("/skills-gap")
async def skills_gap(req: TaskRequest):
    return await run_upwork_task("skills_gap", req.input)
