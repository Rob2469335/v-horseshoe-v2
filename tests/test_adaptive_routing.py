"""Adaptive tool routing (arXiv:2608.13568).

Semantic (Serena/LSP) tool surfaces should be surfaced for REFERENCE/refactor
tasks, where grep is noisy; NOT for localization, where grep/file-read is
cheaper (forcing semantic costs tokens, +6-118% in the paper's measurement).
"""

from __future__ import annotations

import pytest

from runtime_v2.services.stream_runner import is_reference_task, is_research_task, research_source


@pytest.mark.parametrize(
    "text",
    [
        "find all references to AgentServiceV2",
        "who calls get_tool_decision?",
        "list every caller of step_agent_stream",
        "refactor the file to rename the class",
        "change the signature of run and fix its callers",
        "show the dependents of this module",
        "find importers of agent_service_v2",
    ],
)
def test_reference_tasks_detected(text):
    assert is_reference_task(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "where is AgentServiceV2 defined",
        "analyze my codebase for bugs and upgrades",
        "read runtime_v2/api/agent_service_v2.py",
        "summarize the README",
        "",
    ],
)
def test_localization_tasks_not_flagged(text):
    assert is_reference_task(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "what's the latest qwen model release",
        "find papers on speculative decoding",
        "research the SOTA agent architectures",
        "look up the hugging face model card for qwen3.5",
        "compare benchmarks for open models",
        "what's new in agent frameworks",
    ],
)
def test_research_tasks_detected(text):
    assert is_research_task(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "read runtime_v2/api/agent_service_v2.py",
        "fix the bug in the parser",
        "run the tests",
        "",
    ],
)
def test_non_research_tasks_not_flagged(text):
    assert is_research_task(text) is False


# ── research source routing ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        ("research the latest papers on agent architectures", "arxiv"),
        ("find the newest Qwen models on hugging face", "huggingface"),
        ("look up an open-source repo for speech diarization", "github"),
        ("crawl the docs site and extract the API table", "firecrawl"),
    ],
)
def test_research_routes_to_specialized_source(text, expected):
    assert research_source(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "analyze my codebase for bugs and upgrades",
        "hello",
        "read runtime_v2/api/agent_service_v2.py",
    ],
)
def test_non_research_tasks_have_no_preferred_source(text):
    assert research_source(text) is None
