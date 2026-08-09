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
    7    THE COURT:  Sustained.
    8    Q.  What did you see?
    9    A.  A blue car on the street.
SOUTHERN DISTRICT REPORTERS, P.C.


621
              J59TDUN2
    1    MR. FOLLY:  We raise a Batson challenge. The defense is using
    2    peremptory strikes in a pattern of discrimination.
    3    THE COURT:  Overruled.
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
    assert "Ruling: Sustained by THE COURT (Page 620)" in content
    assert "Adjournment/Recess mentioned by MR. FOLLY (Page 621)" in content
    
    # Witness Matrix checks
    assert "Unnamed Witness" in content
    assert "MS. AL-SHABAZZ" in content
    assert "not assessed; see page 620" in content
    
    # Objections Log checks
    assert "Objection** by MR. FOLLY on Page 620" in content
    assert "Objection, leading." in content
    assert "Sustained (Page 620)" in content
    assert "not assessed; see page 620" in content
    
    # Batson Challenge Pass checks
    assert "Batson challenge involves" in content
    assert "MR. FOLLY" in content
    assert "peremptory strikes in a pattern of discrimination" in content
    assert "not assessed; see page 621" in content
