"""
Module: search
Order: 27
Package: app.api.routes
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.post("/search")
def search() -> dict[str, str]:
    return {"ok": "true", "route": "search"}