"""`complete_json_extraction` must honor a caller's token cap and never lose a
JSON result to a reasoning-heavy model.

Live regression (2026-09-12): the deep-analysis findings synthesis sends the
verbatim read content of every file. `deepseek/deepseek-v4-flash` then spends
the whole (1500-token) cap on `reasoning_content` and returns `content=""`
(`finish_reason="length"`), so `_extract_grounded_findings` fell back to a bare
file manifest. Fix: the caller passes a larger cap, and if content is STILL
empty the JSON object is salvaged from `reasoning_content`.
"""

from __future__ import annotations

import pytest

import runtime_v2.services._llm_client as llc


class _Msg:
    def __init__(self, content="", reasoning_content=""):
        self.content = content
        self.reasoning_content = reasoning_content


class _Choice:
    def __init__(self, message):
        self.message = message


class _Resp:
    def __init__(self, message):
        self.choices = [_Choice(message)]


def _patch(monkeypatch, resp):
    captured = {}

    def fake_build_kwargs(model, extra, fallbacks):
        captured["extra"] = dict(extra)
        return {"model": model, "messages": extra["messages"]}

    async def fake_acompletion(**kwargs):
        return resp

    monkeypatch.setattr(llc, "build_kwargs", fake_build_kwargs)
    monkeypatch.setattr(llc.litellm, "acompletion", fake_acompletion)
    return captured


@pytest.mark.asyncio
async def test_salvages_json_from_reasoning_when_content_empty(monkeypatch):
    resp = _Resp(
        _Msg(
            content="",
            reasoning_content=(
                "Let me think... the answer is "
                '{"findings": [{"file": "a.py", "finding": "unguarded call"}]}'
                " ...done."
            ),
        )
    )
    _patch(monkeypatch, resp)
    out = await llc.complete_json_extraction(
        "deepseek/deepseek-v4-flash",
        [{"role": "user", "content": "review"}],
        agent_id="code_analyzer",
        max_tokens=8000,
    )
    assert '"findings"' in out
    assert "unguarded call" in out


@pytest.mark.asyncio
async def test_content_is_preferred_over_reasoning(monkeypatch):
    resp = _Resp(
        _Msg(
            content='{"findings": []}',
            reasoning_content='{"findings": [{"file": "noise.py"}]}',
        )
    )
    _patch(monkeypatch, resp)
    out = await llc.complete_json_extraction(
        "deepseek/deepseek-v4-flash", [{"role": "user", "content": "x"}]
    )
    assert out == '{"findings": []}'


@pytest.mark.asyncio
async def test_caller_max_tokens_is_forwarded(monkeypatch):
    captured = _patch(monkeypatch, _Resp(_Msg(content='{"findings": []}')))
    await llc.complete_json_extraction(
        "deepseek/deepseek-v4-flash",
        [{"role": "user", "content": "x"}],
        max_tokens=8000,
    )
    assert captured["extra"]["max_tokens"] == 8000
