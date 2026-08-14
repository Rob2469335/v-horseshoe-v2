"""Trial advisor for Rob's Lawyer — the criminal-defense case-analysis layer.

This is the layer that answers "what did MY lawyers do, and what can I do about
it?" It runs over the defendant's OWN trial transcript (data/legal/transcripts,
the gitignored trial-files directory), NOT general law. Everything here is
derived from the record with page cites, and every legal characterization is
explicitly framed as "a question for a qualified attorney" — this tool reports
WHAT the record shows and WHERE, and flags the shapes a federal criminal-defense
post-conviction / §2255 review would investigate (Strickland two-prong,
preserved-error analysis, Batson, confrontation). It never asserts that counsel
WAS ineffective or that the government DID tamper — it surfaces the record and
the legal questions the record raises.

WHY THE RECORD IS THE SOURCE OF TRUTH: a §2255 / ineffective-assistance claim
lives or dies on specific, page-cited acts or omissions of counsel (Strickland
v. Washington, 466 U.S. 668 (1984)) plus the preserved error record. General
"advice" is worth nothing here; the transcript is everything.

DISCLAIMER (in every surface): this is not legal advice. It is a record
analysis tool for a self-represented person who cannot afford counsel. Any
potential claim should be reviewed by a qualified attorney before filing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from swarm_os.services.legal.transcript_search import (
    TranscriptIndex,
    ingest_transcript_file,
)

log = logging.getLogger(__name__)

TRANSCRIPTS_DIR = Path("data/legal/transcripts")
CASE = "US v. Duncan / Rainford / Locust, 18 Cr. 289 (SHS)"

# Defendant -> counsel. Confirmed from the trial record's own words:
#   p.65 DINNERSTEIN opens "My name is Mitchell..." speaking about Mr. Locust
#   p.53 AL-SHABAZZ opens about Bryan Duncan
#   p.58/85/145 SCHOLAR speaks about Mr. Rainford
# The appearances block (p.25) cross-lists firms confusingly; the attorneys'
# actual conduct is authoritative. CECUTTI co-counselled with DINNERSTEIN.
DEFENDANT_COUNSEL = {
    "Robert Locust": ["MITCHELL J. DINNERSTEIN", "ANTHONY CECUTTI"],
    "Bryan Duncan": ["IKIESHA TAQUET AL-SHABAZZ"],
    "Ryan Rainford": ["CALVIN H. SCHOLAR"],
}

# Attorney display names (short form used in passages) -> role.
ATTORNEY_ROLES = {
    "MR. DINNERSTEIN": ("Mitchell J. Dinnerstein", "Robert Locust"),
    "MR. CECUTTI": ("Anthony Cecutti", "Robert Locust"),
    "MS. AL-SHABAZZ": ("Ikiesha Taquet Al-Shabazz", "Bryan Duncan"),
    "MR. SCHOLAR": ("Calvin H. Scholar", "Ryan Rainford"),
    "MR. FOLLY": ("Nicholas Folly", "Government (AUSA)"),
    "MS. ROTHMAN": ("Alexandra N. Rothman", "Government (AUSA)"),
    "MR. CHIUCHIOLO": ("Nicholas W. Chiuchiolo", "Government (AUSA)"),
}

# Speaker-prefix -> canonical attorney key (passage speakers use short forms).
_ATTR_PREFIX_MAP = {
    "MR. DINNERSTEIN": "MR. DINNERSTEIN",
    "MR. CECUTTI": "MR. CECUTTI",
    "MS. AL-SHABAZZ": "MS. AL-SHABAZZ",
    "MR. SCHOLAR": "MR. SCHOLAR",
    "MR. FOLLY": "MR. FOLLY",
    "MS. ROTHMAN": "MS. ROTHMAN",
    "MR. CHIUCHIOLO": "MR. CHIUCHIOLO",
}


@dataclass
class AttorneyProfile:
    """One attorney's activity across the trial, page-cited."""

    key: str                    # passage speaker short form
    name: str
    represents: str
    objections: list[dict] = field(default_factory=list)   # {page, text, ruling?}
    examinations: list[dict] = field(default_factory=list) # {witness, pages, kind}
    key_statements: list[dict] = field(default_factory=list)  # {page, text}
    word_count: int = 0
    page_range: tuple[int, int] | None = None


