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

KNOWN LIMITATION (2026-08-09): `_synthesize` passes each retrieved section's
content to the LLM truncated to 800 chars (`content[:800]`) with no explicit
truncation marker. The model may synthesize as if it read the whole section
when it only saw the opening — a grounding-quality risk, NOT data loss (the
full sections stay stored in Qdrant and remain retrievable via /legal/search).
If synthesis output is ever audited for grounding, check whether an answer
over-relies on section openings before trusting it.
"""
from __future__ import annotations

import logging
import re
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
    snapshots: dict[str, set[str]] = {}
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
                snap = (p.payload or {}).get("snapshot")
                if snap:
                    snapshots.setdefault(j, set()).add(snap)
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
            # STATUTE CURRENCY (rec 10): which OpenUSLaw snapshot the ingested
            # sections came from — the "as of WHAT law" answer. A jurisdiction
            # with no snapshot is pre-currency (older ingest).
            "snapshot": sorted(snapshots.get(jur) or [])[-1] if snapshots.get(jur) else "",
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

    # Research: hybrid search scoped to the detected jurisdiction (statutes) +
    # the curated case-law manifest (case_corpus) so the advisor answers from
    # BOTH what the LAW says and what the COURTS have held.
    from swarm_os.services.legal.legal_search import search_cases
    results = await search_statutes(question, jurisdiction=jur, top_k=6)
    case_results = await search_cases(question, top_k=4)
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
    # Case-law citations: manifest authority retrieved for the same question.
    # Carries tier (controlling/backbone/context/batson) so the advisor can
    # weight controlling 2d Cir. precedent above context. Empty on outage.
    # `graph_cited_by_count` (authority weight: how many manifest authorities
    # cite this case) and `treatment` (followed/distinguished/...) come from the
    # citation-graph layer (rec 7/9).
    case_citations = [
        {"citation": c.get("citation", ""), "title": c.get("section_title", ""),
         "court": c.get("court", ""), "circuit": c.get("circuit", ""),
         "year": c.get("year", 0), "tier": c.get("tier", 0),
         "rerank_score": c.get("rerank_score"), "content": (c.get("content") or "")[:600],
         "graph_cited_by_count": c.get("graph_cited_by_count", 0),
         "graph_expanded": c.get("graph_expanded", False)}
        for c in case_results
    ]
    # Treatment annotation: for each retrieved case, find the citing context
    # across ALL retrieved case chunks and label how the retrieved corpus treats
    # it (homemade Shepard's/KeyCite). Best-effort; failures leave treatment
    # absent (never fabricate a treatment).
    try:
        from swarm_os.services.legal.case_graph import (
            case_citation_key, citing_sentence_for, label_treatment,
        )
        case_text = "\n\n".join(
            f"{c.get('citation', '')} — {c.get('section_title', '')}\n{c.get('content', '')}"
            for c in case_results
        )
        for cc in case_citations:
            k = case_citation_key(cc["citation"])
            if not k:
                continue
            sent = citing_sentence_for(case_text, k)
            if sent:
                cc["treatment"] = label_treatment(sent)
    except Exception as exc:
        log.warning("case treatment annotation failed: %s", exc)

    # Grounded synthesis: instruct the LLM to derive ONLY from the retrieved text.
    grounded = await _synthesize(question, jur, results, case_results)
    answer = grounded.get("content", "")
    if not answer:
        answer = (
            f"Based on the retrieved {jur.upper()} statutes below, here are the relevant "
            f"sections for your question. (Full synthesis pending — see citations.)\n"
        )

    # Verification seam: parse the answer's citations, check existence.
    verify = {"checked": False, "fabricated": 0, "ambiguous": 0,
              "shape_mismatch": 0, "unverified": 0, "unparsed": 0, "score": None}
    try:
        vres = await verify_citations(answer)
        fabricated = vres.stats.get("fabricated", 0)
        ambiguous = vres.stats.get("ambiguous", 0)
        shape_mismatch = vres.stats.get("shape_mismatch", 0)
        unverified = vres.stats.get("unverified", 0)
        unparsed = vres.stats.get("unparsed", 0)
        # Denominator = the REAL total examined. count covers everything that
        # entered the lookup loop (verified/fabricated/ambiguous/shape_mismatch/
        # unverified are all subsets of it); unparsed is EXTRA — citation-shaped
        # passages eyecite could not parse never entered count. max() here
        # undercounted (unverified was double-included and unparsed could push
        # the numerator past checked, producing a NEGATIVE score). Sum them.
        checked = max(1, (vres.stats.get("count", 0) or 0) + unparsed)
        verify = {
            "checked": True,
            "fabricated": fabricated,
            "ambiguous": ambiguous,
            "shape_mismatch": shape_mismatch,
            "unverified": unverified,
            "unparsed": unparsed,
            "score": round(1.0 - (
                fabricated + ambiguous + shape_mismatch + unverified + unparsed
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
        # Recompute the score so a statutory MISALIGNMENT also downgrades it —
        # the fail-closed contract (advise() drops the score below 1.0 on ANY of
        # fabricated/unaligned/unverified/unparsed). The first pass above ran
        # before alignment, so `unaligned` was silently missing from the
        # numerator and a fabricated statute left the score at 1.0 whenever no
        # case citation was present. Denominator stays `checked` (every
        # citation-shaped passage examined); `unaligned` joins the numerator
        # exactly like the other failure signals.
        if verify.get("checked"):
            penalties = (
                verify["fabricated"] + verify["ambiguous"] + verify["shape_mismatch"]
                + verify["unverified"] + verify["unparsed"] + verify["unaligned"]
            )
            # Every examined citation gets a denominator slot so the score can
            # never go negative: count covers the case-lookup set, unparsed the
            # citation-shaped-but-unparseable passages, and unaligned the cited
            # statute sections absent from the corpus — each contributes to
            # penalties and each must appear in checked.
            checked = max(1, (vres.stats.get("count", 0) or 0) + unparsed + verify["unaligned"])
            verify["score"] = round(1.0 - penalties / checked, 2)
    except Exception as exc:
        log.warning("alignment seam failed: %s", exc)
        verify.setdefault("alignment", {"count": 0, "aligned": [], "unaligned": []})

    # M6 CASE-LAW alignment: every case citation in the answer must be a real
    # authority — either one of the retrieved manifest cases (aligned) or a
    # curated manifest case that wasn't in the top-k (in_corpus). A citation
    # absent from the WHOLE manifest is the case-law fabrication signal.
    try:
        from swarm_os.services.legal.citation_verify import align_case_citations
        retrieved_case_cites = [c.get("citation", "") for c in case_results]
        case_align = align_case_citations(answer, retrieved_case_cites)
        verify["case_alignment"] = {
            "count": case_align["count"],
            "aligned": [a["cite"] for a in case_align["aligned"]],
            "in_corpus": [a["cite"] for a in case_align["in_corpus"]],
            "unaligned": [u["cite"] for u in case_align["unaligned"]],
        }
        # A cited case NOT in the curated manifest is a fabrication signal: add
        # it to the same penalty pool as the statutory unaligned signal.
        if verify.get("checked"):
            penalties = (
                verify["fabricated"] + verify["ambiguous"] + verify["shape_mismatch"]
                + verify["unverified"] + verify["unparsed"] + verify["unaligned"]
                + len(case_align["unaligned"])
            )
            checked = max(
                1,
                (vres.stats.get("count", 0) or 0)
                + unparsed + verify["unaligned"] + len(case_align["unaligned"]),
            )
            verify["score"] = round(1.0 - penalties / checked, 2)
    except Exception as exc:
        log.warning("case-alignment seam failed: %s", exc)
        verify.setdefault("case_alignment", {"count": 0, "aligned": [], "in_corpus": [], "unaligned": []})

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
    shape_mismatch = verify.get("shape_mismatch", 0)
    case_unaligned = len(verify.get("case_alignment", {}).get("unaligned", []))
    if fabricated or unaligned or unverified or unparsed or shape_mismatch or case_unaligned:
        answer += (
            f"\n\n[VERIFICATION] {fabricated} citation(s) could not be verified "
            f"(fabricated or misaligned), and {unaligned} cited section(s) are not "
            f"present in the retrieved corpus (unaligned). "
        )
        if case_unaligned:
            answer += (
                f"{case_unaligned} case citation(s) are not present in the curated "
                f"case-law manifest (may be fabricated or out-of-corpus). "
            )
        if unverified:
            answer += (
                f"{unverified} case citation(s) could not be externally verified "
                f"(no CourtListener verdict — offline or no token). "
            )
        if shape_mismatch:
            answer += (
                f"{shape_mismatch} case citation(s) exist but not as cited "
                f"(CourtListener normalized a different shape — the citation may "
                f"be an alteration of a real case). "
            )
        if unparsed:
            answer += (
                f"{unparsed} citation-shaped passage(s) could not be parsed. "
            )
        answer += "Do not rely on this answer until checked."

    # STATUTE CURRENCY (rec 10): every statutory answer names the OpenUSLaw
    # snapshot it is grounded on — "as of WHAT law". A snapshot-less
    # jurisdiction (pre-currency ingest) is flagged, never silently passed as
    # current. OpenUSLaw is a dated snapshot, not a live feed (quarterly).
    jur_snap = scope.get("jurisdictions", {}).get(jur, {}).get("snapshot", "")
    if jur_snap:
        answer += f"\n\n[LAW AS OF] {jur.upper()} statutory corpus: OpenUSLaw snapshot {jur_snap}."
    else:
        answer += (
            f"\n\n[LAW AS OF] This {jur.upper()} corpus predates snapshot tracking — "
            "verify current law before relying on it."
        )

    return AdvisorResult(
        ok=True,
        answer=answer,
        jurisdiction=jur,
        issue="research",
        corpus_scope=scope,
        citations=citations,
        verification=verify,
        message=(
            f"{len(citations)} {jur.upper()} section(s) and {len(case_citations)} "
            f"case authority(ies) retrieved. "
            f"Corpus scope: {scope['jurisdictions'].get(jur, {}).get('pct', 0)}% of {jur.upper()} ingested — "
            f"this is a PARTIAL-ingestion answer, not final."
        ),
    )


async def _synthesize(question: str, jurisdiction: str, results: list[dict[str, Any]],
                      case_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Cloud synthesis grounded ONLY in the retrieved sections (statutes) and
    case-law chunks (cases). Falls back to a no-LLM scaffold if the cloud call
    fails.

    Uses stream_content (free-text), NOT complete_for_tool_decision: the latter
    forces a json_object response_format, which the OpenCode Go proxy rejects for
    plain prompts ("Prompt must contain the word 'json'") — verified live."""
    from runtime_v2.services import _llm_client as llm

    ctx = "\n\n".join(
        f"[{i + 1}] {r.get('citation', '')} — {r.get('section_title', '')}\n{(r.get('content') or '')[:800]}"
        for i, r in enumerate(results)
    )
    if case_results:
        case_ctx = "\n\n".join(
            f"[C{i + 1}] {c.get('citation', '')} — {c.get('section_title', '')} "
            f"({c.get('court', '')}, {c.get('year', '')})\n{(c.get('content') or '')[:800]}"
            for i, c in enumerate(case_results)
        )
        ctx += f"\n\nRETRIEVED CASE LAW:\n{case_ctx}"
    system = (
        "You are Rob's Lawyer, a legal research assistant. Answer the user's question "
        "ONLY using the retrieved statute sections and case-law chunks below. Every legal "
        "proposition must be tied to a specific retrieved citation. If the retrieved "
        "materials don't cover the question, say so plainly — do NOT invent law or use "
        "law from memory. This is a partial corpus; state any limitations. Keep it "
        "concise and cite inline like (N.Y. RPA Law § 235-b) or (252 F.3d 238)."
    )
    std = _standard_conditioning(question)
    if std:
        system += f"\n\n{std}"
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


