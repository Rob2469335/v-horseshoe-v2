"""FAIL_TO_PASS flip detection for the FIX:-commit harvester (qwen_train/mine_fix_commits.py).

Revert-proof: builds a real throwaway git repo with a known fail->pass pair and asserts
the harness (a) identifies the flip and (b) REJECTS a pair that does not flip. Isolation
is inherent — find_flip() materializes commits with `git archive` into temp dirs and never
touches the live working tree.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import mine_fix_commits as mfc  # noqa: E402


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Override conftest's subprocess.Popen mock — this module runs REAL git + pytest."""
    yield


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _init(tmp):
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    _git(tmp, "config", "commit.gpgsign", "false")


def _commit(tmp, msg):
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", msg)
    return _git(tmp, "rev-parse", "HEAD").stdout.strip()


def test_find_flip_detects_fail_to_pass(tmp_path):
    _init(tmp_path)
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(
        "from mod import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    parent = _commit(tmp_path, "FIX: broken add")  # bug present -> test fails at parent
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    fix = _commit(tmp_path, "fix it")

    v = mfc.find_flip(parent, fix, ["test_mod.py"], repo=tmp_path, timeout=90)
    assert v["flip"] is True, v
    assert any("test_add" in t for t in v["fail_to_pass"]), v


def test_find_flip_rejects_non_flip(tmp_path):
    _init(tmp_path)
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(
        "from mod import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    a = _commit(tmp_path, "FIX: already correct")
    (tmp_path / "mod.py").write_text(
        "def add(a, b):\n    # doc-only\n    return a + b\n", encoding="utf-8"
    )
    b = _commit(tmp_path, "doc only")

    v = mfc.find_flip(a, b, ["test_mod.py"], repo=tmp_path, timeout=90)
    assert v["flip"] is False, v
    assert v["fail_to_pass"] == []


def test_extract_is_isolated_copy(tmp_path):
    _init(tmp_path)
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    sha = _commit(tmp_path, "FIX: add hello")
    dest = tmp_path / "out"
    dest.mkdir()
    assert mfc.extract(sha, dest, repo=tmp_path) is True
    assert (dest / "hello.py").read_text(encoding="utf-8") == "x = 1\n"
    # the live tree is untouched (still HEAD, file still present)
    assert (tmp_path / "hello.py").exists()
    assert _git(tmp_path, "rev-parse", "HEAD").stdout.strip() == sha