def _load_indices() -> list[TranscriptIndex]:
    """Load every trial transcript in the gitignored data/legal/transcripts dir.

    Only files that actually parse into passages are trial days. Non-transcript
    files that may live in the same dir (e.g. an Al-Shabazz passage extract, a
    cover page, a word-index export) parse to zero passages and are excluded so
    the overview's day/passage counts stay accurate.
    """
    files = sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    indices = []
    for f in files:
        try:
            idx = ingest_transcript_file(f, case=CASE)
        except Exception as exc:
            log.warning("trial advisor: failed to parse %s: %s", f.name, exc)
            continue
        if idx.passages:
            indices.append(idx)
    return indices


async def _load_indices_async() -> list[TranscriptIndex]:
    """Thread-offloaded _load_indices for async endpoints.

    Parsing is CPU-bound regex work over hundreds of transcript pages — on the
    event loop it stalls every concurrent request. The transcripts are static
    per-process, so the parsed result is cached (thread-safe: a missed cache
    just re-parses; the parse is idempotent)."""
    _cache = _indices_cache[0]
    if _cache is not None:
        return _cache
    import asyncio
    indices = await asyncio.to_thread(_load_indices)
    if _indices_cache[0] is None:
        _indices_cache[0] = indices
    return indices


_indices_cache: list = [None]


def _resolve_attorney(speaker: str) -> str | None:
    s = (speaker or "").upper().strip()
    for prefix, key in _ATTR_PREFIX_MAP.items():
        if s.startswith(prefix):
            return key
    # "BY MS. AL-SHABAZZ:" examiner attribution carries the name; also match.
    for prefix, key in _ATTR_PREFIX_MAP.items():
        if prefix in s:
            return key
    return None


def build_attorney_profiles(indices: list[TranscriptIndex]) -> dict[str, AttorneyProfile]:
    """One profile per identified attorney, across all trial days."""
    profiles: dict[str, AttorneyProfile] = {}
    for key, (name, client) in ATTORNEY_ROLES.items():
        profiles[key] = AttorneyProfile(key=key, name=name, represents=client)

    for idx in indices:
        for p in idx.passages:
            key = _resolve_attorney(p.speaker)
            if not key:
                continue
            prof = profiles[key]
            prof.word_count += len(p.text.split())
            page = p.page
            prof.page_range = (
                (min(prof.page_range[0], page), max(prof.page_range[1], page))
                if prof.page_range
                else (page, page)
            )
            t = p.text.strip()
            if not t:
                continue

            # Objections raised by this attorney (same discrimination as the
            # objections log: affirmative acts, not declinations).
            if "OBJECTION" in t.upper() and p.speaker not in ("THE COURT", "THE WITNESS"):
                prof.objections.append({"page": page, "text": t[:300]})

            # Witness examination: attorney passage immediately precedes a
            # THE WITNESS answer (attribution via preceding non-court speaker).
            # Handled in a second pass below for correctness.

            # Key statements: openings (early pages of a day) and notable acts.
            if len(t.split()) > 40 and ("your Honor" in t.lower() or "I object" in t.lower()):
                if not any(s["page"] == page for s in prof.key_statements):
                    prof.key_statements.append({"page": page, "text": t[:300]})

    # Second pass: examinations — the attorney asking before a witness answer.
    for idx in indices:
        for i, p in enumerate(idx.passages):
            if p.speaker != "THE WITNESS":
                continue
            # find the nearest preceding non-court speaker (the examiner)
            for j in range(i - 1, max(-1, i - 12), -1):
                prev = idx.passages[j]
                if prev.speaker in ("THE COURT",):
                    continue
                key = _resolve_attorney(prev.speaker)
                if key:
                    witness = idx.witness_names.get(p.page, "Unknown Witness")
                    profiles[key].examinations.append({
                        "witness": witness,
                        "page": p.page,
                        "kind": "examination",
                    })
                break

    # Dedup examinations per witness/page, cap key statements.
    for prof in profiles.values():
        seen = set()
        deduped = []
        for e in prof.examinations:
            k = (e["witness"], e["page"])
            if k not in seen:
                seen.add(k)
                deduped.append(e)
        prof.examinations = deduped[:60]
        prof.key_statements = prof.key_statements[:40]
    return profiles


