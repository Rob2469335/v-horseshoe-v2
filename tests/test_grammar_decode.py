"""Tests for TASK 1 grammar-constrained tool-decision decoding.

Covers:
  1. response_format (json_schema) is injected into the LOCAL llama.cpp request
     body only when SWARM_GRAMMAR_DECODE=1.
  2. Cloud/DeepSeek requests NEVER receive response_format, regardless of
     SWARM_GRAMMAR_DECODE.
  3. A sync guard: TOOL_DECISION_JSON_SCHEMA in _grammar_schema.py stays aligned
     with the live TOOL_CALL_SCHEMA in _llm_parser.py (same action enum length +
     values, additionalProperties:false), so silent divergence fails loudly.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, patch


def _run_complete(env_on: bool, local_model: bool) -> dict:
    """Call complete_for_tool_decision through a mocked litellm.acompletion,
    returning the outgoing top-level kwargs dict (captures response_format)."""
    import runtime_v2.services._llm_client as llm_client
    import litellm

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.clear()
        captured.update(kwargs)

        class Msg:
            content = '{"action": "final"}'
        class Choice:
            message = Msg()
        class Resp:
            choices = [Choice()]
        return Resp()

    model = "openai/qwen3.5-9b" if local_model else "openrouter/deepseek/deepseek-chat"

    import os
    if env_on:
        os.environ["SWARM_GRAMMAR_DECODE"] = "1"
    else:
        os.environ.pop("SWARM_GRAMMAR_DECODE", None)
    # Reset the once-only log flag.
    llm_client._grammar_decoded_logged = False

    with patch.object(llm_client.litellm, "acompletion", side_effect=fake_acompletion):
        asyncio.run(llm_client.complete_for_tool_decision(
            model, [{"role": "user", "content": "list files"}], []
        ))
    return dict(captured)


def test_local_gets_response_format_when_enabled():
    out = _run_complete(env_on=True, local_model=True)
    rf = out.get("response_format")
    assert rf is not None
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["required"] == ["action"]


def test_local_no_response_format_when_unset():
    out = _run_complete(env_on=False, local_model=True)
    assert "response_format" not in out


def test_cloud_never_gets_grammar_json_schema_even_when_enabled():
    out = _run_complete(env_on=True, local_model=False)
    # Cloud already sets response_format {"type":"json_object"} today — the
    # guarantee is it must NEVER carry the grammar's json_schema decoding.
    rf = out.get("response_format")
    assert rf is None or rf.get("type") != "json_schema"


def test_cloud_never_gets_grammar_json_schema_when_unset():
    out = _run_complete(env_on=False, local_model=False)
    rf = out.get("response_format")
    assert rf is None or rf.get("type") != "json_schema"


def test_schema_remains_synced():
    """Guard: _grammar_schema.py must stay byte-aligned with the parser schema."""
    from runtime_v2.services._grammar_schema import TOOL_DECISION_JSON_SCHEMA as gs
    from runtime_v2.services._llm_parser import TOOL_CALL_SCHEMA as ps

    assert gs["additionalProperties"] is False
    assert gs["required"] == ["action"]

    assert gs["properties"]["action"]["enum"] == ps["properties"]["action"]["enum"]
    assert len(gs["properties"]["action"]["enum"]) == 13

    assert set(gs["properties"].keys()) == set(ps["properties"].keys())
    for key in gs["properties"]:
        if key == "action":
            continue  # enum compared above
        assert gs["properties"][key] == ps["properties"][key], (
            f"schema drift in property '{key}'"
        )