"""Tests for the RECAP docket + FRAP deadline ledger
(swarm_os/services/legal/docket.py).

Evidence: missed deadlines are structural malpractice on a federal appeal; the
ledger is deterministic calendar math (no model). Tests pin the FRAP weekday
rule, trigger extraction, deadline computation from procedural anchors, and the
fail-closed fetch contract (network mocked).
"""
from __future__ import annotations

import datetime as dt
import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.services.legal.docket import (
    _next_business_day, extract_triggers, compute_deadlines, fetch_docket,
    DocketTrigger, DocketLedger, Deadline, render_docket_ledger,
    DOCKET_ENTRIES_URL,
)


def test_next_business_day_skips_weekend():
    # Saturday 2026-08-15 -> Monday 2026-08-17.
    assert _next_business_day(dt.date(2026, 8, 15)) == dt.date(2026, 8, 17)
    # Sunday 2026-08-16 -> Monday 2026-08-17.
    assert _next_business_day(dt.date(2026, 8, 16)) == dt.date(2026, 8, 17)
    # A weekday (Friday) is already a business day — unchanged.
    assert _next_business_day(dt.date(2026, 8, 14)) == dt.date(2026, 8, 14)


def test_next_business_day_skips_holiday():
    # MLK Day 2026-01-19 (Monday) is a federal holiday -> Tuesday 1/20.
    assert _next_business_day(dt.date(2026, 1, 19)) == dt.date(2026, 1, 20)
    # July 4 2026 is a Saturday -> Monday 7/6.
    assert _next_business_day(dt.date(2026, 7, 4)) == dt.date(2026, 7, 6)


def test_extract_triggers_from_entries():
    entries = [
        {"description": "JUDGMENT entered", "date_filed": "2026-01-05"},
        {"description": "Notice of appeal filed", "date_filed": "2026-01-12"},
        {"description": "Case docketed", "date_filed": "2026-01-20"},
        {"description": "Record filed", "date_filed": "2026-01-22"},
        {"description": "Appellant's brief filed", "date_filed": "2026-02-19"},
        {"description": "Appellee's brief filed", "date_filed": "2026-03-21"},
        {"description": "Order granting extension", "date_filed": "2026-01-25"},  # not a trigger
    ]
    trig = extract_triggers(entries)
    kinds = {t.kind for t in trig}
    assert "judgment_entered" in kinds
    assert "notice_of_appeal" in kinds
    assert "docketed" in kinds
    assert "record_filed" in kinds
    assert "appellant_brief_filed" in kinds
    assert "appellee_brief_filed" in kinds
    assert len(trig) == 6  # the extension order is NOT a trigger


def test_compute_deadlines_criminal_notice_of_appeal():
    # Judgment entered 2026-01-05 (Monday) -> NOA due +14 days (FRAP 4(b)).
    trig = [DocketTrigger("judgment_entered", dt.date(2026, 1, 5))]
    dl = compute_deadlines(trig, today=dt.date(2026, 1, 5))
    noa = [d for d in dl if d.label == "Notice of appeal (criminal)"]
    assert noa
    # 1/5 + 14 = 1/19 (Monday) — which is MLK Day -> Tuesday 1/20.
    assert noa[0].due == dt.date(2026, 1, 20)
    assert noa[0].rule == "FRAP 4(b)(1)(A)"


def test_compute_deadlines_appellant_brief_from_record_filed():
    """FRAP 31(a)(1): appellant's brief is due 40 days after the RECORD IS FILED
    (not after the NOA). Pinned to the actual rule text (LII, fetched 2026-08)."""
    trig = [DocketTrigger("record_filed", dt.date(2026, 1, 20))]
    dl = compute_deadlines(trig, today=dt.date(2026, 1, 20))
    ab = [d for d in dl if d.label == "Appellant brief due"]
    assert ab
    # 1/20 + 40 = 3/1 (Sunday) -> Monday 3/2.
    assert ab[0].due == dt.date(2026, 3, 2)
    assert ab[0].rule == "FRAP 31(a)(1)"


def test_compute_deadlines_appellant_brief_proxy_flagged_when_no_record():
    """Without a record-filed trigger, the NOA anchor must be flagged as a
    PROXY (not silently presented as the rule's trigger) — honesty about the
    anchor, never a confident-but-wrong deadline."""
    trig = [DocketTrigger("notice_of_appeal", dt.date(2026, 1, 12))]
    dl = compute_deadlines(trig, today=dt.date(2026, 1, 12))
    ab = [d for d in dl if d.label == "Appellant brief due"]
    assert ab
    assert "proxy" in ab[0].rule  # honest: not the rule's exact trigger


