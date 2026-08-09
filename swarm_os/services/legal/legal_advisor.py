"""M3 vertical slice for Rob's Lawyer — intake → research → synthesis.

The core requirement this module enforces STRUCTURALLY (not in comments):

1. CORPUS-SCOPE MARKER IN THE RESPONSE: every answer carries a `corpus_scope`
   dict computed LIVE from Qdrant (what's actually ingested) vs the source
   Parquets (what should be). This is in the response payload, so if the answer
   is ever used for a real query, the incompleteness is visible AT THE POINT OF
   USE — not something to remember from a commit message.

2. FAIL-CLOSED JURISDICTION GATE: if the question's jurisdiction has nothing
   (or too little) ingested, we do NOT synthesize a plausible-sounding answer
   from a different state's law. We say "I don't have <jurisdiction> law yet"
   and return no answer. A landlord-tenant question about NJ must never get a
   synthesis built on NY law that looks right for a different state.

3. GROUNDED SYNTHESIS: the LLM is instructed to derive the answer ONLY from the
   retrieved sections (each carrying a citation). The citation-verification seam
   (citation_verify.py) is wired in to flag fabricated/misaligned citations; a
   fabricated citation downgrades the answer (verification.score low).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Jurisdiction → how a user might reference it (for the intake gate).
JURISDICTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ny": ("new york", " ny ", " nyc", "n.y.", "nyc"),
    "nj": ("new jersey", " nj ", "n.j.", "jersey"),
    "ga": ("georgia", " ga ", "ga.", "atlanta"),
    "nc": ("north carolina", " nc ", "n.c.", "carolina"),
    "federal": ("federal", "us code", "united states code", "usc", "cfr", "federal court"),
}
EXPECTED_SECTIONS: dict[str, int] = {}  # populated lazily from the Parquets


def _expected_sections() -> dict[str, int]:
    """Expected in-force statute section counts per jurisdiction, from the source
    Parquets (the ground truth for what SHOULD be ingested)."""
    if EXPECTED_SECTIONS:
        return EXPECTED_SECTIONS
    for jur in ("ny", "nj", "ga", "nc", "federal"):
        f = Path(f"data/legal/us_{jur}_statutes.parquet")
        if not f.exists():
            EXPECTED_SECTIONS[jur] = 0
            continue
        try:
            import pyarrow.parquet as pq
            t = pq.read_table(str(f))
            rows = t.to_pylist()
            EXPECTED_SECTIONS[jur] = sum(
                1 for r in rows
                if r.get("act_status") == "in_force"
                and r.get("document_type") in (None, "statute")
            )
        except Exception as exc:
            log.warning("failed to count expected sections for %s: %s", jur, exc)
            EXPECTED_SECTIONS[jur] = 0
    return EXPECTED_SECTIONS


async def corpus_scope() -> dict[str, Any]:
    """Live ingestion state per jurisdiction. Computed from Qdrant counts vs
    expected Parquet counts — the structural, in-band corpus-scope marker."""
    ingested: dict[str, int] = {}
    try:
        from qdrant_client import AsyncQdrantClient
        import os
        client = AsyncQdrantClient(url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"))
        # Qdrant doesn't expose per-value counts directly; scroll is the honest
        # way for a small corpus (a fully-ingested corpus here is ~190k points,
        # scrollable in one pass).
        offset: Any = None
        while True:
            r = await client.scroll("legal_statutes", limit=5000, with_payload=True, offset=offset)
            pts = r[0]
            for p in pts:
                j = (p.payload or {}).get("jurisdiction")
                if j:
                    ingested[j] = ingested.get(j, 0) + 1
            if r[1] is None:
                break
            offset = r[1]
        await client.close()
    except Exception as exc:
        log.warning("corpus_scope scroll failed: %s", exc)

    expected = _expected_sections()
    jurisdictions: dict[str, dict[str, Any]] = {}
    for jur, exp in expected.items():
        got = ingested.get(jur, 0)
        jurisdictions[jur] = {
            "ingested": got,
            "expected": exp,
            "complete": exp > 0 and got >= exp * 0.99,
            "pct": round((got / exp) * 100, 1) if exp else 0.0,
        }
    return {
        "jurisdictions": jurisdictions,
        "total_ingested": sum(ingested.values()),
        "total_expected": sum(expected.values()),
    }


def _detect_jurisdiction(question: str) -> str | None:
    """Detect the jurisdiction from the question text. Returns None if none is
    named (ambiguous) — in that case the advisor refuses to guess."""
    q = f" {question.lower()} "
    for jur, keys in JURISDICTION_KEYWORDS.items():
        if any(k in q for k in keys):
            return jur
    return None


@dataclass
class AdvisorResult:
    ok: bool
    answer: str = ""
    jurisdiction: str | None = None
    issue: str = ""
    corpus_scope: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    fail_closed: bool = False


def _requires_min_coverage(scope: dict[str, Any], jurisdiction: str, minimum_pct: float = 5.0) -> bool:
    """True if the jurisdiction has at least `minimum_pct` of its expected
    sections ingested — enough to synthesize about it without being misleading.
    Below the floor we fail closed (we don't know enough yet)."""
    info = scope.get("jurisdictions", {}).get(jurisdiction, {})
    return bool(info.get("expected")) and info.get("pct", 0) >= minimum_pct


async def advise(question: str) -> AdvisorResult:
    """Full M3 flow. Structurally non-final: the answer's corpus_scope marker
    is always present and always reflects live ingestion state."""
    from swarm_os.services.legal.legal_search import search_statutes
    from swarm_os.services.legal.citation_verify import verify_citations

    scope = await corpus_scope()
    jur = _detect_jurisdiction(question)

    if jur is None:
        return AdvisorResult(
            ok=False, fail_closed=True, corpus_scope=scope,
            message=(
                "I couldn't determine the jurisdiction. Please say which state "
                "(New York, New Jersey, Georgia, North Carolina) or federal law applies."
            ),
        )

    if not _requires_min_coverage(scope, jur):
        got = scope["jurisdictions"].get(jur, {})
        return AdvisorResult(
            ok=False, fail_closed=True, jurisdiction=jur, corpus_scope=scope,
            message=(
                f"I don't have {jur.upper()} law ingested yet "
                f"({got.get('ingested', 0)}/{got.get('expected', 0)} sections, "
                f"{got.get('pct', 0)}%). I won't answer this with another state's law. "
                "Run the corpus ingestion for that jurisdiction first, or ask about "
                "a jurisdiction that is available."
            ),
        )

    # Research: hybrid search scoped to the detected jurisdiction.
    results = await search_statutes(question, jurisdiction=jur, top_k=6)
    if not results:
        return AdvisorResult(
            ok=False, fail_closed=True, jurisdiction=jur, corpus_scope=scope,
            message="Retrieval returned nothing for that question — no sections matched.",
        )

    citations = [
        {"citation": r.get("citation", ""), "title": r.get("section_title", ""),
         "jurisdiction": r.get("jurisdiction", jur),
         "rerank_score": r.get("rerank_score"), "content": (r.get("content") or "")[:600]}
        for r in results
    ]

    # Grounded synthesis: instruct the LLM to derive ONLY from the retrieved text.
    grounded = await _synthesize(question, jur, results)
    answer = grounded.get("content", "")
    if not answer:
        answer = (
            f"Based on the retrieved {jur.upper()} statutes below, here are the relevant "
            f"sections for your question. (Full synthesis pending — see citations.)\n"
        )

    # Verification seam: parse the answer's citations, check existence.
    verify = {"checked": False, "fabricated": 0, "ambiguous": 0,
              "unverified": 0, "unparsed": 0, "score": None}
    try:
        vres = await verify_citations(answer)
        fabricated = vres.stats.get("fabricated", 0)
        ambiguous = vres.stats.get("ambiguous", 0)
        unverified = vres.stats.get("unverified", 0)
        unparsed = vres.stats.get("unparsed", 0)
        checked = max(1, vres.stats.get("count", 0) or 0, unverified, unparsed)
        verify = {
            "checked": True,
            "fabricated": fabricated,
            "ambiguous": ambiguous,
            "unverified": unverified,
            "unparsed": unparsed,
            "score": round(1.0 - (
                fabricated + ambiguous + unverified + unparsed
            ) / checked, 2),
        }
    except Exception as exc:
        log.warning("verification seam failed: %s", exc)

    # M4 statutory-alignment seam: every §-cited section in the answer must
    # exist among the retrieved corpus sections. Eyecite can't do this (it
    # mangles statutes); alignment is deterministic + offline. An unaligned
    # cited section is the statutory-fabrication signal.
    try:
        from swarm_os.services.legal.citation_verify import align_citations
        retrieved_cites = [r.get("citation", "") for r in results]
        align = align_citations(answer, retrieved_cites)
        verify["alignment"] = {
            "count": align["count"],
            "aligned": [a["section"] for a in align["aligned"]],
            "unaligned": [u["section"] for u in align["unaligned"]],
        }
        verify["unaligned"] = len(align["unaligned"])
    except Exception as exc:
        log.warning("alignment seam failed: %s", exc)
        verify.setdefault("alignment", {"count": 0, "aligned": [], "unaligned": []})

    # FAIL-CLOSED: a fabricated OR unaligned citation downgrades the answer —
    # we surface it, and if fabrication is present we mark the answer as
    # not-trustworthy. The L3-trap guard closes the "couldn't check" hole: an
    # UNVERIFIED citation (parsed but no verdict — no CourtListener token /
    # outage) or an UNPARSED citation-shaped passage (eyecite couldn't parse it)
    # must be surfaced as "not verified", never silently pass as "0 citation
    # issues". (score + appended warning, not a silent clean.)
    fabricated = verify.get("fabricated", 0)
    unaligned = verify.get("unaligned", 0)
    unverified = verify.get("unverified", 0)
    unparsed = verify.get("unparsed", 0)
    if fabricated or unaligned or unverified or unparsed:
        answer += (
            f"\n\n[VERIFICATION] {fabricated} citation(s) could not be verified "
            f"(fabricated or misaligned), and {unaligned} cited section(s) are not "
            f"present in the retrieved corpus (unaligned). "
        )
        if unverified:
            answer += (
                f"{unverified} case citation(s) could not be externally verified "
                f"(no CourtListener verdict — offline or no token). "
            )
        if unparsed:
            answer += (
                f"{unparsed} citation-shaped passage(s) could not be parsed. "
            )
        answer += "Do not rely on this answer until checked."

    return AdvisorResult(
        ok=True,
        answer=answer,
        jurisdiction=jur,
        issue="research",
        corpus_scope=scope,
        citations=citations,
        verification=verify,
        message=(
            f"{len(citations)} {jur.upper()} section(s) retrieved. "
            f"Corpus scope: {scope['jurisdictions'].get(jur, {}).get('pct', 0)}% of {jur.upper()} ingested — "
            f"this is a PARTIAL-ingestion answer, not final."
        ),
    )


async def _synthesize(question: str, jurisdiction: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Cloud synthesis grounded ONLY in the retrieved sections. Falls back to a
    no-LLM scaffold if the cloud call fails.

    Uses stream_content (free-text), NOT complete_for_tool_decision: the latter
    forces a json_object response_format, which the OpenCode Go proxy rejects for
    plain prompts ("Prompt must contain the word 'json'") — verified live."""
    from runtime_v2.services import _llm_client as llm

    ctx = "\n\n".join(
        f"[{i + 1}] {r.get('citation', '')} — {r.get('section_title', '')}\n{(r.get('content') or '')[:800]}"
        for i, r in enumerate(results)
    )
    system = (
        "You are Rob's Lawyer, a legal research assistant. Answer the user's question "
        "ONLY using the retrieved statute sections below. Every legal proposition must "
        "be tied to a specific retrieved citation. If the retrieved sections don't cover "
        "the question, say so plainly — do NOT invent law or use law from memory. "
        "This is a partial corpus; state any limitations. Keep it concise and cite inline "
        "like (N.Y. RPA Law § 235-b)."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Question ({jurisdiction.upper()}): {question}\n\nRetrieved statutes:\n{ctx}"},
    ]
    try:
        model = llm._analysis_cloud_model() if llm._analysis_cloud_enabled() else "qwen3.5-4b"
        parts: list[str] = []
        # stream_content yields (content, kind) — content FIRST. Unpack in that
        # order; the reversed unpack (kind, chunk) silently produced zero parts.
        async for chunk, kind in llm.stream_content(model, messages, agent_id="rob_lawyer"):
            if kind == "content":
                parts.append(chunk or "")
        return {"content": "".join(parts).strip()}
    except Exception as exc:
        log.warning("synthesis failed, returning scaffold: %s", exc)
        return {"content": ""}
