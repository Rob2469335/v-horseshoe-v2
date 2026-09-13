"""Success pathway into ReflexionMemory (learn from proven fixes, not only failures).

Analogue of AgentHER / Hindsight Supervised Learning (arXiv:2603.21357, 2607.04235)
at the runtime-memory layer.
"""

from __future__ import annotations

import asyncio

from swarm_os.services import reflection_loop as rl


def test_point_id_namespaces_success_vs_failure():
    f = rl._reflexion_point_id("coder", "loop", "failure")
    s = rl._reflexion_point_id("coder", "loop", "success")
    assert f != s  # a success lesson never collides with a failure rule
    assert rl._reflexion_point_id("coder", "loop", "failure") == f  # deterministic
    assert rl._reflexion_point_id("coder", "loop") == f  # default kind is failure


def test_store_success_lesson_uses_success_kind(monkeypatch):
    calls: dict = {}

    class _FakeSvc:
        async def store_reflexion(self, **kw):
            calls.update(kw)

    monkeypatch.setattr(rl, "get_reflection_service", lambda: _FakeSvc())
    asyncio.run(
        rl.store_success_lesson(
            "coder",
            "looped on sandbox_repl for a non-compute task",
            "over-broad prompt-level tool nudge",
            "avoid blanket tool nudges; scope instructions to compute-shaped tasks",
        )
    )
    assert calls["kind"] == "success"
    assert calls["component"] == "coder"
    assert "blanket tool nudges" in calls["correction"]


def test_store_success_lesson_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("no service")

    monkeypatch.setattr(rl, "get_reflection_service", _boom)
    asyncio.run(rl.store_success_lesson("c", "s", "r", "rule"))  # must not raise
