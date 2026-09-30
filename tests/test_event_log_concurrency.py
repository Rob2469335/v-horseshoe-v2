"""D5 regression: concurrent offset/state writers must not corrupt the temp file
or leave a malformed watermark.

The pre-fix defect: ``event_log_repo._atomic_write_text`` used a FIXED temp name
(``{path.name}.tmp``), so two concurrent ``save_offset`` calls staged into the
SAME temp file. On Windows that is a sharing violation: while one writer holds
its temp open (or has just staged it), the other writer's ``os.replace``/
``write_text`` on the shared path raises PermissionError — the offset update is
lost or the ingest loop crashes. With per-write unique temps (the repo's
established uuid-temp + os.replace pattern in ``swarm_os.lib.atomic_io``) each
writer stages into its own file and both promote cleanly.

Both tests drive the REAL ``EventLogRepository.save_offset`` from REAL threads.

The first test makes the race DETERMINISTIC: thread B completes its staged
write inside ``Path.write_text`` and holds an open handle on the temp (exactly
the window a concurrent writer can hit), then thread A runs a full
``save_offset`` while that handle is open. Pre-fix both writers share one temp
path -> A's promote fails with PermissionError. Post-fix each writer has a
unique temp -> A's promote targets a different file and succeeds, and B's own
promote succeeds after it closes. The second test asserts the same invariants
under an unsynchronized multi-writer hammer (observed pre-fix as a burst of
PermissionErrors).
"""

from __future__ import annotations

import json
import pathlib
import threading

from swarm_os.repositories.event_log_repo import EventLogRepository


def _repo(tmp_path):
    return EventLogRepository(
        event_log_path=tmp_path / "events.jsonl",
        watermark_path=tmp_path / "wm.json",
        state_path=tmp_path / "state.json",
    )


def test_concurrent_offset_writer_blocked_by_open_temp_do_not_corrupt(
    tmp_path, monkeypatch
):
    """Deterministic fixed-temp race through the REAL save_offset seam.

    Thread B stages its write, then holds an open handle on the staged temp
    (the window in which a second writer must not share that path). Thread A
    runs a full save_offset while B's handle is open. Pre-fix A stages into the
    SAME fixed temp and its os.replace raises PermissionError (Windows sharing
    violation); post-fix A stages into a unique temp and both writers succeed
    with a valid final watermark and no temp residue.
    """
    repo = _repo(tmp_path)
    real_write_text = pathlib.Path.write_text
    b_holds = threading.Event()
    a_finished = threading.Event()

    def trapping_write_text(self, *args, **kwargs):
        result = real_write_text(self, *args, **kwargs)
        if threading.current_thread().name == "holder":
            # Hold an open handle on the staged temp while the other writer
            # attempts its full save_offset. The wait is deliberately SHORTER
            # than the FileLock timeout (5.0s) used by _atomic_write_text:
            # pre-fix (no lock) the promoter races in immediately and hits the
            # shared temp -> PermissionError; post-fix the promoter blocks on
            # the lock until this holder releases it, so both writers succeed.
            handle = self.open("r+b")
            try:
                b_holds.set()
                a_finished.wait(timeout=2.0)
            finally:
                handle.close()
        return result

    monkeypatch.setattr(pathlib.Path, "write_text", trapping_write_text)

    errors: list[BaseException] = []

    def holder() -> None:
        try:
            repo.save_offset(111)
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    def promoter() -> None:
        try:
            b_holds.wait(timeout=10)
            repo.save_offset(222)
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)
        finally:
            a_finished.set()

    t_b = threading.Thread(target=holder, name="holder")
    t_a = threading.Thread(target=promoter, name="promoter")
    t_b.start()
    t_a.start()
    t_a.join(timeout=15)
    t_b.join(timeout=15)
    assert not t_a.is_alive() and not t_b.is_alive(), "writer thread deadlocked"

    assert errors == [], (
        f"concurrent save_offset raised (fixed-temp race): "
        f"{[repr(e) for e in errors]}"
    )

    final = json.loads((tmp_path / "wm.json").read_text(encoding="utf-8"))
    assert isinstance(final.get("offset"), int), f"malformed watermark: {final!r}"
    # No temp residue: every staged temp must have been promoted (or cleaned).
    residue = list(tmp_path.glob("*.tmp*"))
    assert residue == [], f"temp files leaked: {residue}"


def test_concurrent_offset_writers_hammer_invariants(tmp_path):
    """Unsynchronized hammer: N threads x M writes -> no exceptions, final state
    always valid JSON with an int offset, and no temp residue."""
    repo = _repo(tmp_path)
    n_threads, n_iters = 8, 50
    errors: list[BaseException] = []
    barrier = threading.Barrier(n_threads)

    def hammer(tid: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(n_iters):
                repo.save_offset(tid * 1000 + i)
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=(t,), name=f"hammer-{t}")
        for t in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "hammer thread did not finish"

    assert errors == [], (
        f"hammered save_offset raised: {[repr(e) for e in errors[:5]]}"
    )
    final = json.loads((tmp_path / "wm.json").read_text(encoding="utf-8"))
    assert isinstance(final.get("offset"), int), f"malformed watermark: {final!r}"
    residue = list(tmp_path.glob("*.tmp*"))
    assert residue == [], f"temp files leaked: {residue}"


def test_save_state_stages_unique_temp_per_write(tmp_path, monkeypatch):
    """save_state shares _atomic_write_text — pin that each write stages through
    a UNIQUE temp name (a fixed temp name re-introduces the race for state too).
    """
    repo = _repo(tmp_path)
    calls: list[str] = []
    real_write_text = pathlib.Path.write_text

    def spy_write_text(self, *args, **kwargs):
        calls.append(self.name)
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", spy_write_text)
    repo.save_state({"a": 1})
    repo.save_state({"b": 2})
    assert len(calls) == 2
    assert calls[0] != calls[1], (
        f"temp names must be unique per write, got {calls!r} "
        "(fixed temp name re-introduces the concurrent-writer race)"
    )
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved == {"b": 2}
