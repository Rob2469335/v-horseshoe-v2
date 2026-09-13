"""Verified tool-use curriculum: pure verifier + CLI-result extraction + shape."""

from __future__ import annotations

import importlib.util
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
    assert (
        rc.verify({"verify": {"type": "regex", "value": r"3\.1[0-9]"}}, "Python 3.14")[
            "passed"
        ]
        is True
    )


def test_verify_manual_is_neither_pass_nor_fail():
    assert rc.verify({"verify": {"type": "manual"}}, "anything")["passed"] is None


def test_extract_result_gets_last_json_object():
    out = 'panel text\n{\n  "ok": true,\n  "content": "hi"\n}'
    assert rc.extract_result(out) == {"ok": True, "content": "hi"}


def test_extract_result_none_when_absent():
    assert rc.extract_result("no json here") is None


def test_curriculum_shape():
    items = rc.load_items()
    assert len(items) == 30
    assert len({i["id"] for i in items}) == 30
    assert sum(1 for i in items if i["split"] == "eval") == 10
    assert sum(1 for i in items if i["split"] == "train") == 20
    for i in items:
        assert i["prompt"].strip()
        assert i.get("verify", {}).get("type")
        assert isinstance(i.get("target_tools"), list) and i["target_tools"]
