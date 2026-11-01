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
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

#: Captured before any test fixture can replace ``subprocess.Popen``. The suite
#: patches Popen globally to block accidental spawns; containment tests and the
#: builder must still spawn real processes.
_REAL_POPEN = subprocess.Popen

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

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


def run_in_job(cmd, *, cwd=None, timeout: float | None = None):
    """Run ``cmd`` in a kill-on-close job; terminate the tree on timeout.

    Returns ``subprocess.CompletedProcess``. Raises ``subprocess.TimeoutExpired``
    after terminating the job, so callers see the same exception they would get
    from ``subprocess.run(timeout=...)``.

    If the host blocks nested jobs (``AssignProcessToJobObject`` denied because
    the current process is already in a job that forbids nesting), the fallback
    terminates the tree with ``taskkill /T``. That is weaker than a job but still
    tree-scoped, and it is recorded by the ``TASKKILL_FALLBACK`` message on the
    timeout path only.
    """
    if not IS_WINDOWS:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )

    job = _make_job()
    proc = None
    assigned = False
    try:
        proc = _REAL_POPEN(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
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
            _terminate_tree(job if assigned else 0, proc)
            out, err = proc.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        if proc is not None and proc.poll() is None:
            _terminate_tree(job if assigned else 0, proc)
            proc.wait()
        _close(job)


def _terminate_tree(job: int, proc: subprocess.Popen) -> None:
    """Kill the whole tree: job first, ``taskkill /T`` as the fallback."""
    if job:
        _kernel32.TerminateJobObject(wintypes.HANDLE(job), 1)
        return
    subprocess.run(
        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
