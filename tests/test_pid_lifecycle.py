"""D1 regression: PID-scoped lifecycle — record creation, tree kill, and the
four tolerances (already-dead, missing dir/file, corrupt record, repeated
stop) plus unrelated-process protection.

Exercises the REAL lifecycle.ps1 functions through real PowerShell against
real spawned processes (parent->child trees) in a temp PID directory — never
touching the stack's actual data/pids records and never killing anything the
test did not itself spawn.

The pre-fix shape (for contrast): unified-stop.ps1 was a commented-out stub
and startup recorded no PIDs at all, so no stop path existed; the forbidden
shapes are broad name kills (Stop-Process -Name / taskkill /IM), asserted
statically here as well.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "lifecycle.ps1"
UNIFIED_STOP = ROOT / "unified-stop.ps1"
START_DEV = ROOT / "start-dev.ps1"


@pytest.fixture(scope="module", autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock.

    This module spawns REAL short-lived sleep processes and REAL PowerShell
    to exercise process recording/termination, so subprocess must not be
    mocked here (same pattern as test_cli_opencode.py).
    """
    yield


def _ps(command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a PowerShell command with lifecycle.ps1 dot-sourced."""
    full = f'. "{LIFECYCLE}"\n{command}'
    return subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            full,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _spawn_sleep(seconds: int = 120) -> subprocess.Popen:
    """Spawn a real, harmless long-running process owned by this test."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _spawn_parent_with_child(seconds: int = 120) -> tuple[subprocess.Popen, int]:
    """Spawn a real parent process that spawns a real child and stays alive.

    Returns (parent_popen, child_pid). The child's ParentProcessId is the
    parent, so taskkill /PID <parent> /T must take the child with it.
    """
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(%d)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(%d)\n"
    ) % (seconds, seconds)
    parent = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    line = parent.stdout.readline().strip()
    assert line.isdigit(), f"parent did not report child pid: {line!r}"
    return parent, int(line)


