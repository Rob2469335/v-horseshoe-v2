"""Windows Job Object containment for build subprocesses.

A per-task timeout that only kills the immediate child leaves grandchildren
(clone helpers, uv, pip, pytest workers) running. A Job Object with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` terminates the whole associated tree when
the job is terminated or its last handle closes.

``run_in_job`` starts a subprocess, assigns it to a fresh job as soon as the
process handle exists, and on timeout terminates the job rather than only the
child. On non-Windows hosts it falls back to ``subprocess.run`` so CI can run
the module; containment is a Windows property.

Residual: a grandchild created in the short window between process creation and
``AssignProcessToJobObject`` is not contained. Closing that window needs
CREATE_SUSPENDED plus a thread handle the standard library does not expose.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import dataclasses
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

#: Captured before any test fixture can replace ``subprocess.Popen``. The suite
#: patches Popen globally to block accidental spawns; containment tests and the
#: builder must still spawn real processes.
_REAL_POPEN = subprocess.Popen

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

#: Termination modes reported on a timeout. ``TERMINATION_FAILURE`` means the
#: implementation could not establish that the tree exited.
NORMAL_JOB_TERMINATION = "NORMAL_JOB_TERMINATION"
TASKKILL_FALLBACK = "TASKKILL_FALLBACK"
TERMINATION_FAILURE = "TERMINATION_FAILURE"


@dataclasses.dataclass(frozen=True)
class TerminationOutcome:
    mode: str
    confirmed: bool
    detail: str = ""


if IS_WINDOWS:  # pragma: no branch - import-time platform switch
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class JobContainmentError(RuntimeError):
    """A job could not be created or the process could not be assigned."""


def _make_job() -> int:
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise JobContainmentError(
            f"CreateJobObject failed (winerror {ctypes.get_last_error()})"
        )
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info),
    ):
        err = ctypes.get_last_error()
        _kernel32.CloseHandle(job)
        raise JobContainmentError(f"SetInformationJobObject failed (winerror {err})")
    return int(job)


def _close(job: int) -> None:
    if job:
        _kernel32.CloseHandle(wintypes.HANDLE(job))


def _assign(job: int, process_handle: int) -> None:
    if not _kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job), wintypes.HANDLE(process_handle)
    ):
        raise JobContainmentError(
            f"AssignProcessToJobObject failed (winerror {ctypes.get_last_error()})"
        )


def _wait_exit(proc: subprocess.Popen, seconds: float) -> bool:
    """Wait a bounded time for the process to exit; report whether it did."""
    try:
        proc.wait(timeout=seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


TASKKILL_TIMEOUT_SECONDS = 10.0
POST_TERMINATION_READ_SECONDS = 5.0


def _terminate_job(job: int) -> bool:
    """Request job termination; return the Win32 result, never assume it."""
    return bool(_kernel32.TerminateJobObject(wintypes.HANDLE(job), 1))


def _run_taskkill(pid: int) -> tuple[bool, str]:
    """Run taskkill with a bounded wait; return (ok, detail)."""
    try:
        # Use the real Popen captured at import: the test suite patches
        # subprocess.Popen globally, and a mocked taskkill would silently do
        # nothing while the code believed it had tried the fallback.
        kill = _REAL_POPEN(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as exc:
        return False, f"taskkill could not start: {exc}"
    try:
        kill.communicate(timeout=TASKKILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        kill.kill()
        try:
            kill.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return False, "taskkill timed out"
    if kill.returncode == 0:
        return True, "taskkill /T /F issued"
    return False, f"taskkill failed rc={kill.returncode}"


def _dispose_pipes(proc: subprocess.Popen) -> None:
    """Close pipe handles a surviving descendant may still hold open."""
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name, None)
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass
        finally:
            try:
                setattr(proc, name, None)
            except (AttributeError, OSError):
                pass


def _bounded_communicate(
    proc: subprocess.Popen, seconds: float
) -> tuple[object, object, bool]:
    """Post-termination handling with a hard bound; never waits for EOF.

    Returns ``(stdout, stderr, drained)``. ``drained`` is always False here:
    output is deliberately not drained, because the first
    ``communicate(timeout=...)`` leaves reader threads holding the pipe buffer
    locks, so a second read (or a close) can block until a surviving descendant
    exits. A daemon thread disposes the handles best-effort; the caller never
    waits for EOF.
    """
    import threading

    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        pass
    threading.Thread(
        target=_dispose_pipes, args=(proc,), daemon=True, name="f2-s8-pipe-dispose"
    ).start()
    return None, None, False


def _terminate_tree(job: int, proc: subprocess.Popen) -> TerminationOutcome:
    """Terminate the tree and report what was actually established.

    A job is the normal path; ``taskkill /T`` is used only when the process was
    never assigned. A failed termination request, a failed/overrun taskkill, or
    an unobserved exit all yield ``TERMINATION_FAILURE`` — never success.
    """
    if job:
        if not _terminate_job(job):
            message = (
                "TerminateJobObject failed "
                f"(winerror {ctypes.get_last_error()}) for pid {proc.pid}"
            )
            print(f"TERMINATION_FAILURE: {message}", file=sys.stderr)
            return TerminationOutcome(
                mode=TERMINATION_FAILURE, confirmed=False, detail=message
            )
        mode = NORMAL_JOB_TERMINATION
        detail = "TerminateJobObject issued"
    else:
        ok, detail = _run_taskkill(proc.pid)
        if not ok:
            message = f"{detail} for pid {proc.pid}"
            print(f"TERMINATION_FAILURE: {message}", file=sys.stderr)
            return TerminationOutcome(
                mode=TERMINATION_FAILURE, confirmed=False, detail=message
            )
        mode = TASKKILL_FALLBACK
    if _wait_exit(proc, 5.0):
        return TerminationOutcome(mode=mode, confirmed=True, detail=detail)
    message = f"{mode} could not confirm process exit for pid {proc.pid}"
    print(f"TERMINATION_FAILURE: {message}", file=sys.stderr)
    return TerminationOutcome(mode=TERMINATION_FAILURE, confirmed=False, detail=message)


def run_in_job(cmd, *, cwd=None, timeout: float | None = None, env=None):
    """Run ``cmd`` in a kill-on-close job; terminate the tree on timeout.

    Returns ``subprocess.CompletedProcess``. Raises ``subprocess.TimeoutExpired``
    (with a ``cleanup`` attribute carrying :class:`TerminationOutcome`) after
    terminating the tree, so callers see the same exception they would get from
    ``subprocess.run(timeout=...)``.

    If the host blocks nested jobs, the fallback terminates the tree with
    ``taskkill /T`` and the timeout exception's ``cleanup.mode`` is
    ``TASKKILL_FALLBACK``. A ``TERMINATION_FAILURE`` outcome means cleanup could
    not be established and must never be reported as success. Post-termination
    pipe handling is bounded: the timeout path never waits for EOF, because a
    surviving descendant can hold the inherited write ends open.
    """
    if not IS_WINDOWS:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )

    job = _make_job()
    proc = None
    assigned = False
    terminated = False
    try:
        proc = _REAL_POPEN(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env,
        )
        try:
            _assign(job, int(proc._handle))
            assigned = True
        except JobContainmentError:
            # Nested jobs are unavailable in this host/CI sandbox. Keep the
            # process tree killable via taskkill instead of failing the build.
            _close(job)
            job = 0
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            cleanup = _terminate_tree(job if assigned else 0, proc)
            out, err, drained = _bounded_communicate(
                proc, POST_TERMINATION_READ_SECONDS
            )
            detail = cleanup.detail
            if not drained:
                detail += "; timeout output not drained (bounded handling)"
            cleanup = dataclasses.replace(cleanup, detail=detail)
            terminated = True
            exc = subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
            exc.cleanup = cleanup
            raise exc
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        if proc is not None and proc.poll() is None and not terminated:
            _terminate_tree(job if assigned else 0, proc)
        _close(job)
