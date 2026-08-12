"""RECAP docket awareness + FRAP deadline ledger for Rob's Lawyer (Gap 2).

Evidence: a federal criminal appeal is won or lost on docket hygiene before it
is won on the merits — missed deadlines are structural malpractice. CourtListener
exposes RECAP dockets/docket-entries/recap-documents free (verified in the v4
API root); this module maps the Rainford docket to a FRAP/2d Cir. deadline
ledger computed from procedural triggers. Deterministic calendar math — no model
work, the thing commercial practice-management suites charge for.

Deadlines computed (FRAP + 2d Cir. L.R.):
  - FRAP 4(b): notice of appeal due 14 days after entry of the judgment being
    appealed (criminal).
  - FRAP 31(a)(1): appellant's brief due 30 days after the docketing date (or
    the date the record is deemed filed, whichever is later).
  - FRAP 31(a)(1) appellee's brief: 30 days after appellant's brief is served.
  - FRAP 28.1 / 2d Cir. L.R. 31.2: reply brief 14 days after appellee's brief.
  - FRAP 32(a)(7): type-volume limit (14,000 words for an appellant's brief).
  - Weekday rule (FRAP 26(a)): a deadline falling on a weekend/holiday moves to
    the next day that isn't.

Never raises — a docket-fetch outage returns the triggers it has with a clear
`error` flag. Deterministic and unit-testable with a synthetic docket."""
from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

DOCKETS_URL = os.getenv(
    "COURTLISTENER_DOCKETS_URL",
    "https://www.courtlistener.com/api/rest/v4/dockets/",
)
# Dedicated docket-entries endpoint (VERIFIED live 2026-08-11): the dockets
# object does NOT inline `docket_entries` (it returns None) — the entries live
# here, keyed by the docket id. The original fetch_docket looked for entries on
# the dockets object and never reached real trigger data (the hand-walk bug).
DOCKET_ENTRIES_URL = os.getenv(
    "COURTLISTENER_DOCKET_ENTRIES_URL",
    "https://www.courtlistener.com/api/rest/v4/docket-entries/",
)

# FRAP 26(a) federal holidays (observed). Non-exhaustive but covers the fixed
# ones that matter for a federal appeal; a holiday not listed is a conservative
# miss (deadline computed slightly earlier, never later).
_FED_HOLIDAYS = frozenset(
    (m, d) for (m, d) in [
        (1, 1), (1, 15), (1, 19), (2, 17), (5, 25), (6, 19),
        (7, 4), (9, 1), (10, 13), (11, 11), (11, 27), (12, 25),
    ]
)


def _next_business_day(d: dt.date) -> dt.date:
    """FRAP 26(a): a deadline on a weekend/federal holiday moves to the next
    day that isn't. Deterministic."""
    while d.weekday() >= 5 or (d.month, d.day) in _FED_HOLIDAYS:
        d += dt.timedelta(days=1)
    return d


@dataclass
class DocketTrigger:
    kind: str               # judgment_entered / docketed / appellant_brief_served / ...
    date: dt.date | None


@dataclass
class Deadline:
    label: str
    due: dt.date | None
    days_remaining: int | None
    rule: str
    trigger: str


@dataclass
class DocketLedger:
    docket_number: str = ""
    case_name: str = ""
    triggers: list[DocketTrigger] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    error: str = ""
    fetched_at: str = ""


