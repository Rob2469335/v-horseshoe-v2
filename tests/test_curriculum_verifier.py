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


def test_numeric_verify_is_boundary_checked():
    """A numeric answer must match standalone, so `12` can't match inside `3912`."""
    item = {"verify": {"type": "contains", "mode": "all", "value": ["12"]}}
    assert rc.verify(item, "The value is 12 exactly")["passed"] is True
    assert rc.verify(item, "The value is 3912")["passed"] is False
    assert rc.verify(item, "The value is 120")["passed"] is False


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


def test_parse_tools_succeeded_only_counts_success_marker():
    out = "  \u26a1 sandbox_repl\n  \u2713 sandbox_repl {...}\n  \u26a1 email\n"
    assert rc.parse_tools_succeeded(out) == ["sandbox_repl"]


def test_parse_tools_denied():
    out = (
        "non-interactive: denied lsp (symbols need approval)\n"
        "  auto-denied: sandbox_repl (execute)"
    )
    assert rc.parse_tools_denied(out) == ["lsp", "sandbox_repl"]


def test_grantable_set_is_task_scoped():
    # exactly the offline-run tools, never blanket: sandbox_repl (ALWAYS_CONFIRM)
    # + lsp (CONFIRM), granted scoped + revoked after.
    assert set(rc._GRANTABLE) == {"sandbox_repl", "lsp"}
    assert "sandbox_repl" not in rc._APPROVAL_FREE
    assert "lsp" not in rc._APPROVAL_FREE


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
    return None  # a lookup-family item (file-grounded), not a math item


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
    math_items = 0
    for it in items:
        expected = _recompute(it["prompt"])
        if expected is None:
            continue  # lookup family, checked separately
        math_items += 1
        assert it["verify"]["value"] == [expected]  # generated answer is correct
        assert rc.verify(it, f"the answer is {expected}")["passed"] is True
        assert rc.verify(it, "the answer is 999999")["passed"] is False
    assert math_items > 0  # the pool actually contains math variants


def test_generated_lookup_variants_match_the_file(tmp_path, monkeypatch):
    """Lookup-family answers are the REAL value in the referenced file."""
    monkeypatch.setattr(rc, "GENERATED", tmp_path / "generated.jsonl")
    rc.generate(40, seed=11)
    items = [
        json.loads(ln)
        for ln in (tmp_path / "generated.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if ln
    ]
    checked = 0
    for it in items:
        if "filesystem" not in it["target_tools"]:
            continue
        m = re.search(
            r"(runtime_v2/[\w/\.]+\.py|organism_console/[\w/\.]+\.py|pyproject\.toml)",
            it["prompt"],
        )
        assert m, it["prompt"]
        val = it["verify"]["value"][0]
        text = (rc._HERE.parent / m.group(1)).read_text(encoding="utf-8")
        assert val.replace("_", "") in text.replace("_", ""), (it["prompt"], val)
        checked += 1
    assert checked > 0  # the pool actually contains lookup variants


def test_next_item_filters_approval_requiring_items(monkeypatch):
    """Unattended runs must never select an ALWAYS_CONFIRM tool (sandbox_repl),
    which would prompt and hang. `--allow-approval` lifts the filter."""
    free = {
        "id": "z1",
        "prompt": "Read a file.",
        "target_tools": ["filesystem"],
        "verify": {"type": "contains", "mode": "all", "value": ["x"]},
    }
    needs = {
        "id": "z2",
        "prompt": "compute 2 + 2",
        "target_tools": ["sandbox_repl"],
        "verify": {"type": "contains", "mode": "all", "value": ["4"]},
    }
    monkeypatch.setattr(rc, "load_items", lambda: [needs, free])
    monkeypatch.setattr(rc, "_completed", lambda: {})
    assert rc.next_item(approval_free=True)["id"] == "z1"
    assert rc.next_item(approval_free=False)["id"] in ("z1", "z2")
    assert "sandbox_repl" not in rc._APPROVAL_FREE


def test_load_items_merges_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "GENERATED", tmp_path / "generated.jsonl")
    seed = len(rc.load_items())
    rc.generate(3, seed=1)
    assert len(rc.load_items()) == seed + 3


def test_mine_pool_is_large_and_diverse():
    pool = rc.mine_pool()
    assert len(pool) > 500
    tools = {t for i in pool for t in i["target_tools"]}
    assert len(tools) >= 2  # at least filesystem + sandbox_repl
    # every pooled item is verifiable (has a verify spec + expected value)
    for it in pool[:200]:
        assert it["verify"]["type"] in ("contains", "regex")


def test_const_symbol_and_defcount_miners_on_a_tmp_repo(tmp_path):
    mod = tmp_path / "runtime_v2" / "api"
    mod.mkdir(parents=True)
    (mod / "sample.py").write_text(
        "MAX_WIDGETS = 742\n"
        "LABEL = 'widget-factory'\n"
        "\n"
        "def alpha():\n"
        "    return 1\n"
        "\n"
        "class Beta:\n"
        "    pass\n"
        "\n"
        "def gamma():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    consts = rc._const_items(tmp_path)
    vals = {v for it in consts for v in it["verify"]["value"]}
    assert "742" in vals and "widget-factory" in vals
    counts = rc._defcount_items(tmp_path)
    assert [it["verify"]["value"][0] for it in counts] == ["3"]  # alpha, Beta, gamma
    syms = rc._symbol_items(tmp_path)
    prompts = " ".join(it["prompt"] for it in syms)
    assert "alpha" in prompts and "Beta" in prompts
