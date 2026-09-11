"""Parser tolerance for reasoning models that emit MULTIPLE JSON objects.

2026-09-10 root cause (live): deepseek-v4-flash on the code_analyzer
tool-decision call "thinks in JSON" — it emits its internal thought/observation
objects followed by the real tool call. The parser's stacked-tag guard RAISED on
that shape, discarding a valid decision; the caller retried the LLM, got another
multi-object response, and the agent fell back to repeated filesystem reads until
max turns ("analyze my codebase for bugs and upgrades" loop). The fix selects the
LAST actionable object instead of raising.
"""

from runtime_v2.services._llm_parser import extract_json


def test_multiple_json_objects_selects_last_actionable():
    text = (
        '{"thought": "The goal asks for bugs and upgrades. Look at the agent loop."}\n'
        '{"observation": "I need tool execution first"}\n'
        '{"action": "filesystem", "operation": "read", "path": "runtime_v2/a.py"}'
    )
    out = extract_json(text)
    assert out["action"] == "filesystem"
    assert out["operation"] == "read"
    assert out["path"] == "runtime_v2/a.py"


def test_multiple_objects_prefers_final_when_last():
    text = (
        '{"thought": "I have enough now"}\n'
        '{"action": "final", "response": "the report"}'
    )
    out = extract_json(text)
    assert out["action"] == "final"
    assert out["response"] == "the report"


def test_single_object_unchanged():
    out = extract_json('{"action": "final", "response": "ok"}')
    assert out == {"action": "final", "response": "ok"}


def test_only_non_action_objects_do_not_raise():
    # A response of scratch objects with no explicit action must NOT raise —
    # normalize_decision coerces the last object to a valid decision (final),
    # so the turn is not discarded.
    text = '{"thought": "still thinking"}\n{"note": "more"}'
    out = extract_json(text)
    assert out["action"] == "final"
