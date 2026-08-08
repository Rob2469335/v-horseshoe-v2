"""Legal assistant routes for Rob's Lawyer.

Endpoints:
- POST /legal/verify-citations — parse a text blob with Eyecite and verify each
  case citation against CourtListener's Citation Lookup & Verification API.
- GET  /legal/health — status of the verification stack (eyecite, token, URL).

The verification endpoint is the primary safety mechanism: a fabricated citation
(404) blocks the response; an ambiguous one (300) is flagged. The page surfaces
per-citation status.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from swarm_os.services.legal.citation_verify import verify_citations, verify_health

log = logging.getLogger(__name__)

router = APIRouter(prefix="/legal", tags=["legal"])


class VerifyCitationsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=60000, description="Text blob to parse + verify citations in")


class VerifyCitationsResponse(BaseModel):
    ok: bool
    citations: list[dict[str, Any]]
    stats: dict[str, Any]
    message: str = ""


@router.post("/verify-citations", response_model=VerifyCitationsResponse)
async def verify_citations_endpoint(req: VerifyCitationsRequest) -> VerifyCitationsResponse:
    """Parse `req.text` with Eyecite and verify each case citation against
    CourtListener. Fabricated citations are reported with verified=false (404)
    so the caller can block/downgrade."""
    try:
        res = await verify_citations(req.text)
    except Exception:
        log.exception("verify-citations failed")
        raise HTTPException(status_code=500, detail="Citation verification failed")
    return VerifyCitationsResponse(
        ok=res.ok,
        citations=[c.__dict__ for c in res.citations],
        stats=res.stats,
        message=res.message,
    )


@router.get("/health")
async def legal_health() -> dict[str, Any]:
    return await verify_health()