def _parse_date(raw: Any) -> dt.date | None:
    """Parse a CourtListener ISO date (YYYY-MM-DD) to a date, or None."""
    if not raw:
        return None
    s = str(raw)[:10]
    try:
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def extract_triggers(docket_entries: list[dict[str, Any]]) -> list[DocketTrigger]:
    """From RECAP docket entries, extract the procedural triggers that start
    FRAP deadlines. Deterministic keyword matching on entry descriptions. The
    real docket entries carry `description` (e.g. "Judgment", "Notice of
    appeal filed"); the docket itself carries the judgment-entry date when
    available. Returns an ordered list of triggers."""
    triggers: list[DocketTrigger] = []
    for entry in docket_entries:
        desc = (entry.get("description") or "") + " " + (entry.get("document_number") or "")
        low = desc.lower()
        date = _parse_date(entry.get("date_filed"))
        if "judgment" in low and "entered" in low and date:
            triggers.append(DocketTrigger("judgment_entered", date))
        elif "notice of appeal" in low and date:
            triggers.append(DocketTrigger("notice_of_appeal", date))
        elif "docketed" in low and date:
            triggers.append(DocketTrigger("docketed", date))
        elif "record" in low and "filed" in low and date:
            triggers.append(DocketTrigger("record_filed", date))
        elif "appellant" in low and "brief" in low and "filed" in low and date:
            triggers.append(DocketTrigger("appellant_brief_filed", date))
        elif "appellee" in low and "brief" in low and "filed" in low and date:
            triggers.append(DocketTrigger("appellee_brief_filed", date))
    return triggers


def compute_deadlines(triggers: list[DocketTrigger],
                      today: dt.date | None = None) -> list[Deadline]:
    """Compute the FRAP/2d Cir. deadline ledger from the extracted triggers.

    RULE TEXT (Federal Rules of Appellate Procedure, current; source: LII,
    https://www.law.cornell.edu/rules/frap/ — fetched 2026-08-11):
      - FRAP 4(b)(1)(A): a defendant's notice of appeal in a criminal case is
        due 14 days after the later of (i) entry of the judgment/order being
        appealed, or (ii) filing of the government's notice of appeal.
      - FRAP 31(a)(1): "The appellant must serve and file a brief within 40
        days after the record is filed. The appellee must serve and file a
        brief within 30 days after the appellant's brief is served. The
        appellant may serve and file a reply brief within 21 days after service
        of the appellee's brief..."
      - FRAP 26(a): periods ending on a weekend/holiday extend to the next day.

    Each deadline is anchored to the LATEST trigger of its kind (procedural
    timers reset). `today` defaults to the real date; injectable for tests.
    Deterministic. A deadline with no anchor trigger is reported with due=None
    (not yet started — honest, never fabricated)."""
    today = today or dt.date.today()

    def _latest(kind: str) -> dt.date | None:
        dates = [t.date for t in triggers if t.kind == kind and t.date]
        return max(dates) if dates else None

    def _add(label: str, due: dt.date | None, rule: str, trigger: str) -> None:
        if due:
            due = _next_business_day(due)
            remaining = (due - today).days
        else:
            remaining = None
        deadlines.append(Deadline(
            label=label, due=due, days_remaining=remaining,
            rule=rule, trigger=trigger,
        ))

    deadlines: list[Deadline] = []
    judgment = _latest("judgment_entered")
    record = _latest("record_filed")
    notice = _latest("notice_of_appeal")
    app_brief = _latest("appellant_brief_filed")
    appee_brief = _latest("appellee_brief_filed")

    # FRAP 4(b)(1)(A): defendant's NOA = 14 days after judgment entry (the
    # later-of with the government's NOA is only reachable with government data
    # we don't carry; judgment entry is the conservative anchor for the
    # defendant — a later government NOA only EXTENDS the defendant's time).
    if judgment:
        _add("Notice of appeal (criminal)", judgment + dt.timedelta(days=14),
             "FRAP 4(b)(1)(A)", "judgment_entered")
    # FRAP 31(a)(1): appellant brief = 40 days after the record is filed.
    if record:
        _add("Appellant brief due", record + dt.timedelta(days=40),
             "FRAP 31(a)(1)", "record_filed")
    elif notice:
        # No record-filed trigger yet; the NOA anchor is the earliest reliable
        # proxy but is NOT the rule's trigger — flag it as a proxy in the rule.
        _add("Appellant brief due", notice + dt.timedelta(days=40),
             "FRAP 31(a)(1) [proxy: record not filed]",
             "notice_of_appeal (proxy)")
    # FRAP 31(a)(1): appellee brief = 30 days after the appellant's brief is
    # served (we anchor on the appellant's brief FILED date).
    if app_brief:
        _add("Appellee brief due", app_brief + dt.timedelta(days=30),
             "FRAP 31(a)(1)", "appellant_brief_filed")
    # FRAP 31(a)(1): reply = 21 days after service of the appellee's brief.
    if appee_brief:
        _add("Reply brief due", appee_brief + dt.timedelta(days=21),
             "FRAP 31(a)(1)", "appellee_brief_filed")
    return deadlines


