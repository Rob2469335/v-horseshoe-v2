"""Tests for transcript_search.py — offline speaker/passage extraction from a
court-reporter-format transcript.

The critical behavior pinned here: during an examination block opened by a
"BY MS. AL-SHABAZZ:" header, her ACTUAL questions are "Q." lines — attributed
to her, not to a literal "MS. AL-SHABAZZ:" prefix. A naive text search would
miss her whole cross-examination.
"""
from __future__ import annotations

from swarm_os.services.legal.transcript_search import parse_transcript

_FOOTER = "SOUTHERN DISTRICT REPORTERS, P.C.\n(212) 805-0300"


def _page(n: int, body: str) -> str:
    return f"\n{n}\nJ591dun1\n\n{body}\n\n{_FOOTER}\n"


SAMPLE = (
    _page(499, (
        "1    THE COURT:  Good morning.  All the jurors are here.\n"
        "2    MR. FOLLY:  Yes, your Honor.\n"
        "3    THE COURT:  Please be seated.\n"
    ))
    + _page(502, (
        "1    CROSS-EXAMINATION\n"
        "2    BY MS. AL-SHABAZZ:\n"
        "3    Q.  Good morning, Ms. Nichols.\n"
        "4    A.  Good morning.\n"
        "5    Q.  You met Reginald Dewitt in October of 2012, right?\n"
        "6    A.  No, I did not.\n"
        "7    (Jury present)\n"
        "8    THE COURT:  All right.  Thank you.\n"
    ))
    + _page(506, (
        "1    CROSS-EXAMINATION\n"
        "2    BY MR. CECUTTI:\n"
        "3    Q.  Now Robert -- you know Robert, right?\n"
        "4    A.  Yes.\n"
        "5    MS. AL-SHABAZZ:  Objection.\n"
        "6    THE COURT:  Overruled.\n"
    ))
    + _page(533, (
        "1    REDIRECT EXAMINATION\n"
        "2    BY MR. CHIUCHIOLO:\n"
        "3    Q.  Who told you to falsely claim you fell?\n"
        "4    A.  This was Bryan.\n"
    ))
)


def test_parses_pages_and_page_numbers():
    idx = parse_transcript(SAMPLE, case="US v. Test")
    pages = sorted({p.page for p in idx.passages})
    assert pages == [499, 502, 506, 533]


def test_speaker_passages_include_q_lines_under_by_header():
    """The core requirement: AL-SHABAZZ's Q. lines under her BY header are
    attributed to her — a naive 'MS. AL-SHABAZZ:' grep would miss them."""
    idx = parse_transcript(SAMPLE)
    passages = idx.speaker_passages("AL-SHABAZZ")
    texts = [p.text for p in passages]
    assert any("Good morning, Ms. Nichols" in t for t in texts)
    assert any("met Reginald Dewitt" in t for t in texts)
    assert any("Objection" in t for t in texts)          # literal prefix line
    assert any("falsely claim you fell" not in t for t in texts)  # Chiuciolo's


def test_q_lines_do_not_leak_to_next_examiner():
    idx = parse_transcript(SAMPLE)
    # CECUTTI's cross starts after AL-SHABAZZ's — her examiner state must not leak.
    cecutti = idx.speaker_passages("CECUTTI")
    assert [p.text for p in cecutti] == ["Now Robert -- you know Robert, right?"]


def test_witness_answers_attributed_to_witness():
    idx = parse_transcript(SAMPLE)
    witness = idx.speaker_passages("THE WITNESS")
    assert any(p.text.rstrip(".") == "Good morning" for p in witness)
    assert any(p.text == "Yes." for p in witness)


def test_speaker_pages_and_search():
    idx = parse_transcript(SAMPLE)
    assert idx.speaker_pages("AL-SHABAZZ") == [502, 506]
    hits = idx.search("Reginald Dewitt")
    assert hits and hits[0][0] == 502


def test_stage_directions_do_not_become_passages():
    idx = parse_transcript(SAMPLE)
    assert not any("Jury present" in p.text for p in idx.passages)