def test_compute_deadlines_reply_from_appellee_brief_served():
    """FRAP 31(a)(1): reply brief is due 21 days after SERVICE of the appellee's
    brief (we anchor on the appellee brief FILED date)."""
    trig = [DocketTrigger("appellant_brief_filed", dt.date(2026, 2, 19)),
            DocketTrigger("appellee_brief_filed", dt.date(2026, 3, 21))]
    dl = compute_deadlines(trig, today=dt.date(2026, 3, 21))
    reply = [d for d in dl if d.label == "Reply brief due"]
    appellee = [d for d in dl if d.label == "Appellee brief due"]
    assert reply and appellee
    # Appellee brief (Sat 3/21) + 30 = ... appellee due: 2/19 + 30 = 3/21 (Sat) -> 3/23.
    assert appellee[0].due == dt.date(2026, 3, 23)
    # Reply: appellee brief filed 3/21 + 21 = 4/11 (Sat) -> Mon 4/13.
    assert reply[0].due == dt.date(2026, 4, 13)
    assert reply[0].rule == "FRAP 31(a)(1)"


def test_compute_deadlines_missing_trigger_reports_not_started():
    dl = compute_deadlines([], today=dt.date(2026, 1, 1))
    assert dl == []  # no triggers -> no deadlines (honest: nothing started)


@pytest.mark.asyncio
async def test_fetch_docket_fail_closed_on_http_error():
    """A docket fetch outage must return an error flag with empty deadlines,
    never raise."""
    with patch("swarm_os.services.legal.docket.httpx.AsyncClient") as mock_cls:
        mock_resp = AsyncMock()
        mock_resp.status_code = 429
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        ledger = await fetch_docket("20-3459")
    assert ledger.error == "http:429"
    assert ledger.deadlines == []


@pytest.mark.asyncio
async def test_fetch_docket_computes_ledger_from_entries():
    """A docket resolved by number + entries from the dedicated docket-entries
    endpoint must extract triggers and compute deadlines — the REAL two-step
    seam (VERIFIED live 2026-08-11: the dockets object returns docket_entries
    as None; entries live at /docket-entries/?docket=<id>). Only the HTTP legs
    are mocked."""
    from types import SimpleNamespace
    docket = {"case_name": "United States v. Rainford", "id": 67590633}
    entries = [
        {"description": "JUDGMENT entered", "date_filed": "2026-01-05"},
        {"description": "Notice of appeal filed", "date_filed": "2026-01-12"},
        {"description": "Record filed", "date_filed": "2026-01-22"},
        {"description": "Appellant's brief filed", "date_filed": "2026-02-19"},
    ]
    # Two GETs: dockets (by number) then docket-entries (by id).
    docket_resp = SimpleNamespace(status_code=200, json=lambda: {"results": [docket]})
    entries_resp = SimpleNamespace(status_code=200, json=lambda: {"results": entries})
    with patch("swarm_os.services.legal.docket.httpx.AsyncClient") as mock_cls:
        client_mock = mock_cls.return_value.__aenter__.return_value
        client_mock.get = AsyncMock(side_effect=[docket_resp, entries_resp])
        ledger = await fetch_docket("20-3459")
    assert ledger.error == ""
    assert ledger.case_name == "United States v. Rainford"
    assert any(d.label == "Notice of appeal (criminal)" for d in ledger.deadlines)
    assert any(d.label == "Appellant brief due" for d in ledger.deadlines)
    # The second GET must hit the DEDICATED entries endpoint with the docket id.
    second_call = client_mock.get.call_args_list[1]
    assert second_call.args[0] == DOCKET_ENTRIES_URL
    assert second_call.kwargs["params"]["docket"] == 67590633


@pytest.mark.asyncio
async def test_fetch_docket_uses_dedicated_entries_endpoint_not_inline():
    """REGRESSION (from the docket hand-walk): the dockets object returns
    `docket_entries` as None — fetch_docket must NOT look for inline entries
    there; it must resolve the id then hit /docket-entries/?docket=<id>. This
    test pins that the entries come from the dedicated endpoint, never from the
    dockets object."""
    from types import SimpleNamespace
    docket = {"case_name": "X", "id": 42, "docket_entries": None}  # the real API shape
    entries = [{"description": "Notice of appeal filed", "date_filed": "2026-01-12"}]
    docket_resp = SimpleNamespace(status_code=200, json=lambda: {"results": [docket]})
    entries_resp = SimpleNamespace(status_code=200, json=lambda: {"results": entries})
    with patch("swarm_os.services.legal.docket.httpx.AsyncClient") as mock_cls:
        client_mock = mock_cls.return_value.__aenter__.return_value
        client_mock.get = AsyncMock(side_effect=[docket_resp, entries_resp])
        ledger = await fetch_docket("x")
    assert ledger.error == ""
    assert len(ledger.triggers) == 1
    assert ledger.triggers[0].kind == "notice_of_appeal"


def test_render_docket_ledger_readable():
    ledger = DocketLedger(
        docket_number="20-3459", case_name="United States v. Rainford",
        deadlines=[Deadline(label="Appellant brief due", due=dt.date(2026, 2, 11),
                            days_remaining=3, rule="FRAP 31(a)(1)", trigger="notice_of_appeal")],
    )
    out = render_docket_ledger(ledger)
    assert "Appellant brief due" in out
    assert "⏰" in out  # <=7 days urgency flag
