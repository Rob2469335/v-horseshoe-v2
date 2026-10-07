"""Unit tests for the governed-F2 runtime isolation guard.

These prove the fail-closed classification logic and the capability strip. The
probe tests use REAL loopback sockets (a listening socket => REACHABLE_VIOLATION;
a closed port => EXPECTED_UNREACHABLE) so the classifier is exercised against the
OS, not a mock. No host service is started or modified.
"""

from __future__ import annotations

import socket

import pytest

from runtime_v2.services import f2_runtime_guard as g


def _listening_port() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv, srv.getsockname()[1]


def _closed_port() -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    return port


def test_governed_f2_false_by_default(monkeypatch):
    monkeypatch.delenv(g.F2_ISOLATION_ENV, raising=False)
    assert g.governed_f2() is False


def test_governed_f2_true_when_marked(monkeypatch):
    monkeypatch.setenv(g.F2_ISOLATION_ENV, "1")
    assert g.governed_f2() is True


def test_probe_reachable_socket_is_violation():
    srv, port = _listening_port()
    try:
        result = g.probe_endpoint("svc", "127.0.0.1", port, timeout=1.0)
    finally:
        srv.close()
    assert result.status == g.REACHABLE_VIOLATION
    assert result.ok is False


def test_probe_closed_port_is_expected_unreachable():
    result = g.probe_endpoint("svc", "127.0.0.1", _closed_port(), timeout=0.5)
    assert result.status == g.EXPECTED_UNREACHABLE
    assert result.ok is True


def test_assert_raises_when_reachable():
    srv, port = _listening_port()
    try:
        with pytest.raises(g.F2IsolationViolation):
            g.assert_forbidden_services_unreachable(
                {"svc": ("127.0.0.1", port)}, timeout=1.0
            )
    finally:
        srv.close()


def test_assert_passes_when_unreachable():
    results = g.assert_forbidden_services_unreachable(
        {"svc": ("127.0.0.1", _closed_port())}, timeout=0.5
    )
    assert all(r.ok for r in results)


def test_check_error_is_not_success(monkeypatch):
    monkeypatch.setattr(
        g,
        "probe_endpoint",
        lambda name, host, port, timeout=0.5: g.ProbeResult(
            name, host, port, g.CHECK_ERROR, "boom"
        ),
    )
    with pytest.raises(g.F2IsolationViolation):
        g.assert_forbidden_services_unreachable({"svc": ("127.0.0.1", 1)})


def test_strip_forbidden_tools_noop_outside_f2(monkeypatch):
    monkeypatch.delenv(g.F2_ISOLATION_ENV, raising=False)
    tools = ["semantic_search", "remember", "filesystem", "final"]
    assert g.strip_forbidden_f2_tools(tools) == tools


def test_strip_forbidden_tools_removes_qdrant_caps_when_governed(monkeypatch):
    monkeypatch.setenv(g.F2_ISOLATION_ENV, "1")
    out = g.strip_forbidden_f2_tools(
        ["semantic_search", "remember", "deprecate_memory", "filesystem", "git", "final"]
    )
    assert out == ["filesystem", "git", "final"]
