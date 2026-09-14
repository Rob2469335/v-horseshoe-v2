"""The headless toast off-switch (SWARM_NO_TOASTS=1).

Unattended/ablation runs must never fire a desktop popup. Pins that the env gate
returns BEFORE any thread/spawn is started.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from organism_console import notifications as n  # noqa: E402


def test_swarm_no_toasts_is_noop(monkeypatch):
    monkeypatch.setenv("SWARM_NO_TOASTS", "1")
    started = {"n": 0}

    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started["n"] += 1

    monkeypatch.setattr(n.threading, "Thread", _T)
    n.notify("title", "body")
    assert started["n"] == 0


def test_empty_content_is_noop(monkeypatch):
    monkeypatch.delenv("SWARM_NO_TOASTS", raising=False)
    started = {"n": 0}

    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started["n"] += 1

    monkeypatch.setattr(n.threading, "Thread", _T)
    n.notify("", "")
    assert started["n"] == 0