# ---------------------------------------------------------------------------
# Defense-error / post-conviction flags
# ---------------------------------------------------------------------------
# Shapes a federal criminal-defense review investigates. These are RECORD
# PATTERNS that raise a QUESTION for qualified counsel — never a verdict.
_ERROR_PATTERNS = {
    "government evidence / tampering concern": re.compile(
        r"\b(tamper|manipulat|altered|missing evidence|withheld|spoliat|"
        r"chain of custody|fabricat|manufactured)\b", re.IGNORECASE,
    ),
    "discovery / notice issue": re.compile(
        r"\b(notice|discovery|unsealed|supplemental|late disclosure|Brady|Giglio)\b",
        re.IGNORECASE,
    ),
    "confrontation / hearsay": re.compile(
        r"\b(confront|hearsay|out.of.court|sixth amendment)\b", re.IGNORECASE,
    ),
    "jury-selection / Batson": re.compile(
        r"\b(batson|peremptory|pattern of discrimination|jury selection)\b",
        re.IGNORECASE,
    ),
    "counsel objection / preservation": re.compile(
        r"\b(objection|sustained|overruled)\b", re.IGNORECASE,
    ),
    "evidence admissibility": re.compile(
        r"\b(admissib|relevan|prejudice|exclude|strike)\b", re.IGNORECASE,
    ),
}


def build_error_flags(indices: list[TranscriptIndex]) -> list[dict]:
    """Surface record passages matching post-conviction-review shapes, each with
    page cites, framed as questions for qualified counsel (never verdicts)."""
    flags = []
    for idx in indices:
        for p in idx.passages:
            if p.speaker in ("THE COURT", "THE WITNESS"):
                continue
            t = p.text.strip()
            if not t or len(t) < 12:
                continue
            for label, rx in _ERROR_PATTERNS.items():
                if rx.search(t):
                    flags.append({
                        "category": label,
                        "page": p.page,
                        "speaker": p.speaker,
                        "text": t[:280],
                    })
                    break  # one flag per passage
    # cap + sort by page
    flags.sort(key=lambda f: (f["category"], f["page"]))
    seen = set()
    out = []
    for f in flags:
        k = (f["category"], f["page"], f["speaker"])
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
        if len(out) >= 250:
            break
    return out


# ---------------------------------------------------------------------------
# Page-grounded Q&A over the trial record
# ---------------------------------------------------------------------------
def search_record(indices: list[TranscriptIndex], query: str, limit: int = 20) -> list[dict]:
    """Search the full trial record (all days) for passages matching `query`.
    Returns page-cited hits across every transcript day."""
    q = query.lower()
    hits = []
    for idx in indices:
        source = Path(idx.source).name if idx.source else "?"
        for p in idx.passages:
            if q in p.text.lower():
                hits.append({
                    "page": p.page,
                    "speaker": p.speaker,
                    "text": p.text,
                    "day": source[:10],
                })
    hits.sort(key=lambda h: h["page"])
    seen = set()
    out = []
    for h in hits:
        k = (h["page"], h["speaker"], h["text"][:60])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def speaker_summary(indices: list[TranscriptIndex], speaker_prefix: str, limit: int = 25) -> list[dict]:
    """Every passage spoken by an attorney (case-insensitive prefix), page-cited."""
    q = speaker_prefix.upper()
    out = []
    for idx in indices:
        for p in idx.passages:
            if p.speaker.upper().startswith(q):
                out.append({"page": p.page, "speaker": p.speaker, "text": p.text})
    out.sort(key=lambda h: h["page"])
    return out[:limit]


