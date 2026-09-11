"""CLI stream-outcome taxonomy + observability (classes 1 & 2).

hermes-agent#102766: a clean EOF, a truncation, a network drop and a timeout
must NOT be conflated. Observability (arXiv:2607.09510): a malformed chunk must
be visible at the step it occurs, not silently dropped.
"""

from __future__ import annotations

import asyncio
import io
from unittest.mock import MagicMock

from rich.console import Console

from organism_console.ui import live_stream as ls


# ── class 2: classification (pure) ───────────────────────────────────────────
def test_clean_eof_with_final_is_ok():
    assert ls.classify_stream_end(True, False) == "ok"


def test_clean_eof_with_done_is_ok():
    assert ls.classify_stream_end(False, True) == "ok"


def test_no_final_no_done_is_truncated():
    assert ls.classify_stream_end(False, False) == "truncated"


def test_timeout_is_distinguished():
    assert ls.classify_stream_end(False, False, TimeoutError("slow")) == "timeout"


def test_network_drop_is_distinguished():
    class RequestError(Exception):
        pass

    RequestError.__module__ = "httpx"
    assert ls.classify_stream_end(False, False, RequestError("reset")) == "network"


def test_other_exception_is_error():
    assert ls.classify_stream_end(False, False, ValueError("boom")) == "error"


def test_messages_are_distinct():
    assert "truncated" in ls.stream_end_message("truncated").lower()
    assert "timed out" in ls.stream_end_message("timeout").lower()
    assert "network drop" in ls.stream_end_message("network").lower()


# ── class 1: a malformed chunk is visible; class 2: truncation is visible ────
class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aclose(self):
        pass


def _ctx(console: Console):
    ctx = MagicMock()
    ctx.console = console
    ctx.toasts_enabled = False
    ctx.trace_mode = False
    ctx.delegation_chain = ["coder"]
    ctx.active_agent = "coder"
    ctx.save = lambda: None
    return ctx


def _run(lines):
    console = Console(file=io.StringIO(), width=200, record=True)
    ctx = _ctx(console)
    resp = _FakeResp(lines)

    async def _fake_stream(url, method, payload):
        return resp

    old = ls.call_api_async_stream
    ls.call_api_async_stream = _fake_stream
    try:
        asyncio.run(ls._stream_prompt_async(ctx, "coder", "hi", []))
    finally:
        ls.call_api_async_stream = old
    return console.export_text()


def test_malformed_chunk_is_surfaced_not_swallowed():
    out = _run(["not-json-at-all", '{"type": "final", "content": "real report"}'])
    assert "malformed stream chunk" in out.lower()


def test_stream_without_final_is_reported_as_truncated():
    out = _run(['{"type": "tool_result", "ok": true}'])  # ends with no final/[DONE]
    assert "truncated" in out.lower()


# ── class 3: the continuation loop is bounded ────────────────────────────────
import json  # noqa: E402

_DENY_CHUNK = json.dumps(
    {
        "type": "approval_request",
        "agent_id": "coder",
        "pending_id": "p1",
        "authorization": "DENY",
        "preview": {"tool": "filesystem", "action": "write", "path": "x.py"},
        "checkpoint_id": None,
    }
)


def test_continuation_loop_is_bounded():
    # A backend that keeps returning an (auto-denied) approval must not loop
    # forever — the hard cap aborts the run.
    out = _run([_DENY_CHUNK])
    assert "too many consecutive" in out.lower()
