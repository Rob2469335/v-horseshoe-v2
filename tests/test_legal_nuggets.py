"""Tests for transcript fact-nuggets + declarative canonicalization
(swarm_os/services/legal/nuggets.py).

The research-backed guarantee: a transcript summary's value is verifiability,
not generation. Nuggets make every summary claim traceable to a page; QA pairs
are canonicalized to declarative statements. All deterministic — no LLM.
"""

from __future__ import annotations

from swarm_os.services.legal.nuggets import (
    build_nuggets,
    sentence_support,
    _is_fragment,
    _answer_to_declarative,
)
from swarm_os.services.legal.transcript_search import Passage


def _p(speaker, page, text, kind="spoken", i=0):
    return Passage(speaker=speaker, page=page, text=text, kind=kind)


def test_fragment_detection():
    assert _is_fragment("Yes.")
    assert _is_fragment("No")
    assert _is_fragment("Uh-huh.")
    assert not _is_fragment("I met him in October.")


def test_answer_to_declarative_full_sentence_lead_stripped():
    # "Yes, I met him." -> lead stripped -> "I met him."
    out = _answer_to_declarative(
        "Did you meet Reginald Dewitt?",
        "Yes, I met him in October of 2012.",
    )
    assert "I met him in October of 2012" in out


def test_answer_to_declarative_bare_confirmation_uses_question_verb():
    # Bare "Yes." to "Did you meet him?" -> "the witness did meet him"
    out = _answer_to_declarative("Did you meet him?", "Yes.")
    assert "meet" in out
    assert "did" in out


def test_build_nuggets_splits_sentences_with_page_anchors():
    passages = [
        _p(
            "MS. AL-SHABAZZ",
            503,
            "You met him in October of 2012, right? You were at his house.",
            kind="q",
        ),
    ]
    idx = build_nuggets(passages)
    # "You were at his house." is a full sentence nugget; the leading question
    # is a substantive sentence too.
    texts = [n.text for n in idx.nuggets]
    assert any("at his house" in t for t in texts)
    assert any(n.page == 503 for n in idx.nuggets), "nugget must carry its page"


def test_build_nuggets_canonicalizes_qa_pair():
    q = _p("MS. AL-SHABAZZ", 503, "Did you meet him in October of 2012?", kind="q")
    a = _p("THE WITNESS", 503, "Yes, I did meet him then.", kind="a")
    idx = build_nuggets([q, a])
    assert idx.declarative_statements, "a Q/A pair must yield a declarative statement"
    joined = " ".join(idx.declarative_statements)
    assert "p.503" in joined
    assert "meet" in joined


def test_sentence_support_maps_summary_sentence_to_nugget():
    passages = [
        _p(
            "THE WITNESS",
            503,
            "I met Reginald Dewitt in October of 2012. He was my landlord.",
        ),
    ]
    idx = build_nuggets(passages)
    support = sentence_support("The witness met Reginald Dewitt in October 2012.", idx)
    assert support, "a faithful summary sentence must map to >= 1 nugget"
    assert all(n.page == 503 for n in support)


def test_sentence_support_returns_empty_for_unverifiable_sentence():
    idx = build_nuggets(
        [
            _p("THE WITNESS", 503, "I met Reginald Dewitt in October of 2012."),
        ]
    )
    support = sentence_support("The defendant confessed on television.", idx)
    assert support == [], "an unsupported summary sentence must have no nugget anchor"