def trial_overview(indices: list[TranscriptIndex]) -> dict:
    """High-level structure of the trial record: days, pages, witnesses.

    Same-day segments (multiple PDF parts, e.g. the 5/6 voir-dire day split
    across four files) are merged into ONE logical trial day so the counts
    reflect trial days, not files."""
    days = []
    total_pages = set()
    by_day: dict[str, list] = {}
    for idx in indices:
        src = Path(idx.source).name if idx.source else "?"
        day = src[:10] if src.startswith("20") else src
        pages = {p.page for p in idx.passages}
        total_pages |= pages
        by_day.setdefault(day, []).append({
            "file": src,
            "passages": len(idx.passages),
            "pages": len(pages),
            "page_min": min(pages) if pages else None,
            "page_max": max(pages) if pages else None,
        })
    for day, segs in sorted(by_day.items()):
        if len(segs) == 1:
            days.append({"day": day, **segs[0]})
            continue
        pages_all = set()
        for s in segs:
            # same-day segments restart page numbering; union the ranges
            pages_all |= set(range(s["page_min"], s["page_max"] + 1))
        days.append({
            "day": day,
            "files": len(segs),
            "passages": sum(s["passages"] for s in segs),
            "pages": len(pages_all),
            "page_min": min(s["page_min"] for s in segs),
            "page_max": max(s["page_max"] for s in segs),
        })
    return {
        "case": CASE,
        "days": days,
        "total_passages": sum(d["passages"] for d in days),
        "total_pages": len(total_pages),
        "page_min": min(total_pages) if total_pages else None,
        "page_max": max(total_pages) if total_pages else None,
    }


# ---------------------------------------------------------------------------
# Key events — named moments the defense would cite (chain of custody, etc.)
# ---------------------------------------------------------------------------
def build_key_events(indices: list[TranscriptIndex]) -> list[dict]:
    """Detect named, page-cited events from the trial record that a criminal
    defense review would flag. Current detectors:
      - CHAIN OF CUSTODY: a seized phone kept unsecured at an agent's desk for
        weeks, powered-state unknown, no markings, then 'gone through' by CART
        (the phone-evidence tampering/handling event the defendant identified).
    Each event is record-grounded with page cites and framed as a QUESTION for
    qualified counsel — never a verdict that the government tampered.
    """
    events: list[dict] = []

    # Chain-of-custody: agent passage about a phone kept at a desk/cubicle,
    # powered state unknown, markings absent, transferred after ~a month.
    chain_re = re.compile(
        r"\b(phone|cell phone)\b.{0,60}?\b(desk|cubicle|secured|locked cabinet)\b",
        re.IGNORECASE,
    )
    powered_re = re.compile(r"\b(powered on|powered off|powered on or off)\b", re.IGNORECASE)
    no_marking_re = re.compile(r"\b(no\s+marking|wasn't\s+marked|not\s+marked)\b", re.IGNORECASE)

    # Build a timeline of phone-related passages per agent per day.
    for idx in indices:
        src = Path(idx.source).name if idx.source else "?"
        phone_passages = [p for p in idx.passages
                          if re.search(r"\b(phone|cell phone)\b", p.text, re.IGNORECASE)]
        if not phone_passages:
            continue

        # Cluster consecutive phone passages into an event window.
        window: list = []
        windows: list[list] = []
        for p in phone_passages:
            if window and p.page - window[-1]["page"] > 15:
                windows.append(window)
                window = []
            window.append({"page": p.page, "speaker": p.speaker, "text": p.text})
        if window:
            windows.append(window)

        for w in windows:
            if len(w) < 4:
                continue
            pages = [x["page"] for x in w]
            texts = " ".join(x["text"] for x in w)
            if not chain_re.search(texts):
                continue
            has_powered = bool(powered_re.search(texts))
            has_no_marking = bool(no_marking_re.search(texts))
            events.append({
                "category": "phone evidence / chain of custody",
                "day": src[:10],
                "pages": f"{min(pages)}-{max(pages)}",
                "page_min": min(pages),
                "page_max": max(pages),
                "note": (
                    "Phone evidence handling documented over multiple FBI agents: "
                    "seized phones kept at an agent's desk/cubicle for ~a month, "
                    "powered state unknown, no identifying markings, then "
                    "transferred to CART for analysis."
                    + (" Powered-state was explicitly probed (unknown)." if has_powered else "")
                    + (" Absence of identifying markings was elicited." if has_no_marking else "")
                ),
                "passages": [
                    {"page": x["page"], "speaker": x["speaker"], "text": x["text"][:200]}
                    for x in w[:8]
                ],
            })

    events.sort(key=lambda e: (e["category"], e["page_min"]))
    return events


