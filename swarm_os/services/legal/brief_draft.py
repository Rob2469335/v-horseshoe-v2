"""Brief/motion drafting as a checklist-conjunctive task for Rob's Lawyer.

Research-grounded (Harvey Legal Agent Benchmark 2026: models pass ~90% of
individual rubric criteria but fail the conjunctive deliverable ~80% of the
time; the fix the benchmarks point to is a structure-first skeleton + a
post-generation CHECKER that re-verifies every citation and flags assertions
lacking a retrieved source — NOT a stronger prompt). This module:

  1. DRAFT_SKELETON — the full 2d Cir. brief structure (cover page, corporate
     disclosure, jurisdictional statement, statement of issues, statement of
     the case, argument with a rule/application/conclusion per issue, conclusion,
     certificate of compliance with the type-volume limit). A machine-checkable
     skeleton that the advisor's IRAC + verification seams then fill.
  2. CHECK_BRIEF — the post-generation checker: parse the draft, re-verify every
     citation against CourtListener + the retrieved corpus (the existing seams),
     and flag assertions in the Argument that lack a retrieved-source citation.
     This is the "models pass 90% of criteria but fail the deliverable" killer.

Deterministic core (skeleton + checker); the sections are filled by the caller
from synthesize_irac + verify_citations. No LLM in the checker itself.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from swarm_os.services.legal.citation_verify import (
    extract_case_citations,
    extract_statute_sections,
)

log = logging.getLogger(__name__)

# The 2d Cir. brief skeleton (FRAP 28 + 2d Cir. Local Rule 28.1). Each section
# is a (header, purpose) pair the drafter fills from the IRAC/verification seams.
BRIEF_SECTIONS: list[tuple[str, str]] = [
    ("Cover Page", "Case caption, court, docket no., party, counsel, title of brief"),
    (
        "Corporate Disclosure Statement",
        "FRAP 26.1 / 2d Cir. L.R. 26.1 — any corporate parent",
    ),
    ("Table of Contents", "Sections + page numbers"),
    ("Table of Authorities", "All cases/statutes cited, with pages"),
    (
        "Jurisdictional Statement",
        "FRAP 28(a)(4) — basis of district-court jurisdiction, appealability, timeliness",
    ),
    ("Statement of Issues", "The issues presented for review"),
    ("Statement of the Case", "Procedural history + facts (record citations)"),
    ("Summary of the Argument", "Concise overview"),
    ("Argument", "One Rule/Application/Conclusion block per issue (IRAC)"),
    ("Conclusion", "The precise relief sought"),
    ("Certificate of Compliance", "FRAP 32(g) — typeface/type-volume compliance"),
    ("Addendum", "2d Cir. L.R. 28.1 — relevant district-court orders/statutes"),
]

# Argument-block assertion rules: a Rule sentence should cite a statute or case;
# an Application sentence should cite the record OR a retrieved authority. The
# checker flags sentences that assert a legal proposition with no citation.
_RULE_SENT_RE = re.compile(
    r"\b(?:rule|standard|require|provid|govern|entitle|shall|must|may)\b", re.IGNORECASE
)
_APPLICATION_SENT_RE = re.compile(
    r"\b(?:here|this case|the defendant|the government|the record|the court)\b",
    re.IGNORECASE,
)
# Sentence splitter that protects legal abbreviations — "United States v.
# Moseley", "F.3d 9", "U.S. 644" must NOT split on their internal periods.
# Split only at a period that ends a REAL sentence: the period is followed by
# whitespace + an uppercase word AND the token preceding the period is not a
# legal abbreviation (v., u.s., f.3d, cir., inc., et al., e.g., i.e., id.).
_ABBR_TOKENS = {
    "v",
    "u",
    "s",
    "f",
    "d",
    "cir",
    "inc",
    "ltd",
    "co",
    "corp",
    "et",
    "al",
    "e",
    "g",
    "i",
    "id",
    "no",
    "sup",
    "n",
    "y",
    "rpa",
    "abc",
    "cpl",
    "gol",
    # Multi-letter dotted abbreviations (period-stripped): state codes
    # (N.Y., N.J., G.A.), U.S., F.3d/F.4th reporters.
    "ny",
    "nj",
    "ga",
    "nc",
    "us",
    "f3d",
    "f4th",
    "f2d",
    "fappx",
    "ed",
    "sd",
    "ct",
    "app",
    "lexis",
}
_SENTENCE_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=[A-Z])")


def _split_sentences(text: str) -> list[str]:
    """Split `text` into sentences, protecting legal abbreviations."""
    if not text:
        return []
    out: list[str] = []
    cur: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(text):
        # part = "previous. next" chunks; decide whether to join or split.
        # Each `part` after split starts with the uppercase word; check whether
        # the token BEFORE the split-point was an abbreviation.
        if cur:
            prev = cur[-1]
            prev_token = prev.rstrip().split()[-1].lower() if prev.strip() else ""
            # prev_token is like "v", "F", "U" (period stripped). Abbreviation?
            prev_alpha = re.sub(r"[^a-z0-9]", "", prev_token)
            if prev_alpha in _ABBR_TOKENS or (
                len(prev_alpha) == 1 and prev_alpha.isalpha()
            ):
                # Abbreviation — join (no sentence break).
                cur.append(part)
                continue
        out.append(" ".join(cur))
        cur = [part]
    if cur:
        out.append(" ".join(cur))
    return [s.strip() for s in out if s.strip()]


def draft_skeleton(issue_headers: list[str] | None = None) -> list[dict[str, str]]:
    """Return the brief skeleton as a list of {section, purpose} — the
    machine-checkable outline the drafter fills. `issue_headers` (from
    split_issues) become Argument sub-blocks. Deterministic."""
    skeleton = [{"section": s, "purpose": p} for s, p in BRIEF_SECTIONS]
    if issue_headers:
        for i, issue in enumerate(issue_headers, 1):
            skeleton.append(
                {
                    "section": f"Argument Issue {i}: {issue}",
                    "purpose": "Rule → Application → Conclusion, each citing a retrieved authority",
                }
            )
    return skeleton


def _has_citation(sentence: str) -> bool:
    """True when a sentence carries a case or statute citation (any shape the
    verification seams recognize)."""
    if extract_case_citations(sentence) or extract_statute_sections(sentence):
        return True
    # Fallback: any vol-reporter-page / § shape.
    if re.search(r"\b\d{1,4}\s+[A-Za-z][A-Za-z0-9.\-]*\s+\d{1,5}\b", sentence):
        return True
    if re.search(r"\u00a7\s*\d", sentence):
        return True
    return False


def check_brief(
    argument_text: str, retrieved_statutes: list[str], retrieved_cases: list[str]
) -> dict[str, Any]:
    """The post-generation checker. For each Argument sentence:
      - a Rule/Application sentence asserting a legal proposition MUST carry a
        citation (case or statute) — else flagged `uncited_assertion`;
      - every case citation must be in the retrieved case corpus OR the curated
        manifest (reuse the M6 alignment) — else flagged `uncited_case`;
      - every statute citation must be in the retrieved statute corpus (the
        M4 alignment) — else flagged `uncited_statute`.
    Returns {assertions, uncited_assertions, case_align, statute_align,
             ok (no uncited assertions)}. Deterministic, offline."""
    sentences = [s for s in _split_sentences(argument_text or "")]
    assertions: list[dict[str, str]] = []
    uncited: list[dict[str, str]] = []
    for s in sentences:
        is_rule = bool(_RULE_SENT_RE.search(s))
        is_app = bool(_APPLICATION_SENT_RE.search(s))
        if not (is_rule or is_app):
            continue  # not an assertion — narrative/factual sentence
        rec = {
            "sentence": s,
            "role": "rule" if is_rule and not is_app else "application",
        }
        assertions.append(rec)
        if not _has_citation(s):
            uncited.append(rec)

    # Citation alignment (reuse the existing seams).
    from swarm_os.services.legal.citation_verify import (
        align_citations,
        align_case_citations,
    )

    stat_align = align_citations(argument_text or "", retrieved_statutes)
    case_align = align_case_citations(argument_text or "", retrieved_cases)
    unaligned_statutes = [u["section"] for u in stat_align["unaligned"]]
    unaligned_cases = [u["cite"] for u in case_align["unaligned"]]

    return {
        "assertions": assertions,
        "uncited_assertions": uncited,
        "uncited_count": len(uncited),
        "case_align": case_align,
        "statute_align": stat_align,
        "unaligned_statutes": unaligned_statutes,
        "unaligned_cases": unaligned_cases,
        "ok": not uncited and not unaligned_statutes and not unaligned_cases,
    }


def render_check(check: dict[str, Any]) -> str:
    """Human-readable checker output for a CLI/console surface."""
    out = [
        f"Assertions: {len(check['assertions'])}",
        f"Uncited assertions: {check['uncited_count']}",
        f"Unaligned statutes: {check['unaligned_statutes']}",
        f"Unaligned cases: {check['unaligned_cases']}",
        f"Overall: {'PASS' if check['ok'] else 'FAIL — fix before filing'}",
    ]
    for u in check["uncited_assertions"]:
        out.append(f"  [uncited] ({u['role']}) {u['sentence'][:120]}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# FRAP 32 TYPE-VOLUME + FORMAT LINT (Build 4)
#
# The #1 "filer rejected the brief" failure mode is deterministic, not AI: FRAP
# 32(a)(7)(B) caps the principal brief at 13,000 words (half that for a reply),
# and FRAP 32(a)(5)-(6) mandate typeface/margins. Commercial tools charge for a
# docx checker; this is a deterministic text lint. Accepts plain text (words
# counted) or a {text, word_count} dict (if the caller counts exactly).
#
# RULE TEXT (current, source: LII https://www.law.cornell.edu/rules/frap/rule_32
# fetched 2026-08-11): FRAP 32(a)(7)(B)(i) — "A principal brief is acceptable
# if it contains no more than 13,000 words"; 32(a)(7)(B)(ii) — "A reply brief is
# acceptable if it contains no more than half of the type volume specified in
# Rule 32(a)(7)(B)(i)" (i.e. 6,500 words). 32(f) excludes cover page, disclosure
# statement, table of contents/citations, addendum, certificate, signature
# block, proof of service from the count.
# ---------------------------------------------------------------------------
_FRAP32_WORD_LIMIT = 13000
_FRAP32_REPLY_WORD_LIMIT = 6500


def check_frap32(
    text_or_dict: Any, title: str = "brief", reply: bool = False
) -> dict[str, Any]:
    """Check FRAP 32(a)(7)(B) type-volume + the certificate of compliance.

    `text_or_dict` may be plain text (words counted here) or
    {"text": ..., "word_count": int}. `reply=True` applies the 6,500-word reply
    limit (32(a)(7)(B)(ii)); principal briefs use 13,000 (32(a)(7)(B)(i)).
    Returns {words, limit, over, has_certificate, ok} where ok = (not over AND
    has_certificate). A brief over the limit or missing its certificate is
    rejected by the clerk — the deterministic lint catches it before filing."""
    if isinstance(text_or_dict, dict):
        text = text_or_dict.get("text", "")
        words = int(text_or_dict.get("word_count") or 0)
    else:
        text = str(text_or_dict or "")
        words = len(text.split())
    limit = _FRAP32_REPLY_WORD_LIMIT if reply else _FRAP32_WORD_LIMIT
    has_certificate = "certificate of compliance" in (text or "").lower()
    over = words > limit
    return {
        "title": title,
        "words": words,
        "limit": limit,
        "reply": reply,
        "over": over,
        "remaining": max(0, limit - words),
        "has_certificate": has_certificate,
        "ok": not over and has_certificate,
    }


# ---------------------------------------------------------------------------
# LEGALCITETRUST FIDELITY PASS (Build 5)
#
# Evidence (LegalCiteTrust 2607.20872): citation trust decomposes into
# Existence / Fidelity / Applicability — and F/A-level revision improves trust
# MORE than existence-only filtering. The existing checker verifies a citation
# EXISTS and ALIGNS to the corpus; this pass checks FIDELITY: does the citation
# actually SUPPORT the sentence it's attached to? Deterministic token-overlap
# between the sentence's substantive tokens and the retrieved source's content.
# ---------------------------------------------------------------------------


def check_fidelity(
    argument_text: str, source_by_cite: dict[str, str], threshold: float = 0.3
) -> dict[str, Any]:
    """For each assertion sentence carrying a citation (OR a name-only reference
    to a known case), check that the cited source's content overlaps the
    sentence's substantive tokens — i.e. the citation supports (not merely
    accompanies) the assertion. A citation whose source shares almost no tokens
    with its sentence is a FIDELITY failure (the classic 'cite exists but
    doesn't say what I claim' error).

    `source_by_cite`: {citation_key: source_content} for the retrieved sources.
    The case NAME is resolved from each source's leading "CaseName, Vol Rep
    Page" so a name-only reference ("Moseley held that...") is checked too —
    otherwise a fabricated holding attributed to a REAL case by name would
    slip past (the demonstrated gap: eyecite only parses reporter-form cites).
    Deterministic, offline. Returns {checked, supporting, unsupporting, rate}."""
    import re as _re
    from swarm_os.services.legal.citation_verify import (
        extract_case_citations,
        extract_statute_sections,
        _normalize_section,
        case_citation_key,
    )

    # Build a case-NAME -> source map from the sources themselves: each source
    # content begins "CaseName, Vol Rep Page: ..." — the name is the leading
    # text before the first comma, and it may be referenced by name later.
    name_to_source: dict[str, str] = {}
    for key, content in (source_by_cite or {}).items():
        # The source content normally leads with "CaseName, cite:" — the case
        # name is everything before the first comma (or the first sentence).
        lead = (content or "").split(",")[0].strip()
        lead = lead.split(":")[0].strip()
        if lead and len(lead) > 3 and _re.search(r"[a-z]", lead):
            name_to_source[lead.lower()] = content
            # Also register the plain surname for single-name references.
            for word in lead.split():
                if word and word[0].isupper() and len(word) > 2:
                    name_to_source.setdefault(word.lower(), content)

    def _tokens(s: str) -> set[str]:
        return set(_re.findall(r"[a-z0-9']+", (s or "").lower())) - {
            "the",
            "a",
            "an",
            "of",
            "to",
            "in",
            "for",
            "on",
            "and",
            "or",
            "is",
            "are",
            "be",
            "by",
            "that",
            "this",
            "with",
            "from",
            "as",
            "at",
            "it",
            "its",
            "was",
            "were",
            "has",
            "have",
            "had",
            "not",
        }

    supporting: list[dict] = []
    unsupporting: list[dict] = []
    checked = 0
    for s in _split_sentences(argument_text or ""):
        case_cites = extract_case_citations(s)
        stat_cites = extract_statute_sections(s)
        if not case_cites and not stat_cites:
            # No parseable cite — but a NAME-ONLY reference to a known case
            # still must be checked (the fabricated-claim-by-name gap).
            lowered = (s or "").lower()
            matched_src = next(
                (
                    src
                    for name, src in name_to_source.items()
                    if name and name in lowered
                ),
                "",
            )
            if not matched_src:
                continue
        else:
            # Find the source content for this sentence's citations.
            matched_src = ""
            for cc in case_cites:
                k = case_citation_key(cc)
                if k and k in (source_by_cite or {}):
                    matched_src = source_by_cite[k]
                    break
            if not matched_src:
                for sc in stat_cites:
                    n = _normalize_section(sc)
                    if n in (source_by_cite or {}):
                        matched_src = source_by_cite[n]
                        break
        if not matched_src:
            continue  # no source content available -> not checkable
        sent_tokens = _tokens(s)
        if not sent_tokens:
            continue
        src_tokens = _tokens(matched_src)
        overlap = (
            len(sent_tokens & src_tokens) / max(1, len(sent_tokens))
            if src_tokens
            else 0.0
        )
        checked += 1
        rec = {
            "sentence": s,
            "overlap": round(overlap, 3),
            "cites": case_cites + stat_cites,
        }
        (supporting if overlap >= threshold else unsupporting).append(rec)
    return {
        "checked": checked,
        "supporting": supporting,
        "unsupporting": unsupporting,
        "rate": round(len(supporting) / checked, 4) if checked else 0.0,
    }
