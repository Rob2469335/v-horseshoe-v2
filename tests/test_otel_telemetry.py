"""Opt-in GenAI-convention telemetry (no required dependency)."""

from __future__ import annotations

import json

import runtime_v2.services.otel_telemetry as ot
import runtime_v2.services.usage_log as ul


def test_disabled_by_default_is_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("SWARM_OTEL", raising=False)
    monkeypatch.setattr(ot, "_telemetry_path", tmp_path / "otel.jsonl")
    ot.emit_llm_span(
        model="m", provider="p", prompt_tokens=10, completion_tokens=5, cost=0.01
    )
    assert not (tmp_path / "otel.jsonl").exists()


def test_enabled_writes_genai_record(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_OTEL", "1")
    monkeypatch.setattr(ot, "_telemetry_path", tmp_path / "otel.jsonl")
    ot.emit_llm_span(
        model="deepseek/deepseek-v4-flash",
        provider="deepseek",
        prompt_tokens=100,
        completion_tokens=40,
        cost=0.0012,
        source="tool_decision",
        agent_id="code_analyzer",
    )
    recs = [
        json.loads(line)
        for line in (tmp_path / "otel.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(recs) == 1
    assert recs[0]["name"] == "gen_ai.chat"
    a = recs[0]["attributes"]
    assert a["gen_ai.usage.input_tokens"] == 100
    assert a["gen_ai.usage.output_tokens"] == 40
    assert a["gen_ai.request.model"] == "deepseek/deepseek-v4-flash"
    assert a["gen_ai.provider.name"] == "deepseek"
    assert a["gen_ai.usage.cost"] == 0.0012


def test_fail_open_when_attr_build_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_OTEL", "1")
    monkeypatch.setattr(ot, "_telemetry_path", tmp_path / "otel.jsonl")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ot, "_genai_attributes", _boom)
    ot.emit_llm_span(model="m")  # must not raise


def test_record_usage_emits_genai_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_OTEL", "1")
    monkeypatch.setattr(ot, "_telemetry_path", tmp_path / "otel.jsonl")
    monkeypatch.setattr(ul, "_USAGE_PATH", tmp_path / "usage.jsonl")
    ul.record_usage(
        model="deepseek/deepseek-v4-flash",
        prompt_tokens=12,
        completion_tokens=3,
        source="tool_decision",
        agent_id="coordinator",
    )
    recs = [
        json.loads(line)
        for line in (tmp_path / "otel.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert recs and recs[0]["attributes"]["gen_ai.usage.input_tokens"] == 12
