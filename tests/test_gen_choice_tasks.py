"""Tests for the multi-tool-choice generators (qwen_train/gen_choice_tasks.py).

Pins: items are multi-tool (real choice), verifiers are exact `contains`, ground
truth for token counts is whole-word (not substring), and generation is grounded
in the repo it scans.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import gen_choice_tasks as gc  # noqa: E402


def _repo(tmp_path):
    d = tmp_path / "runtime_v2"
    d.mkdir(parents=True)
    (d / "a.py").write_text(
        "def alpha_one():\n    return 1\n\n\ndef beta_two():\n    return alpha_one()\n",
        encoding="utf-8",
    )
    (d / "b.py").write_text(
        "from a import alpha_one\n\n\ndef gamma_three():\n    return alpha_one()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_token_count_is_whole_word():
    texts = {"a": "alpha_one alpha_one alphabet alpha_one_ish"}
    assert gc._token_count(texts, "alpha_one") == 2


def test_item_verifier_value_stringified_and_numbered():
    it = gc._item(0, ["filesystem"], 1, "q", 42)
    assert it["id"] == "x00000"
    assert it["verify"] == {"type": "contains", "mode": "any", "value": ["42"]}


def test_generate_grounded_items_are_multi_tool(tmp_path):
    items = gc.generate(_repo(tmp_path), n_per_family=3)
    assert items, "expected generated items"
    for it in items:
        assert it["id"].startswith("x")
        assert it["split"] == "train"
        assert it["verify"]["type"] == "contains"
        assert it["prompt"]
        assert len(it["target_tools"]) >= 2  # a real tool CHOICE


def test_generated_token_count_matches_ground_truth(tmp_path):
    root = _repo(tmp_path)
    items = gc.generate(root, n_per_family=3)
    texts = gc._scan(root)["texts"]
    tok = [it for it in items if "whole word" in it["prompt"]]
    assert tok, "expected a token-count item"
    for it in tok:
        name = it["prompt"].split("`")[1]
        expected = it["verify"]["value"][0]
        assert expected == str(gc._token_count(texts, name))
