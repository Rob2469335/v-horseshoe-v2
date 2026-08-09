from __future__ import annotations

from pathlib import Path
from swarm_os.services.legal.transcript_search import parse_transcript
from swarm_os.services.legal.transcript_analysis import build_analysis

def test_build_analysis_comprehensive(tmp_path: Path):
    transcript_text = """
SOUTHERN DISTRICT REPORTERS, P.C.


620
              J59TDUN2
    1             (At sidebar)
    2             THE COURT:  We will proceed.
    3    BY MS. AL-SHABAZZ:
    4    Q.  Did you see the car?
    5    A.  Yes, I did.
    6    MR. FOLLY:  Objection, leading.
    7    THE COURT:  I'll allow it.
    8    Q.  Can you describe the car in detail so the jury can understand what happened on that night?
    9    A.  It was a very distinctive blue sedan with a dent on the front bumper and custom rims.
    10   MR. FOLLY:  Objection, narrative.
    11   MS. AL-SHABAZZ:  He is just answering the question.
    12   THE COURT:  Sustained. Strike that.
    13   THE COURT:  I instruct the jury to disregard that last statement.
SOUTHERN DISTRICT REPORTERS, P.C.


621
              J59TDUN2
    1    MR. FOLLY:  We raise a Batson challenge. The defense is using
    2    peremptory strikes in a pattern of discrimination.
    3    THE COURT:  Denied.
    4    MR. FOLLY:  Thank you, we adjourn for the day.
"""
    idx = parse_transcript(transcript_text, case="Test Case", source="test.txt")
    out_file = tmp_path / "report.md"
    build_analysis([idx], str(out_file))
    
    assert out_file.exists()
    content = out_file.read_text("utf-8")
    
    # Check that basic formatting and labels exist
    assert "## Day 1 (test.txt) - Chronology" in content
    
    # Chronology checks
    assert "Examination by MS. AL-SHABAZZ begins (Page 620)" in content
    assert "Objection by MR. FOLLY (Page 620)" in content
    assert "Ruling: Overruled (Allowed) by THE COURT (Page 620)" in content
    assert "Ruling: Sustained (Stricken) by THE COURT (Page 620)" in content
    assert "Jury Instructions / Charge" in content
    assert "Adjournment/Recess mentioned by MR. FOLLY (Page 621)" in content
    
    # Witness Matrix checks
    assert "Unnamed Witness" in content
    assert "MS. AL-SHABAZZ" in content
    # The summary should extract the longest answer
    assert "distinctive blue sedan" in content
    assert "not assessed; see page 620" in content
    
    # Objections Log checks - pending objection mapping
    assert "Objection** by MR. FOLLY on Page 620" in content
    assert "Objection, leading." in content
    assert "Overruled (Allowed) (Page 620)" in content
    assert "Objection, narrative." in content
    assert "Sustained / Sustained (Stricken) (Page 620)" in content
    
    # Batson Challenge Pass checks
    assert "Batson doctrine" in content
    assert "MR. FOLLY" in content
    assert "peremptory strikes in a pattern of discrimination" in content
    assert "not assessed; see page 621" in content
    
def test_false_positives(tmp_path: Path):
    transcript_text = """
SOUTHERN DISTRICT REPORTERS, P.C.
(212) 805-0300

100
              J59TDUN2
    1    BY MR. SMITH:
    2    Q.  Did you know Mr. Batson?
    3    A.  Yes, I did. I was in charge of him.
    4    MR. SMITH:  Objection noted.
    5    THE COURT:  Thank you.
"""
    idx = parse_transcript(transcript_text, case="Test", source="test.txt")
    # Sanity: the footer must NOT swallow the page number — passages must parse.
    assert idx.passages, "fixture must produce passages (page number survived)"
    out_file = tmp_path / "report.md"
    build_analysis([idx], str(out_file))
    
    content = out_file.read_text("utf-8")
    # "Mr. Batson" (a person) must NOT trigger the Batson challenge pass.
    assert "No Batson challenge detected." in content
    # "in charge of him" must NOT trigger Jury Instructions.
    assert "Jury Instructions / Charge" not in content
    # "Objection noted" spoken by MR. SMITH must not be logged as a ruling.
    assert "Ruling:" not in content
    assert "No explicit ruling found nearby" in content


def test_batson_challenge_detected_from_court_speaker(tmp_path: Path):
    """A Batson challenge ACKNOWLEDGED BY THE COURT is the most on-point
    passage — it must be detected, not filtered out. (Regression for the audit
    finding: excluding THE COURT hid the real May 7 p.31 acknowledgment.)"""
    transcript_text = """
SOUTHERN DISTRICT REPORTERS, P.C.
(212) 805-0300

31
              J59TDUN2
    1    THE COURT:  Yes, ma'am.  And I should note for the record
    2    that in light of the Batson challenge, it seems appropriate
    3    to take up the issue now.
"""
    idx = parse_transcript(transcript_text, case="Test", source="test.txt")
    assert idx.passages, "fixture must produce passages"
    out_file = tmp_path / "report.md"
    build_analysis([idx], str(out_file))
    content = out_file.read_text("utf-8")
    assert "Batson challenge detected" not in content
    assert "THE COURT" in content
    assert "in light of the Batson challenge" in content
    assert "not assessed" in content


