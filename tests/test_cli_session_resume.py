"""CLI session durability (class 4).

The resume pointer (`resume_checkpoint_id`) must survive a process restart so
`rob --continue` resumes an interrupted run at its last checkpoint instead of
replaying from turn 0. It is set on an approval/resume turn and cleared on a
successful completion.
"""

from __future__ import annotations

from organism_console.state_store import SessionState


def test_resume_checkpoint_id_persists_across_reload(tmp_path):
    f = tmp_path / "session.json"
    s = SessionState(f)
    assert s.resume_checkpoint_id is None  # fresh session has no pointer
    s.resume_checkpoint_id = "ckpt-abc123"
    s.save(sync=True)

    # Simulate a terminal close + relaunch: a new state loads the same file.
    s2 = SessionState(f)
    assert s2.resume_checkpoint_id == "ckpt-abc123"


def test_resume_pointer_survives_other_writes(tmp_path):
    f = tmp_path / "session.json"
    s = SessionState(f)
    s.resume_checkpoint_id = "ckpt-xyz"
    s.save(sync=True)
    s.active_agent = "coder"
    s.save(sync=True)
    assert SessionState(f).resume_checkpoint_id == "ckpt-xyz"
