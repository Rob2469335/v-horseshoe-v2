"""Deterministic (subject, relation, object) supersession for governed lessons.

Why this exists
---------------
Retrieval cannot rank what it cannot distinguish. Measured on a calibrated
dataset, cosine similarity separates a *contradicted* fact from a *duplicated*
one at AUROC 0.59 — near chance — because a value-flip is a minimal edit and so
looks more like the original than a genuine paraphrase does (MemStrata,
arXiv:2606.26511). The same paper reports RAG serving a superseded value
15-40% of the time when asked to answer, and that this is structural, not tuned.

Outdated instruction is worse than none: with only stale content retrieved,
models become *confidently* wrong, whereas with nothing retrieved they abstain
(HoH, ACL 2025 / arXiv:2503.04800). So a stale lesson is actively harmful, and
the supersession decision must not depend on similarity.

Design
------
A governed lesson renders as ``"<condition>: <action>"`` (see
``prompt_repairer._derive_learner_artifact``). That is already a
(subject, relation, object) triple:

    subject   = the condition under which the lesson applies
    relation  = the governing directive, WITHOUT its polarity
    object    = everything the directive applies to

So ``"call-loop: use ripgrep for searching"`` parses to::

    subject   = "call loop"
    relation  = "use"                  (the directive verb)
    object    = "ripgrep for searching" (what it applies to)

and ``"no-edit: never use the write tool"`` parses to subject ``"no edit"``,
relation ``"use"``, object ``"write tool"``, ``negated=True``.

That normalization is the point. Two lessons share a slot when they share
(subject, relation). Within a slot:

1. **Opposite polarity** — a negated rule and its affirmative counterpart are
   mutually exclusive and cannot both be right. The *negated* one is the
   corrective instruction, so it always wins, regardless of age. This is the
   dangerous stale case: an instruction that actively contradicts a newer fix.
   Deciding it by timestamp would be wrong whenever the corrective rule was
   learned first.
2. **Same polarity, different object** — a prescription was replaced (a tool
   was swapped, an argument was changed). Governed age decides.
3. **Identical triple** — a duplicate, which the existing Jaccard dedupe at
   promotion already owns. Not a supersession.

Anything else — different subject, different directive, or an unparseable rule
— yields no verdict. Fail-safe: an unparseable lesson is left alone, never
retired on a hunch.

Governance properties
---------------------
* **Deterministic**: no model, no embedding, no clock, no randomness.
* **Fail-safe**: an unparseable rule yields no verdict.
* **Non-destructive**: this decides *which* lesson is stale; it never deletes.
  The caller records the verdict and sets ``superseded_by``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Polarity markers, checked against the action text.
_NEGATION_TOKENS = (
    "never",
    "not ",
    "don't",
    "dont",
    "avoid",
    "instead of",
    "rather than",
    "must not",
    "do not",
    "stop ",
    "no longer",
)

# Directive verbs. The FIRST of these in the action is the relation head; every
# other content word belongs to the object. Keeping this list small and closed
# is what makes the relation comparable across lessons.
_DIRECTIVE_VERBS = frozenset(
    {
        "use", "try", "prefer", "return", "call", "run", "check", "read",
        "write", "set", "get", "make", "apply", "invoke", "pass", "send",
        "choose", "select", "switch", "reuse", "defer", "skip", "avoid",
        "do", "keep", "stop", "start", "load", "save", "build", "install",
    }
)

# Closed class words: never part of a relation or object payload.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "to", "always", "also", "then", "so", "and", "but",
        "you", "it", "that", "this", "there", "here", "when", "if", "for",
        "from", "with", "in", "on", "at", "by", "as", "or", "any", "each",
        "every", "all", "is", "are", "was", "were", "should", "shall", "can",
        "may", "must", "will", "would", "could", "have", "has", "had",
        "first", "before", "after", "then", "into", "over", "up", "out",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class SRO:
    """A parsed lesson triple.

    Slots are empty strings when the rule could not be parsed into them;
    ``complete`` is False in that case and the caller must not act.
    """

    subject: str
    relation: str
    object: str
    negated: bool

    @property
    def complete(self) -> bool:
        return bool(self.subject and self.relation and self.object)

    @property
    def key(self) -> tuple[str, str]:
        """Identity of the *slot* this lesson fills, ignoring the value.

        Polarity is deliberately excluded: a corrective rule and the rule it
        corrects must land in the same slot for supersession to see them.
        """
        return (self.subject, self.relation)


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def parse_sro(rule: str) -> SRO:
    """Split a governed lesson into (subject, relation, object).

    ``"<condition>: <action>"`` is the governed form. A rule with no colon has
    no subject, so its triple is incomplete and yields no verdict — fail-safe,
    never a guess.
    """
    raw = (rule or "").strip()
    if not raw:
        return SRO("", "", "", False)

    if ":" in raw:
        head, _, tail = raw.partition(":")
        subject = _normalize(head)
        action = tail.strip()
    else:
        subject = ""
        action = raw

    negated = any(tok in action.lower() for tok in _NEGATION_TOKENS)

    words = _normalize(action).split()
    relation = ""
    for w in words:
        if w in _DIRECTIVE_VERBS:
            relation = w
            break
    if not relation:
        # No recognized directive → incomplete, no verdict.
        return SRO(subject=subject, relation="", object="", negated=negated)

    # Object = every content word except the directive head and any polarity
    # marker. Stripping the negation keeps "use the write tool" and "never use
    # the write tool" at the SAME value, so their difference is polarity alone.
    consumed = {relation}
    for tok in _NEGATION_TOKENS:
        consumed.update(_WORD_RE.findall(tok))
    obj = " ".join(w for w in words if w not in consumed and w not in _STOPWORDS)
    return SRO(subject=subject, relation=relation, object=obj, negated=negated)


def supersedes(candidate: str, incumbent: str, *, candidate_newer: bool) -> bool:
    """True when ``candidate`` deterministically retires ``incumbent``.

    ``candidate_newer`` is supplied by the caller from governed state
    (``created_at``, tie-broken by ``id``). This function never consults a
    clock, so it stays a pure predicate.

    Rule 1 — polarity correction wins regardless of age: a negated rule and its
    affirmative counterpart are mutually exclusive, so exactly one is stale and
    the corrective (negated) one survives. Age must not decide this, because a
    corrective rule can legitimately be learned before the behaviour it
    corrects recurs.

    Rule 2 — same slot and polarity, different value → governed age decides.
    """
    new = parse_sro(candidate)
    old = parse_sro(incumbent)
    if not new.complete or not old.complete:
        return False  # fail-safe: never retire what we cannot reason about

    if new.key != old.key:
        return False  # different slots are independent

    # Rule 1: opposite polarity in the same slot. Checked BEFORE the duplicate
    # test, because a corrective rule and the rule it corrects carry the SAME
    # normalized value and differ only in polarity.
    if new.negated != old.negated:
        return True

    if new.object == old.object:
        return False  # identical triple -> duplicate, not supersession

    # Rule 2: same polarity, replaced prescription -> governed age decides.
    return bool(candidate_newer)