def test_real_format_multiline_and_headers():
    """A slice in the actual SDNY layout: multi-line utterances join into one
    passage and section headers never leak into the preceding speaker's line."""
    real = (
        "\n501\nJ591dun1\n\n"
        "        1             THE COURT:  Now Ms. Al-Shabazz is on for\n"
        "        2    cross-examination, correct?\n"
        "        3             MS. AL-SHABAZZ:  No.  I believe it is Mr. Cecutti.\n"
        "        4             THE COURT:  And then you?\n"
        "        5             MS. AL-SHABAZZ:  Yes.\n"
        "\n\n" + _FOOTER + "\n"
        "\n506\nJ59TDUN2\n\n"
        "        1             THE COURT:  All right.  Thank you.\n"
        "        2    CROSS-EXAMINATION\n"
        "        3    BY MS. AL-SHABAZZ:\n"
        "        4    Q.  Good morning, Ms. Nichols.\n"
        "        5    A.  Good morning.\n"
    )
    idx = parse_transcript(real, case="US v. Test", source="real-format")
    assert idx.speaker_pages("AL-SHABAZZ") == [501, 506]
    al_shabazz = [p.text for p in idx.speaker_passages("AL-SHABAZZ")]
    assert any("No.  I believe it is Mr. Cecutti." in t for t in al_shabazz)
    assert any(t == "Good morning, Ms. Nichols." for t in al_shabazz)
    # The COURT's line must not absorb the CROSS-EXAMINATION header.
    court = [p.text for p in idx.speaker_passages("THE COURT")]
    assert any(t == "All right.  Thank you." for t in court)
    assert not any("CROSS-EXAMINATION" in t for t in court)


def test_page_header_line_is_stripped():
    """A page header line ("J5ATDUN1  Mueller - Cross") glued onto the end of a
    passage by a page break must be removed — otherwise the header's witness
    name / section label reads as if it were part of the preceding speech."""
    real = (
        "\n759\nJ5ATDUN1\n\n"
        "        1             MS. AL-SHABAZZ:  I just have a couple of questions.\n"
        "        2             THE COURT:  All right.  Thank you.\n"
        "\n\n" + _FOOTER + "\n"
        "\n760\nJ5ATDUN1                 Mueller - Cross\n\n"
        "        1             MS. AL-SHABAZZ:  Did they show you a photograph?\n"
    )
    idx = parse_transcript(real, case="US v. Test", source="real-header")
    al_shabazz = [p.text for p in idx.speaker_passages("AL-SHABAZZ")]
    # The header must NOT be glued to either her p.759 line or the p.760 line.
    assert all("Mueller" not in t for t in al_shabazz)
    assert any(t == "I just have a couple of questions." for t in al_shabazz)
    assert any(t == "Did they show you a photograph?" for t in al_shabazz)


def test_bare_name_line_boundary_is_flagged():
    """The known-gap dangerous shape — a bare colon-less speaker-name line
    merged onto the end of another speaker's passage — must be mechanically
    flagged (so the user knows the SPECIFIC spots to verify, not all of them)."""
    real = (
        "\n760\nJ5ATDUN1\n\n"
        "        1             MS. AL-SHABAZZ:  I just have a couple of questions.\n"
        "        2             THE COURT:  All right.\n"
        "        3             Ms. Al-Shabazz.\n"   # bare name line, no colon
        "\n\n" + _FOOTER + "\n"
        "\n761\nJ5ATDUN1\n\n"
        "        1             MS. AL-SHABAZZ:  Did they show you a photograph?\n"
    )
    idx = parse_transcript(real, case="US v. Test", source="real-flag")
    flagged = idx.flagged()
    assert any("Ms. Al-Shabazz" in f for p in flagged for f in p.flags)
    # The bare line is flagged on the passage it merged into (THE COURT's).
    court = [p for p in flagged if p.speaker == "THE COURT"]
    assert court and "Ms. Al-Shabazz" in court[0].text
