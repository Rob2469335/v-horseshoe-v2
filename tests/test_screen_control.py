"""Tests for the screen-control tool (computer-use tier).

Human-control mode is the DEFAULT: the swarm may screenshot and read the screen,
but any mouse/keyboard input action is blocked until SWARM_SCREEN_AUTONOMOUS=1
(or set_screen_autonomous(True)). An action cap stops runaway loops.
"""

from __future__ import annotations
import sys
import pytest

# The screen-control module uses ctypes.windll (Windows-only) for mouse/keyboard
# input. On non-Windows runners (CI) it imports fine (graceful "not supported")
# but the functional tests below assert real win32 behavior, so skip them there.
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="screen-control tests require Windows (ctypes.windll)",
)

from swarm_os.lib.mcp import screen
from swarm_os.lib.mcp.screen import screen_handler
from runtime_v2.services import tool_executor
from runtime_v2.prompts.system_prompts import build as build_system_prompt


@pytest.fixture(autouse=True)
def _reset_mode(monkeypatch):
    screen.SCREEN_AUTONOMOUS = False
    screen._screen_action_count = 0
    # Build 2 per-app OS tier: autonomous mode alone no longer enables input on
    # an un-granted app (fail-closed view-only). Grant the foreground app a
    # full-control tier so the human-control/action-cap tests exercise THEIR
    # gates, not the per-app tier gate (which has its own dedicated test).
    monkeypatch.setattr(screen, "_app_tier", lambda: "full-control")
    yield
    screen.SCREEN_AUTONOMOUS = False
    screen._screen_action_count = 0


def test_per_app_tier_blocks_ungranted_app_even_in_autonomous(monkeypatch):
    """Build 2 fail-closed: with no grant for the foreground app, input is
    blocked even in autonomous mode — the per-app tier is NOT implied by the
    screen tool's approval base tier."""
    monkeypatch.setattr(screen, "_app_tier", lambda: "view-only")
    screen.set_screen_autonomous(True)
    res = screen_handler({"action": "left_click", "x": 0, "y": 0})
    assert res.get("ok") is False
    assert "PER-APP TIER" in res.get("error", "")
    assert "view-only" in res.get("error", "")


def test_human_control_blocks_input_by_default():
    res = screen_handler({"action": "left_click", "x": 10, "y": 10})
    assert res.get("ok") is False
    assert "HUMAN-CONTROL MODE" in res.get("error", "")
    # read-only stays allowed
    assert screen_handler({"action": "cursor_position"}).get("ok") is True


def test_all_input_actions_gated():
    for action in (
        "mouse_move",
        "right_click",
        "double_click",
        "scroll",
        "type",
        "key",
    ):
        res = screen_handler(
            {"action": action, "x": 5, "y": 5, "text": "x", "name": "enter"}
        )
        assert res.get("ok") is False, f"{action} should be blocked"
        assert "HUMAN-CONTROL MODE" in res.get("error", "")


def test_set_autonomous_enables_input():
    screen.set_screen_autonomous(True)
    res = screen_handler({"action": "left_click", "x": 0, "y": 0})
    assert res.get("ok") is True
    assert res["result"]["action"] == "click"


def test_action_cap_guards_runaway_loop():
    screen.set_screen_autonomous(True)
    cap = screen._SCREEN_MAX_ACTIONS
    for _ in range(cap):
        screen._spend_action("key")
    res = screen_handler({"action": "key", "name": "enter"})
    assert res.get("ok") is False
    assert "Action cap" in res.get("error", "")
    screen.reset_screen_action_count()
    assert screen._screen_action_count == 0


def test_foreground_window_readable():
    res = screen_handler({"action": "foreground_window"})
    assert res.get("ok") is True
    assert "title" in res["result"]


def test_list_windows_readable():
    res = screen_handler({"action": "list_windows", "max_results": 5})
    assert res.get("ok") is True
    assert isinstance(res["result"]["windows"], list)


def test_screenshot_returns_png_path():
    res = screen_handler({"action": "screenshot"})
    assert res.get("ok") is True
    result = res["result"]
    assert result["path"].endswith(".png")
    assert result["width"] > 0
    assert result["height"] > 0


def test_unknown_action_error():
    res = screen_handler({"action": "explode"})
    assert res.get("ok") is False
    assert "Available" in res.get("error", "")


@pytest.mark.asyncio
async def test_tool_executor_dispatches_screen():
    res = await tool_executor.run("screen", {"action": "cursor_position"})
    assert res.get("ok") is True
    assert "x" in res["result"]
    # input still gated end-to-end
    res2 = await tool_executor.run("screen", {"action": "left_click", "x": 5, "y": 5})
    assert res2.get("ok") is False


def test_screen_tool_in_system_prompt():
    prompt = build_system_prompt("code_analyzer")
    assert "action=screen" in prompt
    assert "HUMAN-CONTROL" in prompt or "human-control" in prompt
    assert "action=screen" not in build_system_prompt("coordinator")
