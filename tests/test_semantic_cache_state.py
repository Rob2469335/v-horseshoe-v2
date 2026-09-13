"""Semantic decision cache must be STATE-scoped.

Regression for the 2026-09-13 loop: `get_cache_key` hashed only the last user
message, so the same prompt in a different execution state reused a stale tool
decision -> repeated call -> loop diagnosis. Same words != same situation.
"""

from __future__ import annotations

from runtime_v2.services import _semantic_decision_cache as sc

_PROMPT = "Read runtime_v2/api/_agent_config.py and report the value of MAX_DEPTH."


def test_same_prompt_different_state_different_key():
    turn0 = [{"role": "user", "content": _PROMPT}]
    turn1 = [
        {"role": "user", "content": _PROMPT},
        {"role": "assistant", "content": '{"action":"filesystem","operation":"read"}'},
        {"role": "tool", "content": "MAX_DEPTH = 15"},
        {"role": "user", "content": _PROMPT},
    ]
    assert sc.get_cache_key(turn0, "coder") != sc.get_cache_key(turn1, "coder")


def test_same_prompt_same_state_is_stable():
    msgs = [{"role": "user", "content": _PROMPT}]
    assert sc.get_cache_key(msgs, "coder") == sc.get_cache_key(msgs, "coder")


def test_state_scoped_exact_cache_does_not_cross_states():
    state_a = [{"role": "user", "content": _PROMPT}]
    state_b = [
        {"role": "assistant", "content": "did something"},
        {"role": "tool", "content": "grep returned: ..."},
        {"role": "user", "content": _PROMPT},
    ]
    ka = sc.get_cache_key(state_a, "coder")
    kb = sc.get_cache_key(state_b, "coder")
    assert ka != kb, "different execution state must not share a cache key"
    sc._decision_cache.clear()
    sc._put_exact(ka, {"action": "filesystem", "operation": "read", "path": "x"})
    assert sc._get_exact(ka) is not None
    assert sc._get_exact(kb) is None, "a stale decision must not replay in a new state"
    sc._decision_cache.clear()
