"""Tests for the diversity-aware curriculum selector (qwen_train/diverse_select.py).

Pins: per-family capping, rare families always kept, prompt dedupe, harder-first
within family, and round-robin interleave (so runs alternate families).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import diverse_select as ds  # noqa: E402


def _item(i, tools, difficulty=1):
    return {
        "id": f"i{i}",
        "split": "train",
        "target_tools": list(tools),
        "prompt": f"task {i} {'|'.join(tools)}",
        "difficulty": difficulty,
    }


def test_family_key_is_sorted_target_tools():
    assert ds.family(_item(0, ["filesystem", "lsp"])) == ("filesystem", "lsp")
    assert ds.family(_item(1, ["lsp", "filesystem"])) == ("filesystem", "lsp")
    assert ds.family({"target_tools": []}) == ("?",)


def test_select_caps_per_family_and_keeps_rare():
    items = [_item(i, ["sandbox_repl"]) for i in range(10)]
    items += [_item(100 + i, ["filesystem"]) for i in range(10)]
    items += [_item(200, ["system"])]  # rare family
    chosen = ds.select_diverse(items, per_family=3, prefer_hard=False)
    fams = [ds.family(c) for c in chosen]
    assert fams.count(("sandbox_repl",)) == 3
    assert fams.count(("filesystem",)) == 3
    assert ("system",) in fams  # rare family kept


def test_select_dedupes_prompts():
    dup = _item(0, ["filesystem"])
    dup2 = dict(dup, id="dup")
    chosen = ds.select_diverse([dup, dup2], per_family=5)
    assert len(chosen) == 1


def test_select_prefers_harder_within_family():
    items = [
        _item(0, ["filesystem"], difficulty=1),
        _item(1, ["filesystem"], difficulty=3),
    ]
    chosen = ds.select_diverse(items, per_family=1, prefer_hard=True)
    assert chosen[0]["difficulty"] == 3


def test_select_interleaves_families():
    items = [_item(i, ["sandbox_repl"]) for i in range(3)]
    items += [_item(10 + i, ["git"]) for i in range(3)]
    chosen = ds.select_diverse(items, per_family=3, prefer_hard=False)
    fams = [ds.family(c) for c in chosen]
    # adjacent items must not all be the same family
    assert fams[0] != fams[1]
    assert fams[1] != fams[2]


def test_select_excludes_eval_split():
    items = [_item(0, ["filesystem"])] + [dict(_item(1, ["filesystem"]), split="eval")]
    chosen = ds.select_diverse(items, per_family=5)
    assert all(c["split"] == "train" for c in chosen)