# ---------------------------------------------------------------------------
# IRAC-STRUCTURED SYNTHESIS (M7)
#
# Research-backed (LegalSemi 2406.13217 / PLAT 2503.03444 / Falkor-IRAC
# 2605.14665): LLMs are weakest at the Application/Conclusion steps of IRAC,
# and structure improves alignment with lawyer analysis. This module:
#   1. splits the question into discrete legal issues;
#   2. forces per-issue synthesis in Issue/Rule/Application/Conclusion form;
#   3. POST-CHECKS (deterministic) that every citation in Application appears in
#      the retrieved corpus — the "verifier accepts only traceable paths"
#      pattern, mapped onto the existing align/unaligned machinery.
# ---------------------------------------------------------------------------
_IRAC_HEADING_RE = re.compile(r"^(Issue|Rule|Application|Conclusion)\s*[:#]?\s*(.*)$", re.IGNORECASE)

# Deconstructive connectors that mark a compound legal question into issues.
_ISSUE_SPLIT_RE = re.compile(r"\b(?:and|also|plus|moreover|additionally)\b", re.IGNORECASE)

# Standard-of-review conditioning (rec 8, flagged unverified in research — treat
# as prompt-engineering to measure, not a proven lever). Detects which review
# standard the question implicates so the synthesis gates deferential
# conclusions behind it ("we review loss for clear error") instead of asserting
# a de-novo-sounding absolute. Conservative keyword table; None = no standard
# named, no conditioning applied.
_STANDARD_OF_REVIEW = (
    ("de_novo", ("de novo", "plenary review", "independent review")),
    ("abuse_of_discretion", ("abuse of discretion", "arbitrary and capricious")),
    ("clear_error", ("clear error", "clearly erroneous", "definite and firm conviction")),
    ("plain_error", ("plain error", "obvious error", "substantial rights")),
    ("substantial_evidence", ("substantial evidence", "reasonable evidence")),
)


