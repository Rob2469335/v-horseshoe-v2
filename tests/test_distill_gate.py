from __future__ import annotations
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.asyncio
async def test_distill_skips_model_variability():
    from swarm_os.services.reflection_loop import _distill

    mock_acompletion = AsyncMock()
    with patch("swarm_os.services.reflection_loop.acompletion", mock_acompletion):
        result = await _distill("some failure content", fix_class="model_variability")
    assert result == ""
    mock_acompletion.assert_not_called()


@pytest.mark.asyncio
async def test_distill_runs_for_prompt_sensitivity():
    from swarm_os.services.reflection_loop import _distill

    mock_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="reflection rule here"))
        ]
    )
    mock_acompletion = AsyncMock(return_value=mock_resp)
    with patch("swarm_os.services.reflection_loop.acompletion", mock_acompletion):
        with patch.dict("os.environ", {}, clear=True):
            result = await _distill(
                "some failure content", fix_class="prompt_sensitivity"
            )
    assert result == "reflection rule here"
    mock_acompletion.assert_called()


@pytest.mark.asyncio
async def test_distill_runs_for_none_fix_class():
    from swarm_os.services.reflection_loop import _distill

    mock_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="distilled rule"))]
    )
    mock_acompletion = AsyncMock(return_value=mock_resp)
    with patch("swarm_os.services.reflection_loop.acompletion", mock_acompletion):
        with patch.dict("os.environ", {}, clear=True):
            result = await _distill("some failure", fix_class=None)
    assert result == "distilled rule"
    mock_acompletion.assert_called()


@pytest.mark.asyncio
async def test_run_reflection_classifies_mv_skips_distill():
    """Full upstream wire: diary error → Diagnostician.classify → _distill skip."""
    from swarm_os.services.reflection_loop import run_reflection

    with tempfile.TemporaryDirectory() as tmp:
        diary_path = Path(tmp) / "test_diary.jsonl"
        # Write a diary entry with a model_variability-like error
        diary_path.write_text(
            json.dumps(
                {
                    "task": "test task",
                    "content_preview": "I don't know",
                    "error": "hallucinated wrong answer",
                    "component": "code_analyzer",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        mock_acompletion = AsyncMock()
        mock_acompletion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="rule"))]
        )

        with patch("swarm_os.services.reflection_loop.DIARY_PATH", diary_path):
            with patch(
                "swarm_os.services.reflection_loop.acompletion", mock_acompletion
            ):
                with patch.dict("os.environ", {}, clear=True):
                    await run_reflection()

        # MV is matched by "hallucin" in diagnostician → _distill skips
        mock_acompletion.assert_not_called()


@pytest.mark.asyncio
async def test_run_reflection_classifies_ps_runs_distill():
    """Full upstream wire: diary error → Diagnostician → _distill runs for PS."""
    from swarm_os.services.reflection_loop import run_reflection

    with tempfile.TemporaryDirectory() as tmp:
        diary_path = Path(tmp) / "test_diary_ps.jsonl"
        diary_path.write_text(
            json.dumps(
                {
                    "task": "test task",
                    "content_preview": "invalid json output",
                    "error": "malformed json parse error",
                    "component": "coder",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        mock_acompletion = AsyncMock()
        mock_acompletion.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="a reflection rule"))
            ]
        )

        with patch("swarm_os.services.reflection_loop.DIARY_PATH", diary_path):
            with patch(
                "swarm_os.services.reflection_loop.acompletion", mock_acompletion
            ):
                with patch.dict("os.environ", {}, clear=True):
                    await run_reflection()

        mock_acompletion.assert_called()
