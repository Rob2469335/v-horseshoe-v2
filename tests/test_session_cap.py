"""Regression: the persisted CLI session history must stay bounded.

Live defect (2026-09-12): `organism_console/.session.json` grew to 70 messages /
~103k chars (~26k tokens), dominated by huge prior assistant finals, and — being
replayed into every coordinator tool-decision — overflowed every model's 16,384
context limit. `_cap_history` caps the persisted history head+tail on write.
"""

from __future__ import annotations

import json

from organism_console.state_store import (
    _SESSION_MAX_CHARS,
    _SESSION_MAX_MESSAGES,
    SessionState,
    _cap_history,
)

TASK = "analyze my codebase for bugs and upgrades"


def _oversized_history() -> list:
    msgs = [
        {"role": "user", "content": TASK},
        {"role": "assistant", "content": "x" * 16000},
        {"role": "assistant", "content": "y" * 15000},
        {"role": "assistant", "content": "q" * 12000},
    ]
    for i in range(30):
        msgs.append({"role": "user", "content": f"step {i} " + "z" * 300})
        msgs.append({"role": "assistant", "content": f"ack {i} " + "w" * 300})
    msgs.append({"role": "user", "content": "final turn"})
    return msgs


def test_cap_history_bounds_message_count_and_chars():
    hist = _oversized_history()
    assert len(hist) > _SESSION_MAX_MESSAGES  # fixture actually overflows
    capped = _cap_history(hist)
    assert len(capped) <= _SESSION_MAX_MESSAGES + 1  # +1 for the elision notice
    total = sum(len(str(m.get("content", ""))) for m in capped)
    assert total <= _SESSION_MAX_CHARS + 500  # + notice overhead
    # must-haves: the first task, a user anchor, and the last turn.
    assert any(m.get("content") == TASK for m in capped)
    assert any(m.get("role") == "user" for m in capped)
    assert capped[-1].get("content") == "final turn"


def test_cap_history_is_noop_when_small():
    hist = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    assert _cap_history(hist) is hist


def test_save_persists_a_capped_history(tmp_path):
    state = SessionState(tmp_path / "session.json")
    state.history = _oversized_history()
    state.save(sync=True)  # synchronous write
    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    hist = saved["history"]
    assert len(hist) <= _SESSION_MAX_MESSAGES + 1, (
        f"persisted history not capped: {len(hist)} messages"
    )
    assert any(m.get("role") == "user" for m in hist)
