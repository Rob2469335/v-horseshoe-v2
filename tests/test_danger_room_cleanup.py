"""DangerRoom disk-safety: heavy-dir exclusion + stale-sandbox sweep.

Regression guard for the 2026-09-14 disk-fill: every DangerRoom copy dragged in
data/run_snapshots (17 GB) + storage/collections (5.3 GB) + qwen_train (3 GB), and a
killed run's `rmtree(ignore_errors=True)` left the whole copy orphaned. Both are fixed
here — an orphaned sandbox is now small AND swept.
"""

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from swarm_os.services.danger_room import DangerRoom  # noqa: E402


def _seed_repo(tmp_path: Path) -> None:
    (tmp_path / "swarm_os").mkdir()
    (tmp_path / "swarm_os" / "code.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "run_snapshots").mkdir()
    (tmp_path / "data" / "run_snapshots" / "s.bin").write_bytes(b"0" * 500)
    for heavy in ("storage", "qwen_train", "logs"):
        (tmp_path / heavy).mkdir()
        (tmp_path / heavy / "big.bin").write_bytes(b"0" * 500)


@pytest.mark.asyncio
async def test_setup_excludes_heavy_dirs(tmp_path):
    _seed_repo(tmp_path)
    dr = DangerRoom(tmp_path)
    await dr.setup()
    try:
        # code is copied...
        assert (dr.sandbox_dir / "swarm_os" / "code.py").exists()
        # ...but the multi-GB runtime/dataset dirs are NOT
        assert not (dr.sandbox_dir / "data" / "run_snapshots").exists()
        assert not (dr.sandbox_dir / "storage").exists()
        assert not (dr.sandbox_dir / "qwen_train").exists()
    finally:
        await dr.teardown()


def test_sweep_stale_sandboxes_removes_old_keeps_recent(tmp_path):
    old = tmp_path / ".sandbox_old"
    old.mkdir()
    (old / "f").write_text("x", encoding="utf-8")
    past = time.time() - 7200
    os.utime(old, (past, past))
    recent = tmp_path / ".sandbox_recent"
    recent.mkdir()

    removed = DangerRoom(tmp_path)._sweep_stale_sandboxes(max_age_s=1800)
    assert removed == 1
    assert not old.exists()
    assert recent.exists()


@pytest.mark.asyncio
async def test_setup_sweeps_orphans(tmp_path):
    _seed_repo(tmp_path)
    orphan = tmp_path / ".sandbox_orphan"
    orphan.mkdir()
    (orphan / "junk.bin").write_bytes(b"0" * 500)
    past = time.time() - 7200
    os.utime(orphan, (past, past))

    dr = DangerRoom(tmp_path)
    await dr.setup()
    try:
        assert not orphan.exists()  # swept during setup
    finally:
        await dr.teardown()
