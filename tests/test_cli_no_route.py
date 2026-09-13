"""Regression: `--no-route` (and `--json` on a non-slash prompt) must send the
prompt to the agent loop verbatim, not re-route it through slash/NL routing.

Live defect: `rob "find all call sites of _build_grounded_report across the
codebase"` was hijacked by the NL router into `/search` and never reached the
agent. Reference-class goals (call-sites/refactor) must reach the agent.
"""

from __future__ import annotations

import organism_console.cli as cli
import organism_console.token_tracker as tt
from organism_console.cli import _raw_command_mode


def test_raw_command_mode_matrix():
    assert _raw_command_mode(True, False, "anything") is True
    assert _raw_command_mode(False, True, "find all call sites") is True
    assert _raw_command_mode(False, True, "/status") is False
    assert _raw_command_mode(False, False, "find all call sites") is False


def _drive(monkeypatch, argv):
    monkeypatch.setattr(tt, "start_background_poll", lambda: None)
    calls: dict = {}

    def fake_handle(line, cctx):
        calls["handle_line"] = line
        return "ROUTED"

    def fake_run(c, prompt, json_flag=False):
        calls["run_agentic"] = prompt
        return {"content": "x", "files_changed": []}

    monkeypatch.setattr(cli.registry, "handle_line", fake_handle)
    monkeypatch.setattr(cli, "run_agentic", fake_run)
    monkeypatch.setattr(cli.sys, "argv", argv)
    cli.main()
    return calls


def test_no_route_bypasses_router(monkeypatch):
    calls = _drive(monkeypatch, ["rob", "--no-route", "find all call sites of X"])
    assert "handle_line" not in calls
    assert calls["run_agentic"] == "find all call sites of X"


def test_json_non_slash_bypasses_router(monkeypatch):
    calls = _drive(monkeypatch, ["rob", "--json", "find all call sites of X"])
    assert "handle_line" not in calls
    assert calls["run_agentic"] == "find all call sites of X"


def test_json_slash_still_routes(monkeypatch):
    calls = _drive(monkeypatch, ["rob", "--json", "/status"])
    assert calls.get("handle_line") == "/status"
    assert calls["run_agentic"] == "ROUTED"
