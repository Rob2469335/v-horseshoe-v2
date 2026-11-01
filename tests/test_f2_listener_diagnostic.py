"""Tests for the listener-visibility diagnostic.

These prove the CLASSIFICATION, not the host's listener table: a context that
cannot report LISTEN ownership must be reported as UNAVAILABLE so a failure is
never mistaken for a resolver bug.  The live environment result is produced by
running ``python -m qwen_train.f2_listener_diagnostic`` in the shell under test.
"""
from __future__ import annotations

from qwen_train import f2_listener_diagnostic as D


class _Addr:
    def __init__(self, port: int):
        self.port = port


class _Row:
    def __init__(self, status: str, port: int | None, pid: int):
        self.status = status
        self.laddr = _Addr(port) if port is not None else None
        self.pid = pid


def test_a_real_listen_row_for_the_right_port_and_pid_is_visible():
    assert D.classify_listener_rows(
        [_Row("LISTEN", 8271, 4242)], port=8271, pid=4242
    ) == D.VISIBLE


def test_status_none_is_unavailable():
    """Observed in the affected agent context: the row exists but is not LISTEN."""
    assert D.classify_listener_rows(
        [_Row("NONE", 8271, 4242)], port=8271, pid=4242
    ) == D.UNAVAILABLE


def test_listen_row_for_a_different_pid_is_not_ownership():
    assert D.classify_listener_rows(
        [_Row("LISTEN", 8271, 9999)], port=8271, pid=4242
    ) == D.UNAVAILABLE


def test_missing_rows_are_unavailable():
    assert D.classify_listener_rows([], port=8271, pid=4242) == D.UNAVAILABLE


def test_mapping_rows_are_supported():
    rows = [{"status": "LISTEN", "laddr": {"port": 8271, "ip": "127.0.0.1"}, "pid": 4242}]
    assert D.classify_listener_rows(rows, port=8271, pid=4242) == D.VISIBLE


def test_probe_always_classifies_and_names_the_owner_action_when_unavailable():
    report = D.probe()
    assert report["classification"] in {D.VISIBLE, D.UNAVAILABLE}
    assert report["port"] > 0
    if report["classification"] == D.UNAVAILABLE:
        assert "execution context" in report["reason"]
        assert "normal, non-agent PowerShell" in report["owner_action"]
