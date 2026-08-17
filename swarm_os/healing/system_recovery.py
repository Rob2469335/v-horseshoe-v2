"""Whole-computer recovery actions — the remedy half of system self-healing.

Every action is registry-keyed by the signal's component so the RecoveryEngine
can dispatch them like the existing infra actions. Destructive actions
(kill/clean/restart) rely on the Governor's approval_required gate — they only
run once a human approves. Safe actions (free_memory) may auto-run.

Safety rails:
  - _NEVER_TOUCH process allowlist is enforced inside every action (defense in
    depth — the action must refuse even if the gate was bypassed).
  - Temp cleanup only deletes stale files (>24h) inside the OS temp folder.
  - Kills are SIGTERM-first (graceful) with a short grace window before force.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

log = logging.getLogger("zenith_healing")

from .system_probes import _NEVER_TOUCH, _TEMP_KEEP_DIRS

_STALE_AGE_SECONDS = 24 * 3600
_KILL_GRACE_SECONDS = 3.0


def _process_name(pid: int) -> str:
    import psutil

    try:
        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        return ""


def _is_never_touch(pid: int, name: str | None = None) -> bool:
    return (name or _process_name(pid)).lower() in _NEVER_TOUCH


def free_memory(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """Empty the working set of non-critical processes to relieve RAM pressure.
    Non-destructive (no kills); safe enough to auto-run under governor gate."""
    import psutil
    import ctypes

    emptied = []
    skipped = 0
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = proc.info["pid"]
            name = (proc.info["name"] or "").lower()
            if _is_never_touch(pid, name) or pid == os.getpid():
                skipped += 1
                continue
            handle = ctypes.windll.kernel32.OpenProcess(
                0x0200, False, pid
            )  # PROCESS_SET_QUOTA
            if not handle:
                skipped += 1
                continue
            try:
                ctypes.windll.psapi.EmptyWorkingSet(handle)
                emptied.append({"pid": pid, "name": proc.info["name"]})
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
    log.info(
        "Freed working sets of %d non-critical processes (skipped %d)",
        len(emptied),
        skipped,
    )
    return {
        "ok": True,
        "action": "free_memory",
        "emptied": len(emptied),
        "skipped": skipped,
    }


def clean_temp_files(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """Delete stale files (>24h) in the OS temp folders. Allowlist-scoped: only
    the OS temp root, never project/cache/data dirs. Non-destructive to anything
    actively used (stale-only)."""
    import tempfile

    roots = {tempfile.gettempdir()}
    if os.name == "nt":
        roots |= {os.environ.get("TEMP", ""), os.environ.get("TMP", "")}
    roots = {r for r in roots if r and os.path.isdir(r)}

    removed = 0
    freed_bytes = 0
    errors = []
    cutoff = time.time() - _STALE_AGE_SECONDS
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _TEMP_KEEP_DIRS]
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    st = os.stat(fp)
                    if st.st_mtime < cutoff:
                        size = st.st_size
                        os.remove(fp)
                        removed += 1
                        freed_bytes += size
                except OSError as exc:
                    errors.append(str(exc))
    log.info("Cleaned %d stale temp files (%s)", removed, f"{freed_bytes / 1e6:.1f} MB")
    return {
        "ok": True,
        "action": "clean_temp_files",
        "removed_files": removed,
        "freed_mb": round(freed_bytes / 1e6, 1),
        "errors": len(errors),
    }


def kill_runaway_process(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """Terminate a runaway process named in the signal detail. Graceful
    terminate first, then force after a short grace window. NEVER touches
    _NEVER_TOUCH processes."""
    import psutil

    detail = anomaly.get("detail") or {}
    processes = detail.get("processes", []) if isinstance(detail, dict) else []
    targets = []
    for p in processes:
        if isinstance(p, dict) and p.get("pid"):
            pid = int(p["pid"])
            name = p.get("name", "")
            if _is_never_touch(pid, name):
                log.warning("Refusing to kill protected process %s (%s)", pid, name)
                continue
            targets.append((pid, name))

    if not targets:
        return {
            "ok": False,
            "action": "kill_runaway_process",
            "reason": "no safe kill targets in signal detail",
        }

    killed = []
    for pid, name in targets:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=_KILL_GRACE_SECONDS)
            except psutil.TimeoutExpired:
                if proc.is_running():
                    proc.kill()
            killed.append({"pid": pid, "name": name})
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("Could not kill %s (%s): %s", pid, name, exc)
    return {"ok": bool(killed), "action": "kill_runaway_process", "killed": killed}


def restart_stopped_service(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """Restart a Windows service whose critical status regressed. The service
    name must be present in the signal detail (never guess from free text)."""
    detail = anomaly.get("detail") or {}
    service_name = detail.get("service_name") if isinstance(detail, dict) else None
    if not service_name:
        return {
            "ok": False,
            "action": "restart_stopped_service",
            "reason": "no service_name in signal detail",
        }
    try:
        import win32serviceutil

        status = win32serviceutil.QueryServiceStatus(service_name)
        log.info("Restarting service %s (current state %s)", service_name, status[1])
        win32serviceutil.RestartService(service_name)
        return {
            "ok": True,
            "action": "restart_stopped_service",
            "service": service_name,
        }
    except Exception as exc:
        log.error("Failed to restart service %s: %s", service_name, exc)
        return {
            "ok": False,
            "action": "restart_stopped_service",
            "service": service_name,
            "error": str(exc),
        }


def tail_event_log(
    window_minutes: int = 60, max_events: int = 500
) -> list[Dict[str, Any]]:
    """Read recent Windows Event Log entries (System + Application). Used by the
    event-log probe; report-only, never remediates."""
    try:
        import win32evtlog
    except ImportError:
        return []
    events = []
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(minutes=int(window_minutes))
    for log_name in ("System", "Application"):
        try:
            handle = win32evtlog.OpenEventLog(None, log_name)
            flags = (
                win32evtlog.EVENTLOG_BACKWARDS_READ
                | win32evtlog.EVENTLOG_SEQUENTIAL_READ
            )
            while len(events) < max_events:
                batch = win32evtlog.ReadEventLog(handle, flags, 0)
                if not batch:
                    break
                for evt in batch:
                    try:
                        ts = evt.TimeGenerated
                        if ts < cutoff:
                            break  # backwards read — older than window
                        level = {
                            1: "Error",
                            2: "Warning",
                            4: "Information",
                            8: "Success",
                            16: "Failure",
                        }.get(evt.EventType, "Unknown")
                        events.append(
                            {
                                "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
                                "level": level,
                                "event_id": evt.EventID,
                                "source": evt.SourceName,
                                "message": str(evt.StringInserts)[:200]
                                if evt.StringInserts
                                else str(evt.SourceName),
                            }
                        )
                    except Exception as exc:
                        log.debug("event-log entry skipped: %s", exc)
                        continue
                    if len(events) >= max_events:
                        break
        except Exception as exc:
            log.warning("event-log read failed for %s: %s", log_name, exc)
            continue
    return events


# component/issue -> recovery action. Safe actions auto-run under governor;
# destructive ones (kill/clean/restart) wait for approval.
SYSTEM_RECOVERY_ACTIONS: Dict[str, Any] = {
    "memory_pressure": free_memory,
    "disk_space": clean_temp_files,
    "runaway_process": kill_runaway_process,
    "stopped_service": restart_stopped_service,
    "temp_growth": clean_temp_files,
}

# Which system issues are safe enough to auto-run when the governor says so.
DESTRUCTIVE_SYSTEM_ACTIONS = {
    "disk_space",
    "runaway_process",
    "stopped_service",
    "temp_growth",
}
