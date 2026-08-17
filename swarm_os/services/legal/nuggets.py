"""Transcript fact-nuggets + declarative canonicalization for Rob's Lawyer.

Research-grounded (Supporting Humans in Evaluating AI Summaries of Legal
Depositions, CHIIR'26; Aspect Classification for Legal Depositions 2009.04485):
a trial transcript's summaries get used in motions, and the blocker isn't
generation quality but VERIFIABILITY. This module makes every summary claim
traceable to transcript pages:

  1. NUGGETS — atomic fact units ("The defendant met the victim in October
     2012") deterministically split from each passage. Nuggets carry a
     (page, passage_index) anchor so a summary sentence can cite its source.
  2. DECLARATIVE CANONICALIZATION — the QA-pair → declarative-statement
     transform from the legal-deposition aspect-classification line: "Q. Did
     you meet him? A. Yes." becomes the canonical declarative "The witness met
     him." so downstream summaries are drawn from canonical statements, not
     raw Q/A.
  3. VERIFIABILITY — every summary sentence maps to >= 1 nugget (the ALCE
     per-sentence citation discipline, applied to transcript facts). A sentence
     with no nugget anchor is unverifiable.

Pure and deterministic — no LLM in the nugget core (the research supports a
deterministic core with optional LLM assist; we keep the core offline).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from swarm_os.services.legal.transcript_search import Passage

# A nugget is a sentence-like clause ending in terminal punctuation. The split
# is deterministic: terminal-punctuation boundaries, then strip empty/fragments.
_TERMINAL_RE = re.compile(r"(?<=[.!?])\s+")
# Ellipsis/fragment guard: a "nugget" that is only "Yes." / "No." / "Uh-huh."
# is not an atomic fact — it only means something in its QA context, and is
# handled by declarative canonicalization, not listed as a standalone nugget.
_FRAGMENT_ONLY_RE = re.compile(
    r"^(?:yes|no|okay|ok|uh-?huh|mm-?hmm|right|correct|i do|i did|i don'?t|"
    r"i didn'?t|i was|i wasn'?t|that'?s right|yeah|nope|yep)[.!?]?$",
    re.IGNORECASE,
)
# Answer-lead stripping: a declarative answer often starts with a confirmation
# filler ("Yes, I met him.") — strip it so the nugget is the substantive fact.
_ANSWER_LEAD_RE = re.compile(
    r"^(?:yes|no|okay|ok|yeah|right|correct|that'?s right|uh-?huh|absolutely|"
    r"certainly|of course|i do|i did|i don'?t|i didn'?t)\s*[,.]\s*",
    re.IGNORECASE,
)


@dataclass
class Nugget:
    """One atomic fact unit from a passage, with its source anchor."""

    text: str
    page: int
    passage_index: int
    speaker: str = ""


@dataclass
class NuggetIndex:
    """Every passage split into anchored nuggets, plus declarative statements."""

    nuggets: list[Nugget] = field(default_factory=list)
    # QA pairs canonicalized to declarative witness statements.
    declarative_statements: list[str] = field(default_factory=list)
    # page -> nugget texts (for page-scoped lookup).
    by_page: dict[int, list[str]] = field(default_factory=dict)


def _clean_nugget(text: str) -> str:
    """Strip line-number columns (already done at parse), collapse whitespace,
    strip surrounding quotes/parentheticals that are reporter stage directions."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = t.strip("\"'()[]")
    return t


def _split_sentences(text: str) -> list[str]:
    """Deterministic sentence split on terminal punctuation. Returns cleaned,
    non-empty fragments."""
    out = []
    for part in _TERMINAL_RE.split(text or ""):
        part = _clean_nugget(part)
        if part and part not in ("", "."):
            out.append(part)
    return out


def _is_fragment(part: str) -> bool:
    """A one-word confirmation/denial is not an atomic fact on its own — it
    only means something inside its QA context (handled by canonicalization)."""
    return bool(_FRAGMENT_ONLY_RE.fullmatch(part.strip().rstrip(".!?")))


