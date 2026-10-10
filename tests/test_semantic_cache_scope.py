"""Semantic decision cache must be SCOPED by task/workspace.

Regression for the 2026-09-16 contamination bug: exact query from an old
`twine` repair task reappeared in a completely unrelated task.
"""

from __future__ import annotations
from runtime_v2.services import _semantic_decision_cache as sc

_PROMPT = "some prompt"

def test_same_prompt_different_task_different_key(monkeypatch):
    turn0 = [{"role": "user", "content": _PROMPT}]
    
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "/task/1")
    key1 = sc.get_cache_key(turn0, "coder")
    
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "/task/2")
    key2 = sc.get_cache_key(turn0, "coder")
    
    assert key1 != key2, "different workspace roots must produce different cache keys"

def test_different_cwd_different_key(monkeypatch, tmp_path):
    turn0 = [{"role": "user", "content": _PROMPT}]
    monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)

    # Use real, platform-neutral temp directories: the old hardcoded
    # ``monkeypatch.chdir("C:\\\\")`` only exists on Windows and raised
    # FileNotFoundError on the Linux CI runner.
    cwd_a = tmp_path / "cwd_a"
    cwd_b = tmp_path / "cwd_b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    key1 = sc.get_cache_key(turn0, "coder")

    monkeypatch.chdir(cwd_b)
    key2 = sc.get_cache_key(turn0, "coder")

    assert key1 != key2, "different cwds must produce different cache keys"

