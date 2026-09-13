"""CLI must render an agent final exactly once — no duplicate report panel.

The backend emits the final's content as a plain content chunk AND as the typed
`final` chunk. The CLI accumulates the plain chunk into `full_content` and
renders it in the dim Live panel; the `final` chunk then prints the green
final_panel. Without clearing the Live panel first, the same grounded report is
left on screen twice (dim panel + green panel) — the observed duplicate. This
drives the REAL `_stream_prompt_async` and pins: (a) the final panel prints once,
(b) the Live panel is cleared before it stops.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from rich.panel import Panel
from rich.text import Text

import organism_console.ui.live_stream as ls

REPORT = "Codebase analysis — grounded report.\nExamined 3 file(s):\n- a.py"


class _FakeConsole:
    def __init__(self):
        self.prints = []

    def print(self, *args, **kwargs):
        self.prints.append(args[0] if args else None)


class _FakeLive:
    instances = []

    def __init__(self, *args, **kwargs):
        self.updates = []
        self.stopped = False
        _FakeLive.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def update(self, renderable, *args, **kwargs):
        self.updates.append(renderable)

    def stop(self):
        self.stopped = True


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_final_rendered_once_and_live_cleared(monkeypatch):
    lines = [
        json.dumps({"agent_id": "code_analyzer", "content": REPORT, "model": "m"}),
        json.dumps(
            {
                "agent_id": "code_analyzer",
                "type": "final",
                "provider": "llama.cpp",
                "content": REPORT,
            }
        ),
        "[DONE]",
    ]

    async def _fake_stream(path, method, payload):
        return _FakeResp(lines)

    monkeypatch.setattr(ls, "call_api_async_stream", _fake_stream)
    monkeypatch.setattr(ls, "Live", _FakeLive)
    monkeypatch.setattr(ls, "update_token_metrics", lambda *a, **k: None)
    monkeypatch.setattr(ls, "record_chunk", lambda *a, **k: None)
    monkeypatch.setattr(
        ls,
        "get_system_stats",
        lambda: {"ram_pct": 10, "ram_color": "green"},
    )

    console = _FakeConsole()
    ctx = SimpleNamespace(
        console=console,
        save=lambda: None,
        history=[],
        history_pointer=0,
        delegation_chain=None,
        last_stream_status=None,
        last_provider=None,
        resume_checkpoint_id=None,
        active_model="m",
        current_topic="",
        current_summary="",
        strategic_intent="",
        toasts_enabled=False,
    )

    await ls._stream_prompt_async(ctx, "code_analyzer", "analyze", [])

    # (a) The report is printed exactly ONCE as a panel.
    report_panels = [
        p
        for p in console.prints
        if isinstance(p, Panel) and REPORT.splitlines()[0] in _panel_text(p)
    ]
    assert len(report_panels) == 1, f"expected 1 final panel, got {len(report_panels)}"

    # (b) The dim Live panel was cleared (empty Text update) before it stopped,
    # so the streamed copy of the report does not linger as a second panel.
    live = _FakeLive.instances[-1]
    assert live.stopped is True
    assert live.updates, "expected Live updates"
    last = live.updates[-1]
    assert isinstance(last, Text), f"expected the pre-stop clear, got {type(last)!r}"
    assert last.plain == ""


def _panel_text(panel) -> str:
    renderable = panel.renderable
    text = getattr(renderable, "markup", None) or getattr(renderable, "text", None)
    if text:
        return str(text)
    return str(renderable)
