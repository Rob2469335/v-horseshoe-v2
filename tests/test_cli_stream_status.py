"""CLI final-verification: a system-termination final must NOT render as success.

Terminal Agents survey: state-tracking error 73.1%, final-window verification
0.0%. The CLI rendered EVERY final in a green success Panel and set
last_stream_status='completed' — including "Task FAILED", "[System: max turns
reached]" and "Healing failed. Loop aborted.". This pins the fix.
"""

from __future__ import annotations

import pytest

from organism_console.ui.live_stream import _final_is_system_failure, final_panel


@pytest.mark.parametrize(
    "text",
    [
        "Task FAILED: code_analyzer could not produce a substantive, grounded final.",
        "[System: max turns reached]",
        "Healing failed. Loop aborted.",
        "Healing failed. Manual intervention required.",
        "Task aborted after 3 LLM failures: timeout",
        "Task aborted after 3 consecutive errors.",
    ],
)
def test_system_failure_finals_detected(text):
    assert _final_is_system_failure(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "## Diagnosis\nruntime_v2/api/_agent_state.py — read budget fields",
        "Codebase analysis - grounded report.\nExamined 3 file(s):",
        "",
        "Done reviewing; two real issues found in stream_runner.py.",
    ],
)
def test_real_finals_not_flagged(text):
    assert _final_is_system_failure(text) is False


def test_system_failure_renders_red_panel():
    panel = final_panel("[System: max turns reached]")
    assert panel.border_style == "red"


def test_real_final_renders_green_panel():
    panel = final_panel("## Findings\nall good")
    assert panel.border_style == "green"