def _answer_to_declarative(
    question: str, answer: str, witness: str = "the witness"
) -> str:
    """QA-pair -> declarative statement (the 2009.04485 canonical-form
    transform). 'Q. Did you meet him? A. Yes.' -> 'The witness met him.'
    Deterministic: strips the answer lead, and when the answer is a bare
    confirmation it echoes the question's main verb in declarative form.

    Not an LLM — a best-effort deterministic transform. When the answer is a
    full sentence (the common case) it is returned verbatim with the lead
    stripped. When it is a bare Yes/No, we synthesize '<witness> <verb> ...'
    from the question. Fragile transforms degrade to the raw answer."""
    a = _clean_nugget(answer or "")
    q = _clean_nugget(question or "")
    if not a:
        return ""
    a_no_lead = _ANSWER_LEAD_RE.sub("", a).strip()
    if a_no_lead and _is_fragment(a_no_lead) is False and a_no_lead != a:
        # Full declarative answer with a confirmation lead stripped.
        return f"{witness} {a_no_lead}" if a_no_lead[0].islower() else a_no_lead
    # Full-sentence answer (no lead to strip) — return verbatim, but if it's a
    # bare confirmation, build a declarative from the question's main verb.
    if _is_fragment(a):
        # Build a declarative from the question's main verb: "Did you meet
        # him?" -> auxiliary "did", subject "you" (-> the witness), verb
        # "meet". Capture the full verb phrase (verb + object) so the
        # declarative keeps the substance ("the witness did meet him").
        m = re.match(
            r"(?:did|do|does|was|were|is|are|have|has|had)\s+(\w+)\s+(.+?)[?.]?$",
            q,
            re.IGNORECASE,
        )
        if m:
            aux = m.group(0).split()[0].lower()
            subj, rest = m.group(1), m.group(2)
            negation = (
                " not"
                if a.lower().startswith(("no", "nope", "i don't", "i didn't"))
                else ""
            )
            # Map the question's 2nd-person subject to the witness; "you"/"your"
            # reference the witness in a deposition.
            if subj.lower() in ("you", "your"):
                subj = witness
            aux_decl = {
                "did": "did",
                "do": "does",
                "does": "does",
                "was": "was",
                "were": "was",
                "is": "is",
                "are": "is",
                "have": "has",
                "has": "has",
                "had": "had",
            }.get(aux, aux)
            return f"{subj} {aux_decl}{negation} {rest}".strip()
        return f"{witness} answered: {a}"
    return a_no_lead or a


def build_nuggets(passages: list[Passage], witness: str = "the witness") -> NuggetIndex:
    """Split every passage into anchored nuggets and canonical declarative
    statements. Deterministic; page anchors are the passage's page + index.

    QA handling: a Q. passage followed by an A. passage is canonicalized to a
    declarative witness statement (the QA-pair transform); the A. passage's
    substantive clauses also become standalone nuggets."""
    idx = NuggetIndex()
    for i, p in enumerate(passages):
        # Whole-passage nuggets: substantive sentences only (fragments like a
        # bare "Yes." are not standalone facts).
        sentences = _split_sentences(p.text)
        for s in sentences:
            if _is_fragment(s):
                continue
            nug = Nugget(text=s, page=p.page, passage_index=i, speaker=p.speaker)
            idx.nuggets.append(nug)
            idx.by_page.setdefault(p.page, []).append(s)
        # QA-pair canonicalization: an A. passage is declarative-ized against
        # its preceding Q. (the immediately prior passage, if it's a question).
        if p.kind == "a" and i > 0 and passages[i - 1].kind == "q":
            q_pass = passages[i - 1]
            decl = _answer_to_declarative(q_pass.text, p.text, witness=witness)
            if decl:
                idx.declarative_statements.append(f"[p.{p.page}] {decl}")
    return idx


def sentence_support(
    sentence: str, index: NuggetIndex, threshold: float = 0.6
) -> list[Nugget]:
    """Return the nuggets supporting a summary sentence (token-overlap based,
    deterministic). A sentence is verifiable iff it maps to >= 1 nugget. This
    is the transcript analog of ALCE per-sentence citation support."""
    if not sentence or not index.nuggets:
        return []
    sent_tokens = set(re.findall(r"[a-z0-9']+", sentence.lower()))
    if not sent_tokens:
        return []
    scored: list[tuple[float, Nugget]] = []
    for nug in index.nuggets:
        nug_tokens = set(re.findall(r"[a-z0-9']+", nug.text.lower()))
        if not nug_tokens:
            continue
        overlap = len(sent_tokens & nug_tokens) / max(1, len(sent_tokens))
        if overlap >= threshold:
            scored.append((overlap, nug))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [n for _, n in scored]