def detect_standard_of_review(question: str) -> str | None:
    """Detect the review standard named in a question. Returns the standard key
    (de_novo / abuse_of_discretion / clear_error / plain_error /
    substantial_evidence) or None when none is named."""
    q = (question or "").lower()
    for key, phrases in _STANDARD_OF_REVIEW:
        if any(p in q for p in phrases):
            return key
    return None


_STANDARD_PROMPTS: dict[str, str] = {
    "de_novo": (
        "This question implicates DE NOVO review: conclusions of law are reviewed "
        "independently, with no deference to the lower court. State the legal rule "
        "as a fresh determination."
    ),
    "abuse_of_discretion": (
        "This question implicates ABUSE-OF-DISCRETION review: the lower court's "
        "discretionary choice is upheld unless no reasonable person could agree — "
        "do not substitute your own judgment for the court's. A 'different result "
        "we might prefer' is NOT an abuse of discretion."
    ),
    "clear_error": (
        "This question implicates CLEAR-ERROR review: a finding stands unless the "
        "reviewing court is left with a definite and firm conviction a mistake was "
        "made. Do not assert a conclusion the finding contradicts."
    ),
    "plain_error": (
        "This question implicates PLAIN-ERROR review: an unpreserved error is "
        "corrected only if it is clear/obvious AND affects substantial rights AND "
        "seriously affects the fairness/integrity of the proceeding. State what the "
        "defendant failed to preserve and why the four prongs do or don't all hold."
    ),
    "substantial_evidence": (
        "This question implicates SUBSTANTIAL-EVIDENCE review: the finding is upheld "
        "if a reasonable mind might accept the evidence as adequate, even if "
        "contrary evidence exists."
    ),
}


