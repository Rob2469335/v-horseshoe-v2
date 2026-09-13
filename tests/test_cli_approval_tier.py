"""Fail-closed default for the CLI approval tier.

A stream chunk with no ``authorization`` must be treated as the strictest tier
(ALWAYS_CONFIRM) so CLI auto-mode can never auto-approve an unlabelled approval
(safe-defaults: fail closed when the policy decision is missing).
"""

from __future__ import annotations

import inspect

import organism_console.ui.live_stream as ls
from organism_console.ui.live_stream import _approval_tier


def test_missing_tier_is_fail_closed():
    assert _approval_tier({}) == "ALWAYS_CONFIRM"


def test_empty_or_blank_tier_is_fail_closed():
    assert _approval_tier({"authorization": ""}) == "ALWAYS_CONFIRM"
    assert _approval_tier({"authorization": "   "}) == "ALWAYS_CONFIRM"
    assert _approval_tier({"authorization": None}) == "ALWAYS_CONFIRM"


def test_present_tier_is_preserved():
    assert _approval_tier({"authorization": "CONFIRM"}) == "CONFIRM"
    assert _approval_tier({"authorization": "DENY"}) == "DENY"
    assert _approval_tier({"authorization": "ALWAYS_CONFIRM"}) == "ALWAYS_CONFIRM"


def test_approval_branch_uses_the_fail_closed_tier():
    src = inspect.getsource(ls._stream_prompt_async)
    assert "auth_tier = _approval_tier(chunk)" in src
