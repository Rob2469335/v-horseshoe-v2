"""
Module: main
Order: 25
Package: app.api
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from fastapi import FastAPI

from swarm_os.app.api.routes import admin, chat, health, search


def create_app() -> FastAPI:
    app = FastAPI(title="Swarm OS")
    app.include_router(health.router, tags=["health"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(search.router, prefix="/api", tags=["search"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    return app


app = create_app()