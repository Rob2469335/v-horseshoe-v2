"""Regression: the coordinator tool-decision payload must be preflight-fit under
the model context budget.

Live defect (2026-09-12): the CLI replays its full session history
(`.session.json`, 70 messages / ~26k tokens) and the message trim preserved all
of it, so the coordinator's tool-decision request was ~26.6k tokens against every
model's 16,384 limit — every fallback failed. `_fit_tool_decision_messages`
deterministically bounds the payload (head+tail, must-have-preserving).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import runtime_v2.services._semantic_decision_cache as sdc
import runtime_v2.services.fallback_manager as fm
import runtime_v2.services.stream_runner as sr
from runtime_v2.services.stream_runner import (
    _CONTEXT_FIT_BUFFER,
    _CONTEXT_LIMIT,
    _OUTPUT_RESERVE,
    _estimate_msg_tokens,
    _fit_tool_decision_messages,
)

TASK = "analyze my codebase for bugs and upgrades"


def _budget(system_prompt: str) -> int:
    return (
        _CONTEXT_LIMIT
        - _OUTPUT_RESERVE
        - _estimate_msg_tokens({"content": system_prompt})
        - _CONTEXT_FIT_BUFFER
    )


def _oversized_history() -> list:
    """Shaped like the measured 70-message / ~26k-token session: a few huge
    prior assistant finals plus many small turns."""
    msgs = [
        {"role": "system", "content": "base system"},
        {"role": "user", "content": TASK},
        {"role": "assistant", "content": "x" * 16000},  # huge prior final
        {"role": "assistant", "content": "y" * 15000},  # huge prior final
        {"role": "assistant", "content": "q" * 12000},  # huge prior final
    ]
    for i in range(30):
        msgs.append({"role": "user", "content": f"step {i} " + "z" * 200})
        msgs.append({"role": "assistant", "content": f"ack {i} " + "w" * 200})
    msgs.append({"role": "user", "content": "now finish the task"})
    return msgs


def test_fit_brings_oversized_history_under_budget():
    msgs = _oversized_history()
    sysp = "COORDINATOR SYSTEM PROMPT\n" + "s" * 3000
    budget = _budget(sysp)
    unfitted = sum(_estimate_msg_tokens(m) for m in msgs if m.get("role") != "system")
    assert unfitted > budget, "fixture must actually overflow the budget"

    fitted = _fit_tool_decision_messages(msgs, sysp)
    non_sys = [m for m in fitted if m.get("role") != "system"]
    assert sum(_estimate_msg_tokens(m) for m in non_sys) <= budget
    # must-haves preserved: the first user task, a user anchor, the last turn.
    assert any(m.get("content") == TASK for m in non_sys)
    assert any(m.get("role") == "user" for m in non_sys)
    assert "now finish the task" in str(non_sys[-1].get("content", ""))


def test_fit_is_noop_when_payload_already_fits():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    assert _fit_tool_decision_messages(msgs, "sys") is msgs


def test_fit_never_emits_assistant_only_slice():
    msgs = [{"role": "user", "content": TASK}] + [
        {"role": "assistant", "content": "a" * 4000} for _ in range(20)
    ]
    fitted = _fit_tool_decision_messages(msgs, "s" * 2000)
    non_sys = [m for m in fitted if m.get("role") != "system"]
    assert any(m.get("role") == "user" for m in non_sys)


@pytest.mark.asyncio
async def test_get_tool_decision_payload_fits_budget(monkeypatch):
    captured: dict = {}

    async def fake_complete(litellm_model, base_messages, fallbacks, agent_id=None):
        captured["messages"] = base_messages
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"action":"final","response":"done"}'
                    )
                )
            ]
        )

    async def fake_fallbacks(mode="auto"):
        return []

    async def fake_cache(messages, agent_id):
        return None

    monkeypatch.setattr(sr, "complete_for_tool_decision", fake_complete)
    monkeypatch.setattr(
        sr, "get_litellm_model", lambda a, m: "deepseek/deepseek-v4-flash"
    )
    monkeypatch.setattr(sr, "get_routing_mode", lambda: "auto")
    monkeypatch.setattr(fm, "get_live_fallbacks", fake_fallbacks)
    monkeypatch.setattr(sdc, "get_semantic_cached_decision", fake_cache)

    await sr.get_tool_decision(
        "robs4b",
        _oversized_history(),
        "coordinator",
        allowed_tools=["final", "filesystem"],
    )

    payload = captured["messages"]
    assert payload, "the decision call must have been made"
    sysp = next((m["content"] for m in payload if m.get("role") == "system"), "")
    total = sum(_estimate_msg_tokens(m) for m in payload if m.get("role") != "system")
    assert total <= _budget(sysp), (
        f"tool-decision payload {total} exceeds budget {_budget(sysp)}"
    )


def _toks(ms) -> int:
    return sum(_estimate_msg_tokens(m) for m in ms if m.get("role") != "system")


def test_oversized_first_user_task_is_truncated_to_fit():
    sysp = "S" * 2000
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "T" * 60000},
    ]
    assert _toks(msgs) > _budget(sysp)  # one message alone overflows
    fitted = _fit_tool_decision_messages(msgs, sysp)
    assert _toks(fitted) <= _budget(sysp)
    head = next(m for m in fitted if m.get("role") == "user")
    assert "[truncated to fit the context window]" in head["content"]


def test_oversized_single_recent_message_is_truncated():
    sysp = "S" * 2000
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "x" * 60000},
    ]
    fitted = _fit_tool_decision_messages(msgs, sysp)
    assert _toks(fitted) <= _budget(sysp)
    assert any(
        "[truncated to fit the context window]" in str(m.get("content", ""))
        for m in fitted
    )


def test_elision_marker_digests_dropped_tools_and_errors():
    import json as _json

    sysp = "S" * 2000
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "TASK"},
        {
            "role": "assistant",
            "content": _json.dumps({"action": "filesystem", "operation": "read"}),
        },
        {"role": "user", "content": "TOOL RESULT (filesystem):\nError: not found"},
        {"role": "assistant", "content": _json.dumps({"action": "web_search"})},
    ]
    for _ in range(60):
        msgs.append({"role": "assistant", "content": "f" * 2000})
    msgs.append({"role": "user", "content": "LAST"})

    fitted = _fit_tool_decision_messages(msgs, sysp)
    marker = [m for m in fitted if str(m.get("content", "")).startswith("[system:")]
    assert marker, "expected an elision marker"
    assert "tools:" in marker[0]["content"]
    assert "error result" in marker[0]["content"]