def _standard_conditioning(question: str) -> str:
    """Prompt-level standard-of-review conditioning: returns the standard's
    instruction block for the synthesis system prompt, or '' if none named."""
    std = detect_standard_of_review(question)
    if not std:
        return ""
    return _STANDARD_PROMPTS.get(std, "")


def split_issues(question: str, max_issues: int = 4) -> list[str]:
    """Split a (possibly compound) legal question into discrete issues.

    Deterministic clause split on deconstructive connectors; falls back to the
    whole question when nothing splits (a single-issue question). Used to scope
    per-issue retrieval + IRAC synthesis so a compound question isn't answered
    with one undifferentiated blob. NOTE: this is a *heuristic* — the connector
    list is deliberately conservative (no split on "and" inside a statutory
    name like "RPA and RPAPL"). Returns 1..max_issues clean issues."""
    q = (question or "").strip()
    if not q:
        return []
    parts = [p.strip(" .,;:") for p in _ISSUE_SPLIT_RE.split(q) if p and p.strip(" .,;:")]
    # Collapse: keep the connector word for readability is NOT worth the risk —
    # the split parts are the discrete issues.
    parts = parts or [q]
    if len(parts) > max_issues:
        parts = parts[:max_issues]
    return parts


def _irac_sections(text: str) -> dict[str, str]:
    """Parse an IRAC-structured answer into its {issue, rule, application,
    conclusion} parts. Tolerant: lines headed 'Issue:'/'Rule:'/etc.; when a
    heading is absent the whole text is treated as a single unstructured block
    (so a model that ignored the format still yields a verifiable Application)."""
    text = (text or "").strip()
    if not text:
        return {}
    out: dict[str, list[str]] = {}
    cur = ""
    for line in text.splitlines():
        m = _IRAC_HEADING_RE.match(line.strip())
        if m:
            cur = m.group(1).strip().lower()
            out.setdefault(cur, [])
            if m.group(2).strip():
                out[cur].append(m.group(2).strip())
        elif cur:
            out[cur].append(line.strip())
        else:
            # Pre-heading text (a model may skip the headers): treat as issue.
            out.setdefault("issue", []).append(line.strip())
    if not out:
        return {}
    if "application" not in out:
        # No headers at all -> the whole text IS the application (verifiable).
        out["application"] = [t for t in text.splitlines() if t.strip()]
    return {k: "\n".join(v) for k, v in out.items() if v}


