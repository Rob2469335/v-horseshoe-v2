"""Two-call structured findings extraction (2026-09-10).

Call 1 = free reasoning (the agent's forced final). Call 2 = a dedicated
`json_object`-mode EXTRACTION over that reasoning + the read ledger, so syntax
cannot fail and every returned file is validated against the ledger. Any
failure returns {} and the deterministic grounded report fires (fail-safe).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import runtime_v2.services._llm_client as llm
from runtime_v2.api._agent_helpers import _extract_grounded_findings


def _resp(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# ── Call 2 must request json_object mode ─────────────────────────────────────
@pytest.mark.asyncio
async def test_complete_json_extraction_requests_json_object_mode():
    with patch.object(
        llm.litellm, "acompletion", new=AsyncMock(return_value=_resp('{"findings": []}'))
    ) as mock_call:
        out = await llm.complete_json_extraction(
            "deepseek/deepseek-v4-flash",
            [{"role": "user", "content": "x"}],
            agent_id="code_analyzer",
        )
    assert out == '{"findings": []}'
    sent = mock_call.call_args.kwargs
    assert sent.get("response_format") == {"type": "json_object"}, sent


# ── extraction: valid findings kept, unread files dropped ────────────────────
@pytest.mark.asyncio
async def test_extraction_keeps_only_ledger_files():
    payload = (
        '{"findings": ['
        '{"file": "runtime_v2/api/_agent_state.py", "finding": "read budget not reset"},'
        '{"file": "models.py", "finding": "unused var line 56"}'
        "]}"
    )
    with patch.object(
        llm, "complete_json_extraction", new=AsyncMock(return_value=payload)
    ):
        out = await _extract_grounded_findings(
            "m", "code_analyzer", "some reasoning", {"runtime_v2/api/_agent_state.py"}
        )
    assert out == {"runtime_v2/api/_agent_state.py": "read budget not reset"}
    assert "models.py" not in out


@pytest.mark.asyncio
async def test_extraction_resolves_basename_to_ledger_path():
    payload = '{"findings": [{"file": "agent_service_v2.py", "finding": "big"}]}'
    with patch.object(
        llm, "complete_json_extraction", new=AsyncMock(return_value=payload)
    ):
        out = await _extract_grounded_findings(
            "m", "code_analyzer", "r", {"runtime_v2/api/agent_service_v2.py"}
        )
    assert out == {"runtime_v2/api/agent_service_v2.py": "big"}


# ── fail-safe: any failure -> {} so the deterministic report fires ───────────
@pytest.mark.asyncio
async def test_extraction_llm_error_returns_empty():
    with patch.object(
        llm, "complete_json_extraction", new=AsyncMock(side_effect=RuntimeError("down"))
    ):
        out = await _extract_grounded_findings("m", "a", "r", {"x.py"})
    assert out == {}


@pytest.mark.asyncio
async def test_extraction_bad_json_returns_empty():
    with patch.object(
        llm, "complete_json_extraction", new=AsyncMock(return_value="not json at all")
    ):
        out = await _extract_grounded_findings("m", "a", "r", {"x.py"})
    assert out == {}


@pytest.mark.asyncio
async def test_extraction_blank_reasoning_returns_empty_without_call():
    # Blank reasoning is NOT a failure — it must return an empty list without
    # even making the extraction call.
    with patch.object(llm, "complete_json_extraction", new=AsyncMock()) as mock_call:
        out = await _extract_grounded_findings("m", "a", "", {"x.py"})
    assert out == {}
    mock_call.assert_not_awaited()
