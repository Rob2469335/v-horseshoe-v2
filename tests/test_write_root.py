"""SWARM_WRITE_ROOT — path-scoped filesystem write confinement (docs/WRITE_FIX_TASKS.md).

The scoped filesystem grant (offline-grantable) may relax write; this env is what
keeps it confined to the task sandbox. Default unset = today's behaviour.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from swarm_os.lib.mcp.filesystem import filesystem_handler  # noqa: E402


def test_write_root_blocks_outside(tmp_path, monkeypatch):
    (tmp_path / "allowed").mkdir()
    monkeypatch.setenv("SWARM_WRITE_ROOT", "allowed")
    inside = filesystem_handler(
        {"operation": "write", "path": "allowed/x.txt", "content": "hi"}, tmp_path
    )
    assert inside.get("ok"), inside
    outside = filesystem_handler(
        {"operation": "write", "path": "outside.txt", "content": "hi"}, tmp_path
    )
    assert not outside.get("ok"), outside
    assert "SWARM_WRITE_ROOT" in outside.get("error", "")


def test_write_root_unset_allows_anywhere(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_WRITE_ROOT", raising=False)
    r = filesystem_handler(
        {"operation": "write", "path": "anywhere.txt", "content": "hi"}, tmp_path
    )
    assert r.get("ok"), r


def test_patch_root_blocks_outside(tmp_path, monkeypatch):
    (tmp_path / "allowed").mkdir()
    (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
    monkeypatch.setenv("SWARM_WRITE_ROOT", "allowed")
    blocked = filesystem_handler(
        {"operation": "patch", "path": "m.py", "old": "a = 1", "new": "a = 2"}, tmp_path
    )
    assert not blocked.get("ok"), blocked
    assert "SWARM_WRITE_ROOT" in blocked.get("error", "")
    # unchanged
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 1\n"