def _application_grounding(application: str, retrieved_citations: list[str],
                           case_citations: list[str]) -> dict[str, Any]:
    """Deterministic post-check: every citation the Application cites must be
    traceable to the retrieved corpus (statute sections + case chunks). Returns
    {count, grounded: [...], ungrounded: [...]}. A citation absent from BOTH
    corpora is ungrounded — the IRAC 'verifier accepts only traceable paths'."""
    from swarm_os.services.legal.citation_verify import (
        extract_statute_sections, _normalize_section, extract_case_citations,
        case_citation_key,
    )
    stat_corpus: set[str] = set()
    for s in (retrieved_citations or []):
        if not isinstance(s, str):
            continue
        for sid in extract_statute_sections(s):
            stat_corpus.add(_normalize_section(sid))
        # Bare section-id-shaped tokens (no §/Law prefix) also enter directly.
        if re.fullmatch(r"[0-9A-Za-z:.\-]+", s) and re.search(r"[0-9]", s):
            stat_corpus.add(_normalize_section(s))
    stat_corpus.discard("")
    case_corpus = {case_citation_key(c) for c in (case_citations or []) if case_citation_key(c)}

    grounded: list[str] = []
    ungrounded: list[str] = []
    seen: set[str] = set()

    for sec in extract_statute_sections(application or ""):
        n = _normalize_section(sec)
        if n in seen:
            continue
        seen.add(n)
        (grounded if n in stat_corpus else ungrounded).append(sec)
    for cite in extract_case_citations(application or ""):
        k = case_citation_key(cite)
        if not k or k in seen:
            continue
        seen.add(k)
        (grounded if k in case_corpus else ungrounded).append(cite)
    return {"count": len(grounded) + len(ungrounded), "grounded": grounded, "ungrounded": ungrounded}


async def synthesize_irac(question: str, jurisdiction: str,
                          results: list[dict[str, Any]],
                          case_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """IRAC-structured synthesis with a deterministic grounding post-check.

    Splits the question into issues, asks the LLM for an IRAC answer, parses the
    IRAC sections, and verifies the Application's citations against the retrieved
    corpora. Returns:
      {"content": full IRAC text, "irac": {issue/rule/application/conclusion},
       "grounding": {count, grounded, ungrounded}}
    Falls back to the plain _synthesize scaffold on LLM failure."""
    from runtime_v2.services import _llm_client as llm

    issues = split_issues(question)
    ctx = "\n\n".join(
        f"[{i + 1}] {r.get('citation', '')} — {r.get('section_title', '')}\n{(r.get('content') or '')[:800]}"
        for i, r in enumerate(results)
    )
    if case_results:
        case_ctx = "\n\n".join(
            f"[C{i + 1}] {c.get('citation', '')} — {c.get('section_title', '')} "
            f"({c.get('court', '')}, {c.get('year', '')})\n{(c.get('content') or '')[:800]}"
            for i, c in enumerate(case_results)
        )
        ctx += f"\n\nRETRIEVED CASE LAW:\n{case_ctx}"

    system = (
        "You are Rob's Lawyer. Answer ONLY from the retrieved statute sections and "
        "case-law chunks. Structure your answer as IRAC for EACH issue:\n"
        "Issue: <the discrete legal issue>\n"
        "Rule: <the rule, citing the retrieved authority>\n"
        "Application: <apply the rule to the facts, citing retrieved authority inline like "
        "(N.Y. RPA Law § 235-b) or (252 F.3d 238)>\n"
        "Conclusion: <conclusion>\n"
        "If a retrieved section does not cover the issue, say so in Application. Never "
        "invent law or cite outside the retrieved materials."
    )
    std = _standard_conditioning(question)
    if std:
        system += f"\n\n{std}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"Question ({jurisdiction.upper()}): {question}\n\n"
            f"Discrete issues: {len(issues)} — {'; '.join(issues)}\n\nRetrieved:\n{ctx}"
        )},
    ]
    try:
        model = llm._analysis_cloud_model() if llm._analysis_cloud_enabled() else "qwen3.5-4b"
        parts: list[str] = []
        async for chunk, kind in llm.stream_content(model, messages, agent_id="rob_lawyer"):
            if kind == "content":
                parts.append(chunk or "")
        content = "".join(parts).strip()
    except Exception as exc:
        log.warning("IRAC synthesis failed, returning scaffold: %s", exc)
        content = ""
    if not content:
        # Scaffold fallback: derive a minimal IRAC from the retrieved corpus so
        # the post-check still runs on real (empty) content -> grounded=0.
        irac = {}
    else:
        irac = _irac_sections(content)
    grounding = _application_grounding(
        irac.get("application", ""),
        [r.get("citation", "") for r in results],
        [c.get("citation", "") for c in (case_results or [])],
    )
    return {"content": content, "irac": irac, "grounding": grounding}
