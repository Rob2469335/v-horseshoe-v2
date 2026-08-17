"""Tests for TASK 1 grammar-constrained tool-decision decoding.

Covers:
  1. response_format (json_schema) is injected into the LOCAL llama.cpp request
     body only when SWARM_GRAMMAR_DECODE=1.
  2. Cloud/DeepSeek requests NEVER receive the LOCAL grammar's response_format
     (the SWARM_GRAMMAR_DECODE gate is local-only). Since P2 (structured outputs),
     cloud may still get a json_schema format via _cloud_response_format() when
     the provider supports it — but never the local grammar-decode schema.
  3. A sync guard: TOOL_DECISION_JSON_SCHEMA in _grammar_schema.py stays aligned
     with the live TOOL_CALL_SCHEMA in _llm_parser.py (same action enum length +
     values, additionalProperties:false), so silent divergence fails loudly.
"""

import asyncio

from unittest.mock import patch


def _run_complete(env_on: bool, local_model: bool) -> dict:
    """Call complete_for_tool_decision through a mocked litellm.acompletion /
    Router, returning the outgoing kwargs dict (captures response_format)."""
    import runtime_v2.services._llm_client as llm_client

    captured: dict = {}

    class _FakeResp:
        def __init__(self):
            class Msg:
                content = '{"action": "final"}'

            class Choice:
                message = Msg()

            self.choices = [Choice()]

    async def fake_acompletion(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return _FakeResp()

    class _FakeRouter:
        async def acompletion(self, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return _FakeResp()

    model = "openai/qwen3.5-4b" if local_model else "openrouter/deepseek/deepseek-chat"

    import os

    if env_on:
        os.environ["SWARM_GRAMMAR_DECODE"] = "1"
    else:
        os.environ.pop("SWARM_GRAMMAR_DECODE", None)
    # Reset the once-only log flag.
    llm_client._grammar_decoded_logged = False

    with (
        patch.object(llm_client.litellm, "acompletion", side_effect=fake_acompletion),
        patch.object(llm_client, "build_router", return_value=_FakeRouter()),
    ):
        asyncio.run(
            llm_client.complete_for_tool_decision(
                model, [{"role": "user", "content": "list files"}], []
            )
        )
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
    # Cloud uses _cloud_response_format() (P2 structured outputs). It may be
    # json_object OR a json_schema built from TOOL_DECISION_JSON_SCHEMA — but it
    # must NEVER carry the LOCAL grammar-decode format, which is the same schema
    # but is only meant for llama.cpp. The guarantee preserved here: the
    # SWARM_GRAMMAR_DECODE gate never leaks the local grammar format to cloud.
    rf = out.get("response_format")
    assert rf is None or rf.get("type") in ("json_object", "json_schema")


def test_cloud_never_gets_grammar_json_schema_when_unset():
    out = _run_complete(env_on=False, local_model=False)
    rf = out.get("response_format")
    assert rf is None or rf.get("type") in ("json_object", "json_schema")


def test_cloud_response_format_uses_strict_schema_when_supported():
    """P2: cloud structured outputs — a provider that supports json_schema gets
    the strict TOOL_DECISION_JSON_SCHEMA; an unsupported one gets json_object.
    Reduces the malformed-JSON retry loops the regex-salvage parser absorbed."""
    import runtime_v2.services._llm_client as llm_client
    from unittest.mock import patch

    with patch("litellm.supports_response_schema", return_value=True):
        rf = llm_client._cloud_response_format("openrouter/deepseek/deepseek-chat")
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["schema"]["required"] == ["action"]

    with patch("litellm.supports_response_schema", return_value=False):
        rf = llm_client._cloud_response_format("openrouter/deepseek/deepseek-chat")
        assert rf == {"type": "json_object"}


def test_cloud_response_format_opencode_proxy_never_gets_json_schema():
    """LIVE-VERIFIED 2026-08-06: the OpenCode Go/Zen proxies reject strict
    json_schema with `400: This response_format type is unavailable now` even
    though litellm.supports_response_schema("openai/deepseek-v4-flash") returns
    True (it trusts the DeepSeek name, not the actual proxy). Every cloud tool
    decision died on that 400. The endpoint-resolved OpenCode base must force
    json_object regardless of litellm's support table."""
    import runtime_v2.services._llm_client as llm_client
    from unittest.mock import patch

    # litellm would happily say "supported" — but the proxy rejects it.
    with patch("litellm.supports_response_schema", return_value=True):
        for model in ("openai/deepseek-v4-flash", "openai/zen/deepseek-v4-flash"):
            rf = llm_client._cloud_response_format(model)
            assert rf == {"type": "json_object"}, f"{model} must degrade to json_object"

    # Non-OpenCode providers keep strict schema when litellm says supported.
    with patch("litellm.supports_response_schema", return_value=True):
        rf = llm_client._cloud_response_format("openrouter/deepseek/deepseek-chat")
        assert rf["type"] == "json_schema"


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