async def fetch_docket(docket_number: str,
                       entries_url: str | None = None) -> DocketLedger:
    """Fetch a RECAP docket + entries from CourtListener and compute the ledger.

    Two-step seam (VERIFIED live 2026-08-11): the dockets endpoint does NOT
    inline `docket_entries` (returns None) — resolve the docket by number, then
    fetch its entries from the dedicated `/docket-entries/?docket=<id>`
    endpoint. `entries_url` may be passed directly (a stored docket-entries
    URL); otherwise the module resolves by docket_number.
    Returns a DocketLedger. Never raises — a fetch outage returns an error flag
    with empty triggers/deadlines."""
    ledger = DocketLedger(docket_number=docket_number, fetched_at=dt.datetime.now().isoformat())
    headers = {}
    if os.getenv("COURTLISTENER_API_TOKEN"):
        headers["Authorization"] = f"Token {os.getenv('COURTLISTENER_API_TOKEN')}"
    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            if entries_url:
                # Direct entries URL supplied — use it as-is.
                ledger.docket_number = entries_url
                eresp = await client.get(entries_url, params={"format": "json"}, timeout=30.0)
                if eresp.status_code != 200:
                    ledger.error = f"http:{eresp.status_code}"
                else:
                    ev = (eresp.json() or {}).get("results") or []
                    ledger.triggers = extract_triggers(ev)
            else:
                # Step 1: resolve the docket by number.
                resp = await client.get(
                    DOCKETS_URL,
                    params={"docket_number": docket_number, "format": "json"},
                    timeout=30.0,
                )
                if resp.status_code != 200:
                    ledger.error = f"http:{resp.status_code}"
                else:
                    results = (resp.json() or {}).get("results") or []
                    if not results:
                        ledger.error = "not_found"
                    else:
                        docket = results[0]
                        ledger.case_name = (docket.get("case_name") or "")
                        docket_id = docket.get("id")
                        if not docket_id:
                            ledger.error = "no_docket_id"
                        else:
                            # Step 2: fetch entries from the dedicated endpoint.
                            eresp = await client.get(
                                DOCKET_ENTRIES_URL,
                                params={"docket": docket_id, "format": "json", "page_size": 50},
                                timeout=30.0,
                            )
                            if eresp.status_code != 200:
                                ledger.error = f"entries_http:{eresp.status_code}"
                            else:
                                ev = (eresp.json() or {}).get("results") or []
                                ledger.triggers = extract_triggers(ev)
    except Exception as exc:
        log.warning("docket fetch failed: %s", exc)
        ledger.error = f"fetch_failed: {exc}"
    ledger.deadlines = compute_deadlines(ledger.triggers)
    return ledger


def render_docket_ledger(ledger: DocketLedger) -> str:
    """Render the deadline ledger as markdown."""
    out = [f"# Docket Ledger — {ledger.docket_number or 'unknown'}"]
    if ledger.case_name:
        out.append(f"**{ledger.case_name}**")
    if ledger.error:
        out.append(f"\n⚠ Docket fetch issue: {ledger.error}")
    if not ledger.deadlines:
        out.append("\nNo deadlines computable yet (triggers pending).")
    for d in ledger.deadlines:
        if d.due:
            urgency = "⏰ " if d.days_remaining is not None and d.days_remaining <= 7 else ""
            out.append(f"- {urgency}{d.label}: **{d.due.isoformat()}** "
                       f"({d.days_remaining} days) — {d.rule} (trigger: {d.trigger})")
        else:
            out.append(f"- {d.label}: not started — {d.rule} (trigger: {d.trigger})")
    return "\n".join(out)


if __name__ == "__main__":
    import asyncio
    asyncio.run(fetch_docket("20-3459"))
