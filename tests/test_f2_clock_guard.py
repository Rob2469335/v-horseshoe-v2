"""Guest clock startup-guard tests. Deterministic; never uses the real clock."""

from __future__ import annotations

from qwen_train import f2_clock_guard as cg

REF = "2026-10-07T00:00:00+00:00"
TOL = 120.0


def _guest(offset_s: float) -> str:
    from datetime import datetime, timedelta, timezone

    base = datetime.fromisoformat(REF)
    return (base + timedelta(seconds=offset_s)).astimezone(timezone.utc).isoformat()


def test_valid_clock_ok():
    r = cg.evaluate_clock(guest_utc=_guest(5), host_reference_utc=REF, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_OK
    assert r["reason"] == ""
    assert abs(r["clock_skew_seconds"] - 5) < 1e-6


def test_too_old_clock_fails():
    r = cg.evaluate_clock(guest_utc=_guest(-100000), host_reference_utc=REF, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_FAIL
    assert r["reason"] == cg.REASON_OUTSIDE


def test_too_new_clock_fails():
    r = cg.evaluate_clock(guest_utc=_guest(100000), host_reference_utc=REF, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_FAIL


def test_exact_boundary_is_ok():
    r = cg.evaluate_clock(guest_utc=_guest(TOL), host_reference_utc=REF, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_OK


def test_one_second_outside_fails():
    r = cg.evaluate_clock(guest_utc=_guest(TOL + 1), host_reference_utc=REF, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_FAIL
    assert r["reason"] == cg.REASON_OUTSIDE


def test_malformed_reference_fails():
    r = cg.evaluate_clock(guest_utc=_guest(0), host_reference_utc="not-a-time", tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_FAIL
    assert r["reason"] == cg.REASON_MALFORMED_REFERENCE


def test_missing_reference_fails():
    r = cg.evaluate_clock(guest_utc=_guest(0), host_reference_utc=None, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_FAIL
    assert r["reason"] == cg.REASON_MISSING_REFERENCE


def test_http_date_reference_accepted():
    # "Wed, 07 Oct 2026 00:00:00 GMT" == REF
    r = cg.evaluate_clock_http_date(
        guest_utc=_guest(5), http_date="Wed, 07 Oct 2026 00:00:00 GMT", tolerance_seconds=TOL
    )
    assert r["clock_status"] == cg.STATUS_OK


def test_malformed_http_date_reference_fails():
    r = cg.evaluate_clock_http_date(
        guest_utc=_guest(5), http_date="not a date", tolerance_seconds=TOL
    )
    assert r["clock_status"] == cg.STATUS_FAIL
    assert r["reason"] == cg.REASON_MALFORMED_REFERENCE


def test_malformed_guest_fails():
    r = cg.evaluate_clock(guest_utc="", host_reference_utc=REF, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_FAIL
    assert r["reason"] == cg.REASON_MALFORMED_GUEST


def test_epoch_seconds_accepted():
    r = cg.evaluate_clock(guest_utc=1759795200.0, host_reference_utc=1759795200, tolerance_seconds=TOL)
    assert r["clock_status"] == cg.STATUS_OK


def test_deterministic_serialization():
    r1 = cg.evaluate_clock(guest_utc=_guest(5), host_reference_utc=REF, tolerance_seconds=TOL)
    r2 = cg.evaluate_clock(guest_utc=_guest(5), host_reference_utc=REF, tolerance_seconds=TOL)
    assert cg.dumps(r1) == cg.dumps(r2)
    assert cg.dumps(r1).startswith('{"clock_skew_seconds"')
