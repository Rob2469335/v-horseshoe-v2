"""AGENTS.md writes must be atomic and serialized (data-loss regression).

AGENTS.md was found at 0 bytes mid-session: four writers
(watch_loop/recovery_engine/reflection_loop/tool_executor) all did a full-file
read-modify-write with ``Path.write_text``, which truncates to 0 bytes before it
writes, and only two of the four held a lock. These tests pin the fix: an
interrupted write leaves the original file intact, concurrent writers do not lose
updates, and every writer routes through the atomic helper.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from swarm_os.lib.agents_md import (
    atomic_write_text,
    insert_after_marker,
    update_agents_md,
)

MARKER = "## Self-Healing & Self-Learning Fixes\n"


# --------------------------------------------------------------------------
# The mechanism: plain write_text has a 0-byte window; the helper does not.
# --------------------------------------------------------------------------


def test_plain_open_w_truncates_before_write(tmp_path: Path):
    """Documents the bug the helper exists to fix (the truncation window)."""
    target = tmp_path / "AGENTS.md"
    target.write_text("ORIGINAL", encoding="utf-8")

    handle = open(target, "w", encoding="utf-8")  # noqa: SIM115 - deliberate
    try:
        # Between open() and write() the file is 0 bytes — a crash here is how
        # AGENTS.md got erased.
        assert target.read_text(encoding="utf-8") == ""
    finally:
        handle.close()


def test_interrupted_write_never_truncates(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    original = "ORIGINAL LINE\n" * 200
    target.write_text(original, encoding="utf-8")

    # Simulate a crash/failure between staging the temp file and promoting it.
    with patch("swarm_os.lib.atomic_io.os.replace", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            atomic_write_text(target, "NEW CONTENT")

    assert target.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp.*"))


def test_atomic_write_leaves_no_temp_and_writes_content(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert not list(tmp_path.glob("*.tmp.*"))


# --------------------------------------------------------------------------
# Serialization: concurrent writers must not lose updates.
# --------------------------------------------------------------------------


def test_concurrent_writers_lose_no_updates(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    target.write_text(MARKER + "\n", encoding="utf-8")

    def _add(i: int) -> None:
        update_agents_md(
            lambda c: insert_after_marker(c, MARKER, f"line-{i}\n"), path=target
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(_add, range(40)))

    text = target.read_text(encoding="utf-8")
    assert all(f"line-{i}\n" in text for i in range(40))
    assert not list(tmp_path.glob("*.tmp.*"))


# --------------------------------------------------------------------------
# update_agents_md contract: no write when nothing changed.
# --------------------------------------------------------------------------


def test_no_write_when_transform_returns_none(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    target.write_text("ORIGINAL", encoding="utf-8")
    assert update_agents_md(lambda _c: None, path=target) is False
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_no_write_when_marker_absent(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    target.write_text("no marker here", encoding="utf-8")
    assert update_agents_md(
        lambda c: insert_after_marker(c, MARKER, "x\n"), path=target
    ) is False
    assert target.read_text(encoding="utf-8") == "no marker here"


def test_missing_file_is_false_not_error(tmp_path: Path):
    assert update_agents_md(lambda c: "x", path=tmp_path / "nope.md") is False


# --------------------------------------------------------------------------
# Integration: the real writers go through the atomic path.
# --------------------------------------------------------------------------


def _seed(tmp_path: Path) -> Path:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# AGENTS\n\n" + MARKER + "\n", encoding="utf-8")
    return agents


def test_reflection_rule_writer_appends(tmp_path: Path, monkeypatch):
    from swarm_os.services import reflection_loop as rl

    agents = _seed(tmp_path)
    monkeypatch.setattr(rl, "ROOT_DIR", tmp_path)

    rl._record_rule_to_agents_md("coder", "Always list the parent dir first", 0.9)

    text = agents.read_text(encoding="utf-8")
    assert "- **Rule (coder)**: Always list the parent dir first" in text
    assert not list(tmp_path.glob("*.tmp.*"))


def test_reflection_rule_writer_dedupes(tmp_path: Path, monkeypatch):
    from swarm_os.services import reflection_loop as rl

    agents = _seed(tmp_path)
    monkeypatch.setattr(rl, "ROOT_DIR", tmp_path)

    rl._record_rule_to_agents_md("coder", "Always list the parent dir first", 0.9)
    rl._record_rule_to_agents_md("coder", "Always list the parent dir first", 0.9)

    text = agents.read_text(encoding="utf-8")
    assert text.count("- **Rule (coder)**: Always list the parent dir first") == 1


def test_recovery_autoheal_writer_appends(tmp_path: Path, monkeypatch):
    from swarm_os.healing import recovery_engine as re

    agents = _seed(tmp_path)
    monkeypatch.setattr(re, "PROJECT_ROOT", tmp_path)

    re._record_to_agents_md("disk_cache_pressure", "# clean up\nclean_directory(x)")

    text = agents.read_text(encoding="utf-8")
    assert "Auto-Heal" in text
    assert "disk_cache_pressure" in text
    assert not list(tmp_path.glob("*.tmp.*"))


def test_watch_loop_audit_write_appends(tmp_path: Path, monkeypatch):
    from swarm_os.services import watch_loop as wl

    agents = _seed(tmp_path)
    audit = tmp_path / "auto_repairs.jsonl"
    monkeypatch.setattr(wl, "_AGENTS_MD", agents)
    monkeypatch.setattr(wl, "_AUDIT_FILE", audit)

    wl._audit_write({"trigger": "test"}, "- **[AUTO-REPAIR]** test line")

    assert "test line" in agents.read_text(encoding="utf-8")
    assert audit.read_text(encoding="utf-8").strip()
    assert not list(tmp_path.glob("*.tmp.*"))


@pytest.mark.parametrize(
    "entrypoint",
    ["reflection", "recovery", "watch_loop"],
)
def test_writers_route_through_atomic_helper(tmp_path: Path, monkeypatch, entrypoint):
    """A future regression back to a direct write_text would bypass the helper —
    sabotage the helper and assert the file is left untouched."""
    agents = _seed(tmp_path)
    before = agents.read_text(encoding="utf-8")

    with patch(
        "swarm_os.lib.agents_md.atomic_write_text",
        side_effect=RuntimeError("sabotaged helper"),
    ):
        if entrypoint == "reflection":
            from swarm_os.services import reflection_loop as rl

            monkeypatch.setattr(rl, "ROOT_DIR", tmp_path)
            rl._record_rule_to_agents_md("coder", "some new rule text", 0.9)
        elif entrypoint == "recovery":
            from swarm_os.healing import recovery_engine as re

            monkeypatch.setattr(re, "PROJECT_ROOT", tmp_path)
            re._record_to_agents_md("some_anomaly", "# do a thing\nrecover()")
        else:
            from swarm_os.services import watch_loop as wl

            monkeypatch.setattr(wl, "_AGENTS_MD", agents)
            monkeypatch.setattr(wl, "_AUDIT_FILE", tmp_path / "audit.jsonl")
            wl._audit_write({"trigger": "test"}, "- **[AUTO-REPAIR]** x")

    assert agents.read_text(encoding="utf-8") == before
