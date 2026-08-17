"""Tests for the forward-citing 'still good law' monitor
(swarm_os/services/legal/citator.py).

Evidence: candor obligation to disclose adverse authority (Shepard's/KeyCite
alert replacement on free CourtListener `/opinions-cited/` data). These tests
pin the DETERMINISTIC parts — treatment classification via the existing
taxonomy, adverse-alert building, state durability/resume, report rendering —
with the network legs mocked (the established seam).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.services.legal.citator import (
    ADVERSE_TREATMENTS,
    _classify_treatment,
    _load_state,
    _save_state,
    CitatorReport,
    render_citator_report,
    poll_authority,
)


MANIFEST = [
    {"cite": "507 U.S. 725", "name": "Olano", "tier": 1},
    {"cite": "252 F.3d 238", "name": "Simeonov", "tier": 1},
]


def test_adverse_treatments_are_the_candor_trigger():
    assert ADVERSE_TREATMENTS == {"distinguished", "overruled", "questioned"}
    assert "followed" not in ADVERSE_TREATMENTS  # followed is informational


def test_classify_treatment_uses_existing_taxonomy():
    # A citing sentence that follows our authority.
    assert (
        _classify_treatment("We follow the rule of 507 U.S. 725 here.", "507|us|725")
        == "followed"
    )
    # An adverse (overruling) citing sentence.
    assert (
        _classify_treatment(
            "Simeonov was overruled by the en banc court.", "252|f3d|238"
        )
        == "overruled"
    )


def test_state_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "swarm_os.services.legal.citator.STATE_FILE", tmp_path / "st.json"
    )
    _save_state(
        {
            "507 U.S. 725": {
                "opinion_id": 42,
                "polled": 1.0,
                "treatments": {"7": "followed"},
            }
        }
    )
    loaded = _load_state()
    assert loaded["507 U.S. 725"]["opinion_id"] == 42
    assert loaded["507 U.S. 725"]["treatments"]["7"] == "followed"


def test_render_citator_report_shows_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "swarm_os.services.legal.citator.STATE_FILE", tmp_path / "st.json"
    )
    report = CitatorReport(
        authorities=[
            {
                "cite": "507 U.S. 725",
                "name": "Olano",
                "treatments": {"7": "overruled"},
                "adverse": ["7"],
            }
        ],
        alerts=[
            {
                "authority": "507 U.S. 725",
                "authority_name": "Olano",
                "citing_opinion_id": "7",
                "treatment": "overruled",
            }
        ],
    )
    out = render_citator_report(report)
    assert "ADVERSE" in out
    assert "overruled" in out


def test_render_citator_report_clean_when_no_alerts():
    report = CitatorReport(
        authorities=[
            {
                "cite": "507 U.S. 725",
                "name": "Olano",
                "treatments": {"7": "followed"},
                "adverse": [],
            }
        ],
        alerts=[],
    )
    out = render_citator_report(report)
    assert "No adverse treatment detected" in out


@pytest.mark.asyncio
async def test_poll_authority_resolves_and_classifies(tmp_path, monkeypatch):
    """poll_authority must resolve opinion ids, fetch forward cites, classify
    each citing opinion's treatment via the taxonomy, and surface adverse
    alerts — driving the REAL logic with only the HTTP legs mocked."""
    monkeypatch.setattr(
        "swarm_os.services.legal.citator.STATE_FILE", tmp_path / "st.json"
    )
    fake_cites = [
        {"cite": "507 U.S. 725", "name": "Olano", "tier": 1},
    ]
    # opinion id resolution: citation-lookup -> cluster id -> opinions-by-cluster
    with (
        patch(
            "swarm_os.services.legal.citator._resolve_opinion_id",
            new=AsyncMock(return_value=100),
        ),
        patch(
            "swarm_os.services.legal.citator._forward_citing",
            new=AsyncMock(
                return_value=[
                    {"citing_opinion": 200, "depth": 3},
                    {"citing_opinion": 300, "depth": 1},
                ]
            ),
        ),
        patch(
            "swarm_os.services.legal.citator._citing_opinion_text",
            new=AsyncMock(
                side_effect=[
                    ("We follow the rule of 507 U.S. 725.", "New Case A"),
                    ("The holding in 507 U.S. 725 was overruled.", "New Case B"),
                ]
            ),
        ),
    ):
        report = await poll_authority(fake_cites, max_authorities=1)
    assert len(report.authorities) == 1
    assert report.authorities[0]["cite"] == "507 U.S. 725"
    assert report.authorities[0]["treatments"]["200"] == "followed"
    assert report.authorities[0]["treatments"]["300"] == "overruled"
    # The overruled citing opinion is an adverse ALERT.
    assert len(report.alerts) == 1
    assert report.alerts[0]["treatment"] == "overruled"
    assert report.alerts[0]["authority"] == "507 U.S. 725"
    # State persisted.
    state = _load_state()
    assert state["507 U.S. 725"]["opinion_id"] == 100


@pytest.mark.asyncio
async def test_poll_authority_resumes_without_repolling(tmp_path, monkeypatch):
    """An already-polled authority must be read from state, not re-polled —
    the rate-limit-aware resume contract. The FIRST manifest authority (already
    in state) must not be re-polled; the second (not in state) legitimately is."""
    monkeypatch.setattr(
        "swarm_os.services.legal.citator.STATE_FILE", tmp_path / "st.json"
    )
    _save_state(
        {
            "507 U.S. 725": {
                "opinion_id": 100,
                "polled": 1.0,
                "treatments": {"200": "followed"},
                "adverse": [],
            }
        }
    )
    with (
        patch(
            "swarm_os.services.legal.citator._resolve_opinion_id",
            new=AsyncMock(side_effect=[100]),
        ) as resolve_mock,
        patch(
            "swarm_os.services.legal.citator._forward_citing",
            new=AsyncMock(return_value=[{"citing_opinion": 200, "depth": 1}]),
        ) as fwd_mock,
    ):
        report = await poll_authority(MANIFEST, max_authorities=2)
    # First authority (507 U.S. 725) resumes from state: treatments preserved,
    # and only the SECOND authority (not in state) hit the resolve/fwd legs.
    first = report.authorities[0]
    assert first["cite"] == "507 U.S. 725"
    assert first["treatments"]["200"] == "followed"
    assert resolve_mock.await_count == 1  # only 252 F.3d 238 resolved
    assert fwd_mock.await_count == 1  # only 252 F.3d 238 forward-cited


@pytest.mark.asyncio
async def test_poll_authority_records_resolve_failure(tmp_path, monkeypatch):
    """An authority whose opinion id can't be resolved must be recorded with an
    error, never skipped silently (fail-closed)."""
    monkeypatch.setattr(
        "swarm_os.services.legal.citator.STATE_FILE", tmp_path / "st.json"
    )
    with patch(
        "swarm_os.services.legal.citator._resolve_opinion_id",
        new=AsyncMock(return_value=None),
    ):
        report = await poll_authority(
            [{"cite": "999 F.3d 999", "name": "Ghost", "tier": 3}], max_authorities=1
        )
    assert report.authorities[0]["error"] == "resolve_failed"
    assert _load_state()["999 F.3d 999"]["error"] == "resolve_failed"


@pytest.mark.asyncio
async def test_pace_is_async_not_blocking_sleep(monkeypatch):
    """The CourtListener rate limiter must be an ASYNC sleep — the old
    `time.sleep(12.5)` blocked the WHOLE FastAPI event loop (all agent streams,
    heartbeats, daemons) for minutes during a citator poll, and made the
    `asyncio.timeout` on the endpoint unable to fire mid-sleep. An async sleep
    keeps the loop responsive while preserving the rate limit."""
    import inspect
    import swarm_os.services.legal.citator as ct

    assert inspect.iscoroutinefunction(ct._pace), (
        "_pace must be async — a sync time.sleep freezes the event loop"
    )
    # The four rate-limited call sites must AWAIT it.
    import swarm_os.services.legal.citator as _ct

    src = inspect.getsource(_ct)
    assert src.count("await _pace()") == 4, (
        "all four rate-limited legs must await _pace()"
    )
    # And it must actually yield to the loop (an await asyncio.sleep), not run a
    # blocking time.sleep on the loop thread.
    with patch.object(ct, "_PACE_S", 0.001):
        await ct._pace()  # must complete quickly and not block

    # time.sleep must not be CALLED in the pace implementation anymore.
    pace_src = inspect.getsource(ct._pace)
    assert "time.sleep(" not in pace_src
    assert "asyncio.sleep(" in pace_src
