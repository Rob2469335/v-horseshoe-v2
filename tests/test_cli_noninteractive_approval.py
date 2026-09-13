"""Unattended CLI runs must fail closed on approvals, never block/prompt."""

from __future__ import annotations

import sys

import organism_console.ui.live_stream as ls


class _NotTTY:
    def isatty(self) -> bool:
        return False


class _TTY:
    def isatty(self) -> bool:
        return True


def test_stdin_is_interactive_false_when_piped(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _NotTTY())
    assert ls._stdin_is_interactive() is False


def test_stdin_is_interactive_true_on_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _TTY())
    assert ls._stdin_is_interactive() is True


def test_stdin_none_is_not_interactive(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    assert ls._stdin_is_interactive() is False
