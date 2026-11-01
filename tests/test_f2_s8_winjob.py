"""Job Object containment: a timeout kills the whole process tree.

Windows-only containment tests. On other platforms ``run_in_job`` falls back to
``subprocess.run`` and these tests skip.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import subprocess
import sys
import time

import pytest

from qwen_train.f2_s8_winjob import IS_WINDOWS, run_in_job

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="Job Objects are Windows-only")

STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _alive(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def test_run_in_job_returns_output():
    proc = run_in_job(
        [sys.executable, "-c", "print('hello')"], timeout=30
    )
    assert proc.returncode == 0
    assert "hello" in proc.stdout


def test_timeout_raises_and_kills_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "tree.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{pid_file}', 'w').write(str(p.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_in_job([sys.executable, str(script)], timeout=3)
    deadline = time.time() + 5
    while not pid_file.exists() and time.time() < deadline:
        time.sleep(0.1)
    assert pid_file.exists(), "grandchild pid was never recorded"
    grandchild = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.time() + 5
    while _alive(grandchild) and time.time() < deadline:
        time.sleep(0.2)
    assert not _alive(grandchild), "grandchild survived the job termination"


def test_pilot_build_run_delegates_to_the_job(monkeypatch):
    from qwen_train import f2_s8_winjob
    from qwen_train import f2_s8_pilot_build as pb

    called = {}

    def fake(cmd, *, cwd=None, timeout=None):
        called["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(f2_s8_winjob, "run_in_job", fake)
    proc = pb.run([sys.executable, "-V"], timeout=5)
    assert called["cmd"] == [sys.executable, "-V"]
    assert proc.returncode == 0


# --- B: termination outcomes are explicit and never overclaim success --------


def _sleep_child(seconds=30):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_timeout_reports_a_confirmed_job_or_fallback_outcome():
    with pytest.raises(subprocess.TimeoutExpired) as info:
        run_in_job(_sleep_child(), timeout=2)
    cleanup = info.value.cleanup
    assert cleanup.confirmed is True
    assert cleanup.mode in {"NORMAL_JOB_TERMINATION", "TASKKILL_FALLBACK"}


def test_assignment_failure_uses_and_reports_the_fallback(monkeypatch):
    from qwen_train import f2_s8_winjob as W

    def _refuse(job, handle):
        raise W.JobContainmentError("simulated nested-job denial")

    monkeypatch.setattr(W, "_assign", _refuse)
    with pytest.raises(subprocess.TimeoutExpired) as info:
        run_in_job(_sleep_child(), timeout=2)
    assert info.value.cleanup.mode == "TASKKILL_FALLBACK"
    assert info.value.cleanup.confirmed is True


def test_failed_cleanup_is_reported_as_failure(monkeypatch, capsys):
    from qwen_train import f2_s8_winjob as W

    monkeypatch.setattr(W, "_wait_exit", lambda proc, seconds: False)
    with pytest.raises(subprocess.TimeoutExpired) as info:
        run_in_job(_sleep_child(), timeout=2)
    assert info.value.cleanup.mode == "TERMINATION_FAILURE"
    assert info.value.cleanup.confirmed is False
    assert "TERMINATION_FAILURE" in capsys.readouterr().err


def test_collection_timeout_reports_cleanup_outcome(monkeypatch, tmp_path):
    from qwen_train import f2_s8_build_executor as X
    from qwen_train import f2_s8_winjob as W

    ws = tmp_path / "ws"
    (ws / "repo").mkdir(parents=True)
    cleanup = W.TerminationOutcome(mode=W.NORMAL_JOB_TERMINATION, confirmed=True)

    def _timeout(cmd, *, cwd=None, timeout=None, env=None):
        exc = subprocess.TimeoutExpired(cmd, timeout)
        exc.cleanup = cleanup
        raise exc

    monkeypatch.setattr(W, "run_in_job", _timeout)
    ok, code, detail = X.check_declared_nodes_collect(
        ws, ["t"], "pytest", python=sys.executable, timeout=10
    )
    assert ok is False
    assert code == "COLLECT_TIMEOUT"
    assert "cleanup=NORMAL_JOB_TERMINATION confirmed=True" in detail


# --- F-B1: post-timeout pipe handling is bounded ----------------------------


def test_bounded_communicate_gives_up_on_a_descendant_holding_pipes(tmp_path):
    from qwen_train import f2_s8_winjob as W

    pid_file = tmp_path / "holder.pid"
    script = tmp_path / "holder.py"
    script.write_text(
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{pid_file}', 'w').write(str(p.pid))\n",
        encoding="utf-8",
    )
    proc = W._REAL_POPEN(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        proc.wait(timeout=30)  # parent exits; the grandchild keeps the pipes open
        start = time.monotonic()
        out, err, drained = W._bounded_communicate(proc, 1.0)
        elapsed = time.monotonic() - start
        assert out is None and err is None
        assert drained is False  # draining is deliberately abandoned
        assert elapsed < 8
        time.sleep(0.2)
    finally:
        try:
            if pid_file.exists():
                holder = int(pid_file.read_text(encoding="utf-8").strip())
                kill = W._REAL_POPEN(
                    ["taskkill", "/PID", str(holder), "/F"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                kill.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


def test_terminate_job_failure_is_not_reported_as_normal(monkeypatch, capsys):
    import types
    from qwen_train import f2_s8_winjob as W

    monkeypatch.setattr(W, "_terminate_job", lambda job: False)
    fake = types.SimpleNamespace(pid=4242, wait=lambda timeout=None: 0)
    outcome = W._terminate_tree(123, fake)
    assert outcome.mode == "TERMINATION_FAILURE"
    assert outcome.confirmed is False
    assert "TERMINATION_FAILURE" in capsys.readouterr().err


def test_taskkill_failure_is_not_reported_as_success(monkeypatch, capsys):
    import types
    from qwen_train import f2_s8_winjob as W

    monkeypatch.setattr(W, "_run_taskkill", lambda pid: (False, "taskkill failed rc=5"))
    fake = types.SimpleNamespace(pid=4242, wait=lambda timeout=None: 0)
    outcome = W._terminate_tree(0, fake)
    assert outcome.mode == "TERMINATION_FAILURE"
    assert outcome.confirmed is False
    assert "TERMINATION_FAILURE" in capsys.readouterr().err


def test_taskkill_success_remains_distinguishable_from_job_termination(monkeypatch):
    import types
    from qwen_train import f2_s8_winjob as W

    monkeypatch.setattr(W, "_run_taskkill", lambda pid: (True, "taskkill /T /F issued"))
    fake = types.SimpleNamespace(pid=4242, wait=lambda timeout=None: 0)
    outcome = W._terminate_tree(0, fake)
    assert outcome.mode == "TASKKILL_FALLBACK"
    assert outcome.confirmed is True


def test_job_success_remains_normal_job_termination(monkeypatch):
    import types
    from qwen_train import f2_s8_winjob as W

    monkeypatch.setattr(W, "_terminate_job", lambda job: True)
    fake = types.SimpleNamespace(pid=4242, wait=lambda timeout=None: 0)
    outcome = W._terminate_tree(123, fake)
    assert outcome.mode == "NORMAL_JOB_TERMINATION"
    assert outcome.confirmed is True


def test_timeout_does_not_wait_for_a_pipe_holding_descendant(tmp_path):
    """F-B1: the old second communicate() blocked until the descendant exited."""
    from qwen_train import f2_s8_winjob as W

    pid_file = tmp_path / "holder.pid"
    script = tmp_path / "holder.py"
    script.write_text(
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{pid_file}', 'w').write(str(p.pid))\n",
        encoding="utf-8",
    )
    start = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired) as info:
            run_in_job([sys.executable, str(script)], timeout=2)
        elapsed = time.monotonic() - start
        assert elapsed < 20, "post-timeout handling waited for the descendant"
        assert info.value.cleanup is not None
    finally:
        if pid_file.exists():
            holder = int(pid_file.read_text(encoding="utf-8").strip())
            kill = W._REAL_POPEN(
                ["taskkill", "/PID", str(holder), "/F"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            kill.communicate(timeout=10)


def test_taskkill_nonzero_result_is_failure(monkeypatch):
    from qwen_train import f2_s8_winjob as W

    class _Kill:
        returncode = 5

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(W, "_REAL_POPEN", lambda *a, **k: _Kill())
    ok, detail = W._run_taskkill(4242)
    assert ok is False
    assert "rc=5" in detail


def test_taskkill_timeout_is_failure(monkeypatch):
    from qwen_train import f2_s8_winjob as W

    class _Kill:
        returncode = None
        killed = False

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(["taskkill"], timeout)

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    kill = _Kill()
    monkeypatch.setattr(W, "_REAL_POPEN", lambda *a, **k: kill)
    ok, detail = W._run_taskkill(4242)
    assert ok is False
    assert "timed out" in detail
    assert kill.killed is True
