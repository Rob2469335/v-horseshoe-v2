# tests/test_nl_prompt_routing.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from organism_console.command_registry import registry, route_natural_language_keywords
from organism_console.cli import build_command_context


def test_route_natural_language_keywords_analyze_prompt():
    prompt = "analyze my codebase for bugs and search internet for improvments and upgrades"
    cmd, args = route_natural_language_keywords(prompt)
    assert cmd == "goal"
    assert args == [prompt]


def test_handle_line_end_to_end_routing():
    ctx = build_command_context()
    mock_goal_loop = MagicMock()
    ctx.run_goal_loop = mock_goal_loop

    prompt = "analyze my codebase for bugs and search internet for improvments and upgrades"
    registry.handle_line(prompt, ctx)

    mock_goal_loop.assert_called_once_with(prompt)


def test_handle_line_upgrade_command():
    ctx = build_command_context()
    mock_goal_loop = MagicMock()
    ctx.run_goal_loop = mock_goal_loop

    registry.handle_line("/upgrade analyze my codebase for bugs and search internet for improvments and upgrades", ctx)

    mock_goal_loop.assert_called_once()
    call_arg = mock_goal_loop.call_args[0][0]
    assert "web_search" in call_arg
    assert "analyze my codebase for bugs" in call_arg
