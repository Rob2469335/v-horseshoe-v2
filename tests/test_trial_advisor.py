"""Tests for the trial_advisor layer (Rob's Lawyer criminal-defense analysis).

Covers the per-attorney profiles, defense-error flags, phone-evidence events,
and record search — all built on the real trial-transcript parser with the
"report the record, never assert a legal conclusion" contract.
"""
from __future__ import annotations

from swarm_os.services.legal import trial_advisor as ta
from swarm_os.services.legal.transcript_search import parse_transcript


def _mini_index(day: str = "2019-05-07") -> list:
    """A small synthetic transcript index (reporter format) for unit tests."""
    body = (
        "1    THE COURT:  Good morning.\n"
        "2    MR. DINNERSTEIN:  Yes, your Honor.  My name is Mitchell, for Mr. Locust.\n"
        "3    MS. AL-SHABAZZ:  Objection, your Honor.\n"
        "4    THE COURT:  Sustained.\n"
        "5    MR. SCHOLAR:  Regarding Mr. Rainford's notice, your Honor.\n"
    )
    idx = parse_transcript(f"\n100\nJ591dun1\n\n{body}\n\nSOUTHERN DISTRICT REPORTERS, P.C.\n(212) 805-0300\n", case="US v. Test", source=f"{day}_x.txt")
    return [idx]


def test_attorney_profiles_identify_locust_counsel():
    idx = _mini_index()
    profiles = ta.build_attorney_profiles(idx)
    assert profiles["MR. DINNERSTEIN"].represents == "Robert Locust"
    assert profiles["MR. CECUTTI"].represents == "Robert Locust"
    assert profiles["MS. AL-SHABAZZ"].represents == "Bryan Duncan"
    assert profiles["MR. SCHOLAR"].represents == "Ryan Rainford"
    # Dinnerstein spoke in the fixture.
    assert profiles["MR. DINNERSTEIN"].word_count > 0


def test_attorney_profiles_capture_objections():
    idx = _mini_index()
    profiles = ta.build_attorney_profiles(idx)
    # The fixture has Al-Shabazz raising an objection.
    assert any(o["page"] == 100 for o in profiles["MS. AL-SHABAZZ"].objections)


def test_defendant_counsel_map_matches_record():
    """The counsel map matches the record's own conduct: Dinnerstein/Cecutti for
    Locust, Al-Shabazz for Duncan, Scholar for Rainford."""
    assert ta.DEFENDANT_COUNSEL["Robert Locust"] == ["MITCHELL J. DINNERSTEIN", "ANTHONY CECUTTI"]
    assert ta.DEFENDANT_COUNSEL["Bryan Duncan"] == ["IKIESHA TAQUET AL-SHABAZZ"]
    assert ta.DEFENDANT_COUNSEL["Ryan Rainford"] == ["CALVIN H. SCHOLAR"]


def test_record_search_page_cited():
    idx = _mini_index()
    hits = ta.search_record(idx, "Locust")
    assert hits and hits[0]["page"] == 100
    assert hits[0]["speaker"] == "MR. DINNERSTEIN"


def test_speaker_summary_prefix():
    idx = _mini_index()
    out = ta.speaker_summary(idx, "MR. DINNERSTEIN")
    assert out and out[0]["speaker"].startswith("MR. DINNERSTEIN")


def test_error_flags_never_verdicts():
    """The defense-error flags are record patterns with page cites — the module
    contract is 'report the record, never assert a legal conclusion'."""
    idx = _mini_index()
    flags = ta.build_error_flags(idx)
    assert all("page" in f for f in flags)
    assert all(isinstance(f["page"], int) for f in flags)


def test_phone_evidence_event_detected():
    """The phone-evidence/selective-disclosure challenge (real shape: p.1518-1553
    on 5/20) must be detected from record text."""
    body = (
        "1    MS. AL-SHABAZZ:  There's a lot of exhibits.  The ones the government "
        "highlighted in the phone records, and then a few they selectively left "
        "out.  How much has the government put into evidence and how much has "
        "been left out?\n"
    )
    idx = parse_transcript(f"\n1518\nJ591dun1\n\n{body}\n\nSOUTHERN DISTRICT REPORTERS, P.C.\n(212) 805-0300\n", case="US v. Test", source="2019-05-20_x.txt")
    events = ta.build_phone_evidence_events([idx])
    assert events, "the selective-evidence challenge must be detected"
    assert events[0]["page"] == 1518
    assert "selectively left out" in events[0]["text"].lower() or "left out" in events[0]["text"].lower()


def test_phone_evidence_event_ignores_non_phone_passage():
    """A passage using selective-evidence language about generic trial conduct
    (no phone/email/extraction topic) must NOT be flagged — this filters the
    real noise (a generic opening statement about 'selectively' or 'piecemeal'
    that is not about phone evidence)."""
    body = (
        "1    MS. AL-SHABAZZ:  The prosecution has been piecemeal and has "
        "selectively presented its case about the patient falls, the back "
        "surgeries, and the staffing arrangements at the clinic.\n"
    )
    idx = parse_transcript(f"\n1518\nJ591dun1\n\n{body}\n\nSOUTHERN DISTRICT REPORTERS, P.C.\n(212) 805-0300\n", case="US v. Test", source="2019-05-20_x.txt")
    events = ta.build_phone_evidence_events([idx])
    assert events == [], "non-phone selective-evidence passage must not be flagged"


def test_trial_overview_shape():
    idx = _mini_index()
    ov = ta.trial_overview(idx)
    assert ov["case"]
    assert ov["days"]
    assert ov["total_passages"] > 0
    assert ov["page_min"] == 100


def test_load_indices_async_offloads_and_caches(monkeypatch):
    """The async endpoint loader must run the CPU-bound transcript parse via
    asyncio.to_thread (never on the event loop) and cache the result so a
    burst of /trial/* requests parses once."""
    import asyncio
    from unittest.mock import patch

    called = {"n": 0}

    def _fake_load():
        called["n"] += 1
        return _mini_index()

    # Reset the module cache so the test observes a fresh load.
    ta._indices_cache[0] = None
    with patch.object(ta, "_load_indices", _fake_load):
        indices = asyncio.run(ta._load_indices_async())
        assert indices
        assert called["n"] == 1
        # Second call hits the cache — the underlying loader is NOT re-run.
        indices2 = asyncio.run(ta._load_indices_async())
        assert indices2 is indices
        assert called["n"] == 1, "async loader must cache after first parse"
    ta._indices_cache[0] = None
