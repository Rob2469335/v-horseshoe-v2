# api/routes/health.py
from fastapi import APIRouter
from config.settings import APP_TITLE, APP_VERSION, SWARM_BASE_URL, OLLAMA_BASE_URL

router = APIRouter()

@router.get("/health")
async def health():
    return {
        "status": "ok",
        "app": APP_TITLE,
        "version": APP_VERSION,
        "swarm": SWARM_BASE_URL,
        "ollama": OLLAMA_BASE_URL,
    }
