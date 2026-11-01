"""Read-only diagnostic for listener visibility in the current execution context.

Why this exists
---------------
`f2_execution_adapter.resolve_serving_pid` and `f1_infra.RuntimeMonitor` fail
closed when psutil cannot attribute a listening port to a PID.  Two F1 tests and
two P2 tests failed in one agent execution context with "no LISTEN owner
resolved", while the same tests passed earlier in an ordinary shell.  In the
affected context a plain `socket.listen()` was reported with status ``NONE`` and
Windows-native tooling also showed no ``LISTEN`` state, so the cause is the
visibility of the listener table, not the resolver's logic.

This module changes NO production resolution behaviour.  It reports whether the
current context can see a listener it just created, so a failure can be
classified as an environment-visibility failure rather than an implementation
failure.  The authoritative evidence must still come from an owner PowerShell
session (see ``owner_action`` in the report).

Usage::

    python -m qwen_train.f2_listener_diagnostic
"""
from __future__ import annotations

import argparse
import json
import os
import socket
from typing import Any, Iterable, Mapping

VISIBLE = "VISIBLE"
UNAVAILABLE = "UNAVAILABLE"

OWNER_ACTION = (
    "Run this module (and tests/test_f1_evidence.py with "
    "tests/test_f2_real_p2_integration.py) from a normal, non-agent PowerShell "
    "session. Record the classification, psutil version and the visible status "
    "values. A normal-shell VISIBLE result means the agent-context failures are "
    "environment-visibility failures; only a normal-shell UNAVAILABLE result "
    "justifies investigating the resolver."
)


def _field(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def classify_listener_rows(rows: Iterable[Any], *, port: int, pid: int) -> str:
    """Return ``VISIBLE`` only for a LISTEN row matching ``port`` AND ``pid``.

    A row for the right port with a different PID, a non-LISTEN status (psutil
    reports ``NONE`` in some containers/virtualised contexts), or no row at all
    is ``UNAVAILABLE``: the context cannot confirm ownership, so nothing may be
    inferred from it.
    """
    for row in rows:
        if str(_field(row, "status", "") or "").upper() != "LISTEN":
            continue
        laddr = _field(row, "laddr")
        lport = _field(laddr, "port") if laddr is not None else None
        if lport == port and _field(row, "pid") == pid:
            return VISIBLE
    return UNAVAILABLE


def probe(port: int = 0) -> dict[str, Any]:
    """Open a loopback listener and report whether this context can see it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        bound = listener.getsockname()[1]
        pid = os.getpid()
        report: dict[str, Any] = {"port": bound, "pid": pid}
        try:
            import psutil
        except Exception as exc:  # noqa: BLE001 - diagnostic must not raise
            report.update(classification=UNAVAILABLE, reason=f"psutil unavailable: {exc}")
            report["owner_action"] = OWNER_ACTION
            return report
        report["psutil_version"] = getattr(psutil, "__version__", "unknown")
        try:
            rows = psutil.net_connections("tcp")
        except Exception as exc:  # noqa: BLE001
            report.update(classification=UNAVAILABLE, reason=f"net_connections failed: {exc}")
            report["owner_action"] = OWNER_ACTION
            return report
        report["statuses_for_port"] = sorted({
            str(_field(row, "status", "") or "") for row in rows
            if _field(_field(row, "laddr"), "port") == bound
        })
        report["classification"] = classify_listener_rows(rows, port=bound, pid=pid)
        if report["classification"] != VISIBLE:
            report["reason"] = (
                "this execution context does not report LISTEN ownership for a "
                "listener it just created; a resolver cannot resolve identity here"
            )
            report["owner_action"] = OWNER_ACTION
        return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Listener visibility diagnostic (read-only).")
    ap.add_argument("--port", type=int, default=0, help="loopback port to bind (0 = ephemeral)")
    args = ap.parse_args(argv)
    report = probe(args.port)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
