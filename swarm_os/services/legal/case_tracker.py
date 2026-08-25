"""Case tracker for Rob's Lawyer — a local "my cases" store built from docket
data pulled via CourtListener/RECAP (the deep-research guidance: dockets as a
first-class data source, ingested via proper APIs, never scraped).

This module is deliberately offline/persistent-only: it stores normalized case
metadata + an ordered list of docket entries in a JSON file under data/cases/ so
the app can answer "what's going on in my case" / "what's my next event" without
re-hitting the (rate-limited) API every time. The CourtListener fetch + webhook
ingestion lives separately; this is the storage + timeline layer.

Note: criminal trial transcript PDFs are party-restricted — this module tracks
docket *entries* (what exists, when), never transcript content. A transcript is
obtained from the court reporter / party PACER access, then searched with
transcript_search.py.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from datetime import UTC, datetime

_CASES_DIR = Path("data/cases")
_LOCK = threading.Lock()


@dataclass
class CaseEntry:
    """One line on a docket (filing, order, hearing notice, transcript notice)."""

    entry_date: str = ""
    description: str = ""
    document_number: str = ""
    pacer_doc_id: str = ""

    def to_dict(self) -> dict:
        return {
            "entry_date": self.entry_date,
            "description": self.description,
            "document_number": self.document_number,
            "pacer_doc_id": self.pacer_doc_id,
        }


@dataclass
class CaseRecord:
    """Normalized case metadata + ordered docket entries."""

    docket_id: str = ""
    case_name: str = ""
    docket_number: str = ""
    court: str = ""
    date_filed: str = ""
    date_terminated: str = ""
    entries: list[CaseEntry] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "docket_id": self.docket_id,
            "case_name": self.case_name,
            "docket_number": self.docket_number,
            "court": self.court,
            "date_filed": self.date_filed,
            "date_terminated": self.date_terminated,
            "entries": [e.to_dict() for e in self.entries],
            "updated_at": self.updated_at,
        }


def _path_for(docket_id: str) -> Path:
    return _CASES_DIR / f"{docket_id}.json"


def save_case(record: CaseRecord) -> Path:
    """Persist a case record atomically (tmp + replace) under data/cases/."""
    _CASES_DIR.mkdir(parents=True, exist_ok=True)
    record.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    path = _path_for(record.docket_id)
    with _LOCK:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)
    return path


def load_case(docket_id: str) -> CaseRecord | None:
    """Load a stored case record, or None if not present / corrupt."""
    path = _path_for(docket_id)
    if not path.exists():
        return None
    try:
        with _LOCK:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return CaseRecord(
        docket_id=data.get("docket_id", docket_id),
        case_name=data.get("case_name", ""),
        docket_number=data.get("docket_number", ""),
        court=data.get("court", ""),
        date_filed=data.get("date_filed", ""),
        date_terminated=data.get("date_terminated", ""),
        entries=[CaseEntry(**e) for e in data.get("entries", [])],
        updated_at=data.get("updated_at", ""),
    )


def list_cases() -> list[CaseRecord]:
    """All stored cases, sorted by docket_id."""
    if not _CASES_DIR.exists():
        return []
    out = []
    for p in sorted(_CASES_DIR.glob("*.json")):
        rec = load_case(p.stem)
        if rec is not None:
            out.append(rec)
    return out


def timeline(record: CaseRecord) -> list[CaseEntry]:
    """Docket entries ordered by entry_date (chronological)."""
    return sorted(record.entries, key=lambda e: e.entry_date)


def next_event(record: CaseRecord) -> CaseEntry | None:
    """The next future-dated entry (the "what's my next event" primitive).
    Entries are calendrical text; this only finds entries dated AFTER today."""
    today = datetime.now(UTC).date().isoformat()
    for e in timeline(record):
        if e.entry_date and e.entry_date > today:
            return e
    return None