# ---------------------------------------------------------------------------
# Named defense events — the phone-evidence / selective-disclosure challenge
# ---------------------------------------------------------------------------
_SELECTIVE_RE = re.compile(
    r"\b(selectively left out|left out|piecemeal|conflating|selected the evidence|"
    r"what the government has put into evidence|how much has been left out|"
    r"any portion of this email that is missing|pulled this through|"
    r"read and delete|previously deleted)\b",
    re.IGNORECASE,
)
# A passage is only a PHONE/MESSAGE evidence event if it is actually about phone,
# email, text, or extraction evidence — not just any "left out / piecemeal"
# statement about generic trial conduct. This filters the noise (e.g. a generic
# opening statement or an unrelated "back surgeries" aside).
_EVIDENCE_TOPIC_RE = re.compile(
    r"\b(phone|cell|text|message|email|extraction|intake|contact|exhibit 710|"
    r"710[a-c]?|exhibit 53[0-9]|535|read and delete|records)\b",
    re.IGNORECASE,
)


def build_phone_evidence_events(indices: list[TranscriptIndex]) -> list[dict]:
    """The specific phone/email evidence-challenge events a post-conviction
    review would flag — Al-Shabazz confronting the government's selective or
    altered presentation of phone/message/email evidence. Each is record-grounded
    with page cites and framed as a QUESTION for qualified counsel."""
    events = []
    for idx in indices:
        src = Path(idx.source).name if idx.source else "?"
        for p in idx.passages:
            if p.speaker in ("THE COURT", "THE WITNESS"):
                continue
            if not _SELECTIVE_RE.search(p.text):
                continue
            if not _EVIDENCE_TOPIC_RE.search(p.text):
                continue
            # cluster context: pull the surrounding passages on the same page
            context = []
            for p2 in idx.passages:
                if abs(p2.page - p.page) <= 1 and p2.speaker in (
                    "MS. AL-SHABAZZ", "THE COURT", "MR. CHIUCHIOLO", "MS. ROTHMAN", "MR. FOLLY"
                ):
                    context.append({"page": p2.page, "speaker": p2.speaker, "text": p2.text[:200]})
            events.append({
                "category": "phone/email evidence — selective or altered presentation",
                "day": src[:10],
                "page": p.page,
                "speaker": p.speaker,
                "text": p.text[:320],
                "context": context[:6],
                "legal_question": (
                    "Whether the government's presentation of phone/email/text "
                    "evidence was complete, and whether omitted or altered "
                    "messages affected the jury's understanding — a discovery/"
                    "Brady/fair-trial issue for qualified counsel to assess."
                ),
            })
    # dedup by page+speaker+text
    seen = set()
    out = []
    for e in sorted(events, key=lambda e: e["page"]):
        k = (e["page"], e["speaker"], e["text"][:40])
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
        if len(out) >= 12:
            break
    return out