def _alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _kill_quiet(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001 - best-effort test cleanup
        pass


# ---------------------------------------------------------------------------
# 1. PID record creation
# ---------------------------------------------------------------------------


def test_write_service_pid_records_pid_and_process_name(tmp_path):
    pid_dir = tmp_path / "pids"
    proc = _spawn_sleep()
    try:
        res = _ps(
            f'Write-ServicePid -PidDir "{pid_dir}" -Role "sleeper" -ProcessId {proc.pid}'
        )
        assert res.returncode == 0, res.stderr
        record = pid_dir / "sleeper.pid"
        assert record.exists(), "PID record file not created"
        raw = record.read_text(encoding="ascii").strip()
        assert raw == f"{proc.pid}|python", (
            f"record must be '<pid>|<ProcessName>', got {raw!r}"
        )
    finally:
        _kill_quiet(proc)


def test_write_service_pid_skips_already_dead_process(tmp_path):
    pid_dir = tmp_path / "pids"
    proc = _spawn_sleep()
    dead_pid = proc.pid
    _kill_quiet(proc)
    # PID must actually be gone before we probe the dead-path.
    for _ in range(50):
        if not _alive(dead_pid):
            break
        time.sleep(0.1)
    assert not _alive(dead_pid)

    res = _ps(
        f'Write-ServicePid -PidDir "{pid_dir}" -Role "ghost" -ProcessId {dead_pid}'
    )
    assert res.returncode == 0, res.stderr
    assert not (pid_dir / "ghost.pid").exists(), (
        "a dead process must not be recorded (would produce a stale record "
        "at startup)"
    )


# ---------------------------------------------------------------------------
# 2. Tree kill of recorded processes
# ---------------------------------------------------------------------------


def test_stop_kills_recorded_process_tree(tmp_path):
    """Recorded root + its child both die via PID-scoped tree kill."""
    pid_dir = tmp_path / "pids"
    parent, child_pid = _spawn_parent_with_child()
    try:
        assert _alive(parent.pid) and _alive(child_pid)
        res = _ps(
            f'Write-ServicePid -PidDir "{pid_dir}" -Role "proxy" -ProcessId {parent.pid}'
        )
        assert res.returncode == 0, res.stderr

        res = _ps(f'Stop-RecordedServices -PidDir "{pid_dir}"')
        assert res.returncode == 0, res.stderr + res.stdout
        assert "stopped PID" in res.stdout, res.stdout

        # Both must be gone (allow reaping grace).
        for _ in range(50):
            if not _alive(parent.pid) and not _alive(child_pid):
                break
            time.sleep(0.1)
        assert not _alive(parent.pid), "recorded root survived the tree kill"
        assert not _alive(child_pid), (
            "descendant survived the tree kill — grandchildren would orphan "
            "(model_router/llama/MCP-node class)"
        )
        assert not (pid_dir / "proxy.pid").exists(), (
            "record must be removed after a successful stop (repeated-stop "
            "tolerance)"
        )
    finally:
        _kill_quiet(parent)
        try:
            psutil.Process(child_pid).kill()
        except Exception:  # noqa: BLE001 - already dead is fine
            pass


# ---------------------------------------------------------------------------
# 3. Tolerance: already-dead PID, missing dir/file, corrupt record, repeated stop
# ---------------------------------------------------------------------------


def test_stop_tolerates_already_dead_record(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    proc = _spawn_sleep()
    dead_pid = proc.pid
    _kill_quiet(proc)
    for _ in range(50):
        if not _alive(dead_pid):
            break
        time.sleep(0.1)
    (pid_dir / "dead.pid").write_text(f"{dead_pid}|python", encoding="ascii")

    res = _ps(f'Stop-RecordedServices -PidDir "{pid_dir}"')
    assert res.returncode == 0, res.stderr
    assert "already dead" in res.stdout, res.stdout
    assert not (pid_dir / "dead.pid").exists(), "stale record must be cleaned up"


def test_stop_tolerates_missing_pid_dir(tmp_path):
    missing = tmp_path / "no-such-dir"
    res = _ps(f'Stop-RecordedServices -PidDir "{missing}"')
    assert res.returncode == 0, res.stderr
    assert "no PID records" in res.stdout, res.stdout


def test_stop_tolerates_corrupt_record(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    (pid_dir / "garbage.pid").write_text("not-a-pid-record\x00junk", encoding="ascii")

    res = _ps(f'Stop-RecordedServices -PidDir "{pid_dir}"')
    assert res.returncode == 0, res.stderr
    assert "unreadable" in res.stdout, res.stdout
    assert not (pid_dir / "garbage.pid").exists()


def test_repeated_stop_is_a_noop(tmp_path):
    """Stopping twice (and after all records are gone) must not error."""
    pid_dir = tmp_path / "pids"
    proc = _spawn_sleep()
    try:
        res = _ps(
            f'Write-ServicePid -PidDir "{pid_dir}" -Role "sleeper" -ProcessId {proc.pid}'
        )
        assert res.returncode == 0, res.stderr

        res1 = _ps(f'Stop-RecordedServices -PidDir "{pid_dir}"')
        assert res1.returncode == 0, res1.stderr + res1.stdout
        assert "stopped PID" in res1.stdout

        res2 = _ps(f'Stop-RecordedServices -PidDir "{pid_dir}"')
        assert res2.returncode == 0, res2.stderr + res2.stdout
        assert "no PID records" in res2.stdout, (
            f"second stop must be a clean no-op, got: {res2.stdout}"
        )
    finally:
        _kill_quiet(proc)
        for _ in range(50):
            if not _alive(proc.pid):
                break
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# 4. Unrelated-process protection
# ---------------------------------------------------------------------------


def test_stop_does_not_kill_unrecorded_process(tmp_path):
    """A live process NOT in the PID dir must survive the stop pass."""
    pid_dir = tmp_path / "pids"
    unrelated = _spawn_sleep()
    recorded = _spawn_sleep()
    try:
        res = _ps(
            f'Write-ServicePid -PidDir "{pid_dir}" -Role "owned" -ProcessId {recorded.pid}'
        )
        assert res.returncode == 0, res.stderr

        res = _ps(f'Stop-RecordedServices -PidDir "{pid_dir}"')
        assert res.returncode == 0, res.stderr + res.stdout

        for _ in range(50):
            if not _alive(recorded.pid):
                break
            time.sleep(0.1)
        assert not _alive(recorded.pid), "recorded process must be stopped"
        assert _alive(unrelated.pid), (
            "unrelated (unrecorded) process was killed — the stop path must "
            "target only recorded PIDs"
        )
    finally:
        _kill_quiet(unrelated)
        _kill_quiet(recorded)


def test_stop_refuses_pid_reuse_with_mismatched_name(tmp_path):
    """Record holds a live PID but a WRONG process name (recycled PID):
    the stop must refuse to kill it."""
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    survivor = _spawn_sleep()
    try:
        assert _alive(survivor.pid)
        # Forge a record for this live PID under a name it does not have.
        (pid_dir / "reused.pid").write_text(
            f"{survivor.pid}|definitely-not-python", encoding="ascii"
        )

        res = _ps(f'Stop-RecordedServices -PidDir "{pid_dir}"')
        assert res.returncode == 0, res.stderr
        assert "PID reused" in res.stdout, res.stdout
        assert _alive(survivor.pid), (
            "process with mismatched recorded name was killed — PID-reuse "
            "guard failed"
        )
        assert not (pid_dir / "reused.pid").exists(), (
            "refused record must still be cleaned up"
        )
    finally:
        _kill_quiet(survivor)


# ---------------------------------------------------------------------------
# 5. Static invariants: recording wiring + no broad name kills
# ---------------------------------------------------------------------------


def test_start_dev_records_all_four_roles():
    src = START_DEV.read_text(encoding="utf-8")
    for role in ('"proxy"', '"qdrant"', '"backend"', '"frontend"'):
        assert f"-Role {role}" in src, f"start-dev.ps1 does not record {role}"
    assert "lifecycle.ps1" in src, "start-dev.ps1 must dot-source lifecycle.ps1"
    assert "-PassThru" in src, "qdrant Start-Process must capture its PID"


def test_unified_stop_is_pid_scoped_only():
    src = UNIFIED_STOP.read_text(encoding="utf-8")
    assert "Stop-RecordedServices" in src, "unified-stop must call the PID stop"
    assert "lifecycle.ps1" in src
    # Forbidden shapes anywhere in the stop path (D1 hard constraint).
    assert "Stop-Process" not in src, "broad Stop-Process found in unified-stop"
    assert "taskkill /IM" not in src, "name-based taskkill found in unified-stop"
    assert "Get-Process -Name" not in src, (
        "enumeration by process name found in unified-stop — PID-scoped only"
    )


def test_lifecycle_helper_has_no_name_based_kills():
    src = LIFECYCLE.read_text(encoding="utf-8")
    # Only EXECUTED lines matter — comments legitimately document the ban and
    # the broken-taskkill environment finding.
    code_lines = [
        line for line in src.splitlines() if not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "taskkill" not in code, (
        "taskkill is broken on this machine (62s timeout, target survives) "
        "and must not be invoked"
    )
    assert "Stop-Process -Name" not in code, (
        "name-based Stop-Process found — termination must be PID-scoped only"
    )
    # Tree-kill requirement: the helper must enumerate descendants (Toolhelp32
    # snapshot) so children do not orphan when the root dies.
    assert "SwarmToolhelp" in src and "Get-PidTree" in src, (
        "process-tree enumeration required so descendants do not orphan"
    )
    # Actual Stop-Process INVOCATIONS (command position, incl. piped) must
    # carry -Id. Log strings merely mentioning "Stop-Process" are fine.
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        invoked = re.search(r"(^|[|;&]\s*)Stop-Process\b", stripped)
        if invoked:
            assert "-Id" in stripped, (
                f"Stop-Process invocation without -Id at line {i}: {line!r}"
            )
        assert not re.search(r"Stop-Process\s+-Name", stripped), (
            f"name-based Stop-Process at line {i}: {line!r}"
        )


def test_start_dev_stop_cleanup_is_pid_scoped():
    src = START_DEV.read_text(encoding="utf-8")
    # The finally block must use the PID stop, and every Stop-Process line in
    # the whole script must remain commented (the legacy name-kill shape).
    assert "Stop-RecordedServices" in src, (
        "start-dev.ps1 finally block must run PID-scoped cleanup"
    )
    for i, line in enumerate(src.splitlines(), 1):
        if "Stop-Process" in line:
            assert line.lstrip().startswith("#"), (
                f"uncommented Stop-Process (name kill) at line {i}: {line!r}"
            )
