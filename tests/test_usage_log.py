"""Tests for runtime_v2/services/usage_log.py — durable per-model token & cost telemetry.

Covers:
  1. Provider classification (deepseek direct / openrouter / local / paid).
  2. Cost estimation: cache-hit discount, local = $0, unknown cloud = None (honest).
  3. extract_usage normalizes litellm usage objects/dicts (incl. cached_tokens).
  4. record_usage appends JSONL under the configured path; usage_report aggregates.
  5. Telemetry never raises (write to an invalid path is swallowed).
"""
import json
from pathlib import Path

import pytest

from runtime_v2.services import usage_log


def test_provider_classification():
    cases = {
        "deepseek/deepseek-v4-flash": "deepseek_direct",
        "deepseek/deepseek-chat": "deepseek_direct",
        "openrouter/deepseek/deepseek-chat": "openrouter",
        "openai/qwen3.5-4b": "local",
        "openai/deepseek-chat": "openai_paid",
        "groq/llama-3.3-70b-versatile": "groq",
        "gemini/gemini-2.0-flash": "gemini",
        "qwen3.5-4b": "local",
        "openai/o3-mini": "openai_paid",
    }
    for model, expected in cases.items():
        assert usage_log._provider_of(model) == expected, model


def test_cost_estimation_cache_hit_discount():
    # 10K input all miss + 2K output: 10000/1e6*0.14 + 2000/1e6*0.28
    miss = usage_log.estimate_cost("deepseek/deepseek-v4-flash", 10000, 2000, 0)
    assert miss == pytest.approx(0.00196, rel=1e-3)
    # 9K of the 10K prompt cached: 1000*0.14 + 9000*0.0028 + 2000*0.28 / 1e6
    hit = usage_log.estimate_cost("deepseek/deepseek-v4-flash", 10000, 2000, 9000)
    assert hit == pytest.approx(0.0007252, rel=1e-3)
    assert hit < miss  # cache hits are cheaper


def test_cost_local_is_free_and_unknown_is_none():
    assert usage_log.estimate_cost("openai/qwen3.5-4b", 10000, 2000, 0) == 0.0
    assert usage_log.estimate_cost("qwen3.5-4b", 10000, 2000, 0) == 0.0
    assert usage_log.estimate_cost("gemini/gemini-2.0-flash", 10000, 2000, 0) is None


def test_extract_usage_object_and_dict():
    class Usage:
        def __init__(self):
            self.prompt_tokens = 100
            self.completion_tokens = 50
            self.total_tokens = 150
            self.prompt_tokens_details = {"cached_tokens": 40}

    class Resp:
        usage = Usage()

    assert usage_log.extract_usage(Resp()) == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cached_tokens": 40,
    }
    assert usage_log.extract_usage({"usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}) == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "cached_tokens": 0,
    }
    assert usage_log.extract_usage(object()) is None
    assert usage_log.extract_usage(None) is None


def test_record_usage_roundtrip(tmp_path: Path):
    log_path = tmp_path / "usage.jsonl"
    orig = usage_log._USAGE_PATH
    usage_log._USAGE_PATH = log_path
    try:
        usage_log.record_usage(
            model="deepseek/deepseek-v4-flash",
            prompt_tokens=10000,
            completion_tokens=2000,
            cached_tokens=9000,
            source="test",
            agent_id="researcher",
        )
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["model"] == "deepseek/deepseek-v4-flash"
        assert row["provider"] == "deepseek_direct"
        assert row["cost"] == pytest.approx(0.0007252, rel=1e-3)
        assert row["agent_id"] == "researcher"

        report = usage_log.usage_report(days=30)
        assert report["rows"] == 1
        assert report["known_cost"] == pytest.approx(0.0007252, rel=1e-3)
        assert report["per_model"]["deepseek/deepseek-v4-flash"]["calls"] == 1
    finally:
        usage_log._USAGE_PATH = orig


def test_telemetry_never_raises(tmp_path: Path):
    # A path we cannot write (parent is a file) must be swallowed, not raised.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    usage_log._USAGE_PATH = blocker / "usage.jsonl"
    try:
        usage_log.record_usage(model="openai/qwen3.5-4b", prompt_tokens=5, completion_tokens=5)
        usage_log.record_response({"usage": None}, "openai/qwen3.5-4b")  # no usage -> skip
    finally:
        usage_log._USAGE_PATH = tmp_path / "usage.jsonl"


def test_record_response_skips_no_usage():
    class Msg:
        content = "hi"
    class Choice:
        message = Msg()
    class Resp:
        choices = [Choice()]  # no .usage attribute

    # Should be a no-op (no usage present), not raise.
    usage_log.record_response(Resp(), "deepseek/deepseek-v4-flash", source="test")
