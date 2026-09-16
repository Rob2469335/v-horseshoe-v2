"""`filesystem` patch accepts the argument names agents actually send.

Measured 2026-09-15: a real SWE run patched `pyfakefs/fake_os.py` with
`find=…` and got "Surgical Error: 'old' string cannot be empty." — the FILE WAS
LEFT UNTOUCHED, the run scored 0 edits, and the model looked incapable. `find`
is also the GREP alias, so the arg name alone cannot tell the tool what was
meant; the same error peppers the auto-repair log, so this blocked edits
globally, not just under SWE.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swarm_os.lib.mcp.filesystem import filesystem_handler

BASE = "def walk(self):\n    return 1\n"


@pytest.fixture(autouse=True)
def _no_write_root(monkeypatch):
    """`.env` sets SWARM_WRITE_ROOT (the fix-curriculum confinement), which would
    refuse every tmp_path patch before the arg logic is reached."""
    monkeypatch.delenv("SWARM_WRITE_ROOT", raising=False)


@pytest.mark.parametrize(
    "kwargs",
    [
        # what the agent actually sent
        {"find": "    return 1", "content": "    return 2"},
        # canonical
        {"old": "    return 1", "new": "    return 2"},
        {"search": "    return 1", "replace": "    return 2"},
        {"old_str": "    return 1", "new_str": "    return 2"},
        {"old_string": "    return 1", "new_string": "    return 2"},
    ],
)
def test_patch_accepts_agent_arg_aliases(tmp_path, kwargs):
    f = tmp_path / "m.py"
    f.write_text(BASE, encoding="utf-8")
    r = filesystem_handler({"operation": "patch", "path": str(f), **kwargs}, tmp_path)
    assert r.get("ok") is True, r
    assert f.read_text(encoding="utf-8") != BASE  # the edit actually landed


def test_patch_without_old_gives_an_actionable_error(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(BASE, encoding="utf-8")
    r = filesystem_handler({"operation": "patch", "path": str(f), "new": "x"}, tmp_path)
    assert r.get("ok") is False
    # The old message ("'old' string cannot be empty") never said what to send.
    assert "old" in r["error"].lower()
    assert f.read_text(encoding="utf-8") == BASE  # nothing written


def test_patch_end_to_end_edits_the_real_file(tmp_path):
    """The exact shape pyfakefs' agent used, against a real file on disk."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "fake_os.py"
    target.write_text(
        "class FakeOs:\n    def walk(self, top):\n        return None\n",
        encoding="utf-8",
    )
    r = filesystem_handler(
        {
            "operation": "patch",
            "path": str(target),
            "find": "    def walk(self, top):\n        return None",
            "content": "    def walk(self, top, topdown=True):\n        return []",
        },
        tmp_path,
    )
    assert r.get("ok") is True, r
    assert "topdown=True" in target.read_text(encoding="utf-8")


DIFF = (
    "diff --git a/src/core.py b/src/core.py\n"
    "--- a/src/core.py\n"
    "+++ b/src/core.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def f():\n"
    "-    return 1\n"
    "+    return 2\n"
)


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    target = repo / "src" / "core.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True)
    return repo, target


def test_patch_accepts_a_unified_diff(tmp_path):
    """The arg shape the CLICK run sent (three times) and that silently no-opped.

    Revert-proof: pre-fix `old` was empty, so the call returned
    "'old' string cannot be empty" and the file was never written.
    """
    repo, target = _git_repo(tmp_path)
    r = filesystem_handler(
        {"operation": "patch", "path": str(target), "patch": DIFF}, tmp_path
    )
    assert r.get("ok") is True, r
    assert "return 2" in target.read_text(encoding="utf-8")


def test_patch_bad_diff_fails_loudly(tmp_path):
    repo, target = _git_repo(tmp_path)
    r = filesystem_handler(
        {
            "operation": "patch",
            "path": str(target),
            "patch": "@@ -9,1 +9,1 @@\n-nope\n+yep\n",
        },
        tmp_path,
    )
    assert r.get("ok") is False
    # The failure must NAME the cause, not silently no-op.
    assert "git apply failed" in r["error"]
