"""Tests for the write/fix task family (qwen_train/fix_tasks.py).

Pins the safety-critical verifier: a task is BROKEN by construction, verifying
False until the module is patched, verifying True after the fix, and REJECTED if
the check file is modified (anti-cheat). Uses a temp repo root.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import fix_tasks as ft  # noqa: E402


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock — this module
    runs REAL `python check.py` subprocesses, so Popen must not be mocked."""
    yield


def _root(tmp):
    return tmp / "data" / "curriculum_fix"


def test_broken_task_fails_verification(tmp_path):
    item = ft.make_task("wrong_op", 0, _root(tmp_path), tmp_path)
    assert ft.verify_fix(item)["passed"] is False


def test_task_passes_after_patch(tmp_path):
    item = ft.make_task("wrong_op", 0, _root(tmp_path), tmp_path)
    (tmp_path / item["verify"]["module"]).write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    assert ft.verify_fix(item)["passed"] is True


def test_modified_check_is_rejected(tmp_path):
    item = ft.make_task("wrong_op", 0, _root(tmp_path), tmp_path)
    chk = tmp_path / item["verify"]["check"]
    chk.write_text(chk.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    res = ft.verify_fix(item)
    assert res["passed"] is False
    assert "modified" in res["reason"]


def test_reference_solution_actually_fixes_every_kind(tmp_path):
    fixes = {
        "off_by_one": "def total(n):\n    return sum(range(1, n + 1))\n",
        "wrong_op": "def add(a, b):\n    return a + b\n",
        "missing_return": "def double(x):\n    return x * 2\n",
        "wrong_default": "def scale(x, factor=3):\n    return x * factor\n",
        "string_case": "def shout(s):\n    return s.upper()\n",
    }
    for kind, fixed in fixes.items():
        item = ft.make_task(kind, 0, _root(tmp_path), tmp_path)
        (tmp_path / item["verify"]["module"]).write_text(fixed, encoding="utf-8")
        assert ft.verify_fix(item)["passed"] is True, kind


def test_generate_shape(tmp_path):
    items = ft.generate(2, _root(tmp_path), tmp_path)
    assert len(items) == len(ft._tasks()) * 2
    for it in items:
        assert it["verify"]["type"] == "fix_file"
        assert it["target_tools"] == ["filesystem", "sandbox_repl"]
        assert it["prompt"]


def test_missing_files_fail(tmp_path):
    item = ft.make_task("wrong_op", 0, _root(tmp_path), tmp_path)
    (tmp_path / item["verify"]["module"]).unlink()
    assert ft.verify_fix(item)["passed"] is False
