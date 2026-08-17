"""Tests for case_tracker.py — the local "my cases" store built from
CourtListener/RECAP docket data (deep-research guidance: dockets as a first-class
data source, ingested via proper APIs, never scraped).

Pins: atomic persist/load round-trip, chronological timeline, next-event
primitive, corrupt-file tolerance, list ordering.
"""

from __future__ import annotations

import pytest

from swarm_os.services.legal import case_tracker


@pytest.fixture(autouse=True)
def _isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(case_tracker, "_CASES_DIR", tmp_path)
    yield tmp_path


def _record() -> case_tracker.CaseRecord:
    return case_tracker.CaseRecord(
        docket_id="7033130",
        case_name="United States v. Kalkanis",
        docket_number="1:18-cr-00289",
        court="nysd",
        date_filed="2018-04-17",
        entries=[
            case_tracker.CaseEntry(
                entry_date="2023-06-08",
                description="JUDGMENT IN A CRIMINAL CASE as to Kerry Gordon",
                document_number="123",
            ),
            case_tracker.CaseEntry(
                entry_date="2025-05-27",
                description="ORDER as to Robert Locust, Ryan Rainford",
            ),
        ],
    )


def test_save_load_roundtrip(tmp_path):
    rec = _record()
    case_tracker.save_case(rec)
    loaded = case_tracker.load_case("7033130")
    assert loaded is not None
    assert loaded.case_name == "United States v. Kalkanis"
    assert len(loaded.entries) == 2
    assert (
        loaded.entries[0].description
        == "JUDGMENT IN A CRIMINAL CASE as to Kerry Gordon"
    )
    assert loaded.updated_at  # stamped on save


def test_no_tmp_left_after_save(tmp_path):
    case_tracker.save_case(_record())
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_timeline_is_chronological(tmp_path):
    rec = _record()
    tl = case_tracker.timeline(rec)
    assert [e.entry_date for e in tl] == ["2023-06-08", "2025-05-27"]


def test_next_event_skips_past(tmp_path):
    rec = _record()
    # Both entries are in the past -> no next event.
    assert case_tracker.next_event(rec) is None
    rec.entries.append(
        case_tracker.CaseEntry(
            entry_date="2030-01-15",
            description="Evidentiary hearing",
        )
    )
    nxt = case_tracker.next_event(rec)
    assert nxt is not None and nxt.description == "Evidentiary hearing"


def test_load_missing_and_corrupt(tmp_path):
    assert case_tracker.load_case("nonexistent") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert case_tracker.load_case("bad") is None


def test_list_cases_sorted(tmp_path):
    for dk in ("b", "a"):
        case_tracker.save_case(case_tracker.CaseRecord(docket_id=dk))
    ids = [c.docket_id for c in case_tracker.list_cases()]
    assert ids == ["a", "b"]
