"""Verified tool-use curriculum: verifier, tool parsing, generator, shape."""

from __future__ import annotations

import importlib.util
import json
import math
import re
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "qwen_train" / "run_curriculum.py"
_spec = importlib.util.spec_from_file_location("run_curriculum", _MOD)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


def test_verify_contains_all():
    item = {
        "verify": {"type": "contains", "mode": "all", "value": ["12", "code_analyzer"]}
    }
    out = rc.verify(item, "MAX_TURNS = 12 and ANALYSIS_AGENTS includes code_analyzer")
    assert out["passed"] is True


def test_verify_contains_all_fails_when_missing():
    item = {"verify": {"type": "contains", "mode": "all", "value": ["12", "nope"]}}
    assert rc.verify(item, "MAX_TURNS = 12")["passed"] is False


def test_verify_contains_any():
    item = {"verify": {"type": "contains", "mode": "any", "value": ["windows", "win"]}}
    assert rc.verify(item, "platform: Windows")["passed"] is True
    assert rc.verify(item, "platform: linux")["passed"] is False


def test_verify_regex():
    item = {"verify": {"type": "regex", "value": r"3\.1[0-9]"}}
    assert rc.verify(item, "Python 3.14")["passed"] is True


def test_verify_manual_is_neither_pass_nor_fail():
    assert rc.verify({"verify": {"type": "manual"}}, "anything")["passed"] is None


def test_extract_result_gets_last_json_object():
    out = 'panel text\n{\n  "ok": true,\n  "content": "hi"\n}'
    assert rc.extract_result(out) == {"ok": True, "content": "hi"}


def test_extract_result_none_when_absent():
    assert rc.extract_result("no json here") is None


def test_parse_tools_used_from_stream_markers():
    out = "  \u26a1 filesystem\n  \u2713 filesystem {...}\n  \u26a1 sandbox_repl\n"
    assert rc.parse_tools_used(out) == ["filesystem", "sandbox_repl"]


def test_tool_match_semantics():
    item = {"target_tools": ["sandbox_repl"]}
    assert rc._tool_match(item, ["filesystem", "sandbox_repl"]) == (True, True)
    assert rc._tool_match(item, ["filesystem"]) == (False, False)
    many = {"target_tools": ["a", "b"]}
    assert rc._tool_match(many, ["a"]) == (True, False)


def test_seed_curriculum_shape():
    """The committed seed file is 30 items, 20 train / 10 held-out eval."""
    lines = [ln for ln in rc.CURRICULUM.read_text(encoding="utf-8").splitlines() if ln]
    items = [json.loads(ln) for ln in lines]
    assert len(items) == 30
    assert len({i["id"] for i in items}) == 30
    assert sum(1 for i in items if i["split"] == "eval") == 10
    for i in items:
        assert i["prompt"].strip()
        assert i.get("verify", {}).get("type")
        assert isinstance(i.get("target_tools"), list) and i["target_tools"]


def test_math_helpers():
    assert rc._primes_below(50) == 15
    assert rc._fib(10) == 55  # F(1)=1, F(2)=1


def _recompute(prompt: str) -> str:
    m = re.search(r"compute (\d+) \* (\d+)", prompt)
    if m:
        return str(int(m.group(1)) * int(m.group(2)))
    m = re.search(r"sum of all integers from 1 to (\d+)", prompt)
    if m:
        n = int(m.group(1))
        return str(n * (n + 1) // 2)
    m = re.search(r"prime numbers? are below (\d+)", prompt)
    if m:
        return str(rc._primes_below(int(m.group(1))))
    m = re.search(r"the (\d+)th Fibonacci", prompt)
    if m:
        return str(rc._fib(int(m.group(1))))
    m = re.search(r"greatest common divisor of (\d+) and (\d+)", prompt)
    if m:
        return str(math.gcd(int(m.group(1)), int(m.group(2))))
    raise AssertionError(f"unrecognized generated prompt: {prompt}")


def test_generated_variants_are_correctly_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "GENERATED", tmp_path / "generated.jsonl")
    n = rc.generate(25, seed=7)
    assert n == 25
    items = [
        json.loads(ln)
        for ln in (tmp_path / "generated.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if ln
    ]
    assert len(items) == 25
    for it in items:
        expected = _recompute(it["prompt"])
        assert it["verify"]["value"] == [expected]  # generated answer is correct
        assert rc.verify(it, f"the answer is {expected}")["passed"] is True
        assert rc.verify(it, "the answer is 999999")["passed"] is False


def test_load_items_merges_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "GENERATED", tmp_path / "generated.jsonl")
    seed = len(rc.load_items())
    rc.generate(3, seed=1)
    assert len(rc.load_items()) == seed + 3