def _page_block(page: int, vol: str, header: str, lines: list[str]) -> str:
    """A single SDNY-format page: number + (optional) named page-header +
    content lines. Used to build named-header fixtures for the matrix tests."""
    hdr = f"              {vol}" + (f"                 {header}" if header else "") + "\n"
    body = "".join(f"    {i+1:<4} {ln}\n" for i, ln in enumerate(lines))
    return f"\n{page}\n{hdr}\n{body}\nSOUTHERN DISTRICT REPORTERS, P.C.\n(212) 805-0300\n"


def test_witness_matrix_groups_by_name_across_gaps(tmp_path: Path):
    """One witness (Mueller) answers, then falls silent for 60+ passages of
    court colloquy, then answers again — SAME witness, must be ONE matrix row
    named "Mueller". The old >50-passage gap heuristic split this real
    transcript shape into ~40 "Unnamed Witness" rows."""
    filler = ["THE COURT:  Proceed, counsel." for _ in range(55)]
    text = (
        _page_block(620, "J5ATDUN1", "Mueller - Cross", [
            "MS. AL-SHABAZZ:  Did you see the car?",
            "THE WITNESS:  Yes, I did.",
            "THE COURT:  Thank you.",
        ])
        + _page_block(630, "J5ATDUN1", "Mueller - Cross", filler)
        + _page_block(631, "J5ATDUN1", "Mueller - Cross", [
            "MS. AL-SHABAZZ:  What color was it?",
            "THE WITNESS:  A distinctive blue sedan.",
            "THE COURT:  Continue.",
        ])
    )
    idx = parse_transcript(text, case="Test", source="test.txt")
    assert idx.witness_names == {620: "Mueller", 630: "Mueller", 631: "Mueller"}
    out_file = tmp_path / "report.md"
    build_analysis([idx], str(out_file))
    content = out_file.read_text("utf-8")

    wm = content.split("Witness Matrix")[1].split("Objections")[0]
    # ONE named row spanning both answers — NOT fragmented, NOT unnamed.
    assert wm.count("| Mueller |") == 1
    assert "| Mueller | 620-631 |" in wm
    assert "Unnamed Witness" not in wm


def test_witness_matrix_separates_distinct_witnesses_by_name(tmp_path: Path):
    """A different witness name in the page header starts a NEW matrix row —
    identity is the page-header name, not silence."""
    text = (
        _page_block(620, "J5ATDUN1", "Mueller - Cross", [
            "MS. AL-SHABAZZ:  Did you see the car?",
            "THE WITNESS:  Yes, I did.",
            "THE COURT:  Thank you.",
        ])
        + _page_block(640, "J5ATDUN1", "Dewitt - Direct", [
            "MR. FOLLY:  And then what happened?",
            "THE WITNESS:  We left the building.",
            "THE COURT:  Proceed.",
        ])
    )
    idx = parse_transcript(text, case="Test", source="test.txt")
    assert idx.witness_names == {620: "Mueller", 640: "Dewitt"}
    out_file = tmp_path / "report.md"
    build_analysis([idx], str(out_file))
    content = out_file.read_text("utf-8")

    wm = content.split("Witness Matrix")[1].split("Objections")[0]
    assert wm.count("| Mueller |") == 1
    assert wm.count("| Dewitt |") == 1
    assert "| Mueller | 620-620 |" in wm
    assert "| Dewitt | 640-640 |" in wm
    assert "Unnamed Witness" not in wm


def test_witness_matrix_unnamed_fallback_keeps_gap_split(tmp_path: Path):
    """Transcripts with NO page-header names (e.g. the J59TDUN2-only fixtures)
    fall back to "Unnamed Witness" and the >50-passage gap heuristic — the
    behavior the comprehensive test pins."""
    filler = ["THE COURT:  Proceed, counsel." for _ in range(55)]
    text = (
        _page_block(620, "J59TDUN2", "", [
            "MS. AL-SHABAZZ:  Did you see the car?",
            "THE WITNESS:  Yes, I did.",
            "THE COURT:  Thank you.",
        ])
        + _page_block(630, "J59TDUN2", "", filler)
        + _page_block(631, "J59TDUN2", "", [
            "MR. FOLLY:  And then what?",
            "THE WITNESS:  A blue sedan.",
            "THE COURT:  Continue.",
        ])
    )
    idx = parse_transcript(text, case="Test", source="test.txt")
    assert idx.witness_names == {}
    out_file = tmp_path / "report.md"
    build_analysis([idx], str(out_file))
    content = out_file.read_text("utf-8")

    wm = content.split("Witness Matrix")[1].split("Objections")[0]
    assert wm.count("| Unnamed Witness |") == 2  # gap split still applies
    assert "Unnamed Witness" in wm
