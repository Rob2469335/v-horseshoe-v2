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


def test_page_bleed_regression():
    """A page's FINAL speaker line must keep that page's number, not inherit
    the next page's — the parser must attribute by where the passage STARTED,
    not by the page of the NEXT block (which lazily flushes the prior buffer).
    Real shape: p.499's last line is a speaker line; p.502 starts a new section."""
    idx = parse_transcript(SAMPLE, case="US v. Test")
    court = idx.speaker_passages("THE COURT")
    seated = [p for p in court if p.text == "Please be seated."]
    assert seated and seated[0].page == 499
    # The 502 block's own speaker line must be on 502, not pulled back to 499.
    greeting = [p for p in idx.speaker_passages("AL-SHABAZZ")
                if p.text == "Good morning, Ms. Nichols."]
    assert greeting and greeting[0].page == 502


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


def test_page_header_witness_name_harvested():
    """The reporter prints the testifying witness's name at the top of each
    page of testimony ("J5ATDUN1  Mueller - Cross"). That name must be
    harvested into idx.witness_names so the analysis layer can group passages
    by witness — not discarded as mere layout."""
    real = (
        "\n760\nJ5ATDUN1                 Mueller - Cross\n\n"
        "        1             MS. AL-SHABAZZ:  Did they show you a photograph?\n"
        "        2             THE WITNESS:  Yes, I did.\n"
        "\n\n" + _FOOTER + "\n"
        "\n770\nJ5ATDUN1                 Dewitt\n\n"
        "        1             MR. FOLLY:  And what happened next?\n"
    )
    idx = parse_transcript(real, case="US v. Test", source="real-header-names")
    assert idx.witness_names == {760: "Mueller", 770: "Dewitt"}


def test_page_header_witness_name_drops_section_labels():
    """The witness-name harvest must NOT capture section labels or the
    "J571dun1 - Corrected" transcript-fix header as a bogus witness."""
    real = (
        "\n501\nJ591dun1                 Direct\n\n"
        "        1             THE COURT:  Please be seated.\n"
        "\n\n" + _FOOTER + "\n"
        "\n502\nJ571dun1 - Corrected\n\n"
        "        1             THE COURT:  Thank you.\n"
        "\n\n" + _FOOTER + "\n"
        "\n503\nJ591dun1                 Mueller - Cross\n\n"
        "        1             THE COURT:  Continue.\n"
    )
    idx = parse_transcript(real, case="US v. Test", source="real-header-sections")
    # Only the genuine witness header (with a name + " - Cross") is harvested.
    assert idx.witness_names == {503: "Mueller"}


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


# --- BLT lesson (2311.09693): verbatim transcript lookup is DETERMINISTIC -----
# The BLT paper found public LLMs are poor at "look up the text at line N of a
# deposition". Our transcript parser does this deterministically (never the
# LLM). These tests PIN that guarantee: exact line lookup, line-number-column
# stripping, and page attribution — so a quote in a motion is structurally
# traceable to the source page.

def test_blt_exact_line_lookup_is_deterministic():
    """'Look up the text at line N' must be a deterministic string op — the same
    input always yields the same exact quote, with the line-number column
    stripped and the page attributed correctly."""
    body = (
        "1    MS. AL-SHABAZZ:  You met Reginald Dewitt in October of 2012, right?\n"
        "2    A.  No, I did not.\n"
        "3    THE COURT:  All right.\n"
    )
    idx = parse_transcript(_page(503, body), case="US v. Test", source="blt")
    # The exact quote text must survive verbatim (no line-number prefix).
    hits = idx.search("You met Reginald Dewitt")
    assert hits, "the verbatim question text must be findable"
    page, text = hits[0]
    assert page == 503, "the quote must be attributed to its source page"
    assert "1" != text[:1], "the line-number column must be stripped from the quote"
    assert "You met Reginald Dewitt in October of 2012, right?" in text
    # Deterministic: searching again returns the identical quote.
    assert idx.search("You met Reginald Dewitt")[0] == (page, text)


def test_blt_verbatim_quote_not_truncated_or_normalized():
    """The verbatim lookup must NOT normalize whitespace/quotes — a motion quote
    must match the transcript byte-for-byte after only the line-number column
    strip (BLT: models collapse/spell-correct verbatim text)."""
    exact = "No, I did not."
    body = (
        "1    MS. AL-SHABAZZ:  Did you meet him?\n"
        "2    A.  " + exact + "\n"
    )
    idx = parse_transcript(_page(507, body), case="US v. Test", source="blt2")
    page, text = idx.search(exact)[0]
    assert exact in text
    assert text.strip() == exact, (
        "the answer text must be the verbatim quote, not an LLM paraphrase"
    )


def test_blt_search_scoped_to_correct_page_not_page_bleed():
    """BLT page attribution: the same phrase on two different pages must return
    BOTH page anchors (the page-bleed fix — a passage belongs to the page where
    it STARTED, never inherited from the next page's block)."""
    p1 = (
        "1    THE COURT:  Please be seated.\n"
        "2    MR. FOLLY:  Yes, your Honor.\n"
    )
    p2 = (
        "1    THE COURT:  All right.  Thank you.\n"
    )
    idx = parse_transcript(_page(499, p1) + _page(502, p2), case="US v. Test", source="blt3")
    pages = {p for p, _ in idx.search("All right")}
    assert 502 in pages
    # "Please be seated" was on page 499 — never re-stamped 502.
    assert idx.search("Please be seated")[0][0] == 499
    # The speaker label itself is not the search text; the SPOKEN text is.
    assert idx.search("THE COURT") == [], (
        "search matches passage TEXT, not the speaker label"
    )
