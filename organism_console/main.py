from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="v-horseshoe-v2 Backend")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
