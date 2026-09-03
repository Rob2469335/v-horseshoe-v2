"""Whole-computer health probes — extend the FailureDetector with machine-level
detection (disk, RAM, runaway processes, temp growth, event-log storms).

Design (2026 agent-safety guidance): probes are read-only and cheap. Each probe
returns `{"ok": bool, "detail": {...}}` where `detail` may carry a
`"destructive": True` flag for issues whose remedy touches the machine (kill,
delete, restart). The Governor forces approval_required for destructive signals,
so safe ops auto-heal while destructive ones wait for a human.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Dict

# Processes the swarm must NEVER terminate or memory-trim. Includes the OS core
# and the swarm's own critical processes (empty-working-set would evict the
# model weights and make generation crawl).
_NEVER_TOUCH = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "dwm.exe",
    "explorer.exe",
    "taskmgr.exe",
    "conhost.exe",
    "msmpeng.exe",
    "windowsdefender",
    "llama.exe",
    "llama-server.exe",
    "opencode.exe",
}

# Substrings that identify the legitimate Swarm stack workload (backend, model
# router, qdrant, frontend, training). These are high-CPU/RAM BY DESIGN and must
# not be flagged as runaway_process on startup — recovery would refuse to kill
# them anyway ("no safe kill targets"), producing pure alert noise.
_SWARM_WORKLOAD_CMDLINE = (
    "swarm_os",
    "model_router",
    "uvicorn",
    "qdrant",
    "vite",
    "train_v4",
    "train_v5",
    "organism_console",
    "start-dev",
)

# Subdirectories inside the OS temp folder that are never deleted wholesale
# (actively-used caches / app-private state).
_TEMP_KEEP_DIRS = {"node_modules", "pip", "npm-cache", "uv", ".cache"}

_DISK_THRESHOLD_PERCENT = 90.0
_MEMORY_THRESHOLD_PERCENT = 90.0
_RUNAWAY_CPU_PERCENT = 70.0
_RUNAWAY_MEMORY_MB = 4096
_TEMP_THRESHOLD_GB = 2.0
_EVENTLOG_ERROR_THRESHOLD = 20


def _drive_usage():
    import psutil

    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        yield part.device, part.mountpoint, usage


def check_disk_space(
    threshold_percent: float = _DISK_THRESHOLD_PERCENT,
) -> Dict[str, Any]:
    warnings = []
    for device, mountpoint, usage in _drive_usage():
        if usage.percent >= float(threshold_percent):
            warnings.append(
                {
                    "device": device,
                    "mountpoint": mountpoint,
                    "percent": round(usage.percent, 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                }
            )
    if warnings:
        return {
            "ok": False,
            "detail": {"issue": "disk_space", "destructive": True, "drives": warnings},
        }
    return {"ok": True, "detail": {"issue": "disk_space", "drives": []}}


def check_memory_pressure(
    threshold_percent: float = _MEMORY_THRESHOLD_PERCENT,
) -> Dict[str, Any]:
    import psutil

    vmem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    if vmem.percent >= float(threshold_percent):
        return {
            "ok": False,
            "detail": {
                "issue": "memory_pressure",
                "destructive": False,
                "ram_percent": round(vmem.percent, 1),
                "ram_available_gb": round(vmem.available / (1024**3), 1),
                "swap_percent": round(swap.percent, 1),
            },
        }
    return {
        "ok": True,
        "detail": {"issue": "memory_pressure", "ram_percent": round(vmem.percent, 1)},
    }


def check_runaway_processes(
    cpu_threshold: float = _RUNAWAY_CPU_PERCENT,
    memory_mb_threshold: float = _RUNAWAY_MEMORY_MB,
    top: int = 3,
) -> Dict[str, Any]:
    """Flag processes pinned at high CPU or huge memory. Sustained-hog detection:
    two consecutive samples avoids flagging a legit burst.

    Uses the non-blocking psutil pattern: baseline every process with
    `cpu_percent(None)`, sleep ONCE globally, then sample — otherwise the
    per-process `interval=` blocking calls serialize and take O(n) seconds."""
    import psutil

    runaway = []
    procs = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name in _NEVER_TOUCH:
                continue
            proc.cpu_percent(None)  # baseline (returns 0.0)
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.2)  # single global window — do not sleep per process

    for proc in procs:
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            cmdline = " ".join(info["cmdline"] or []).lower()
            # Skip the legitimate Swarm stack (high CPU/RAM by design).
            if any(s in cmdline for s in _SWARM_WORKLOAD_CMDLINE):
                continue
            cpu = proc.cpu_percent(None)
            mem = proc.memory_info().rss / (1024**2)
            if cpu < float(cpu_threshold) and mem < float(memory_mb_threshold):
                continue
            runaway.append(
                {
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu_percent": round(cpu, 1),
                    "memory_mb": round(mem, 1),
                    "cmdline": " ".join(info["cmdline"] or [])[:200],
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    runaway.sort(key=lambda p: (p["cpu_percent"], p["memory_mb"]), reverse=True)
    if runaway:
        return {
            "ok": False,
            "detail": {
                "issue": "runaway_process",
                "destructive": True,
                "processes": runaway[: max(1, int(top))],
            },
        }
    return {"ok": True, "detail": {"issue": "runaway_process", "processes": []}}


def _walk_size(root: str, cap_bytes: int | None = None) -> int:
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _TEMP_KEEP_DIRS]
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    continue
                if cap_bytes and total > cap_bytes:
                    return total
    except OSError:
        pass
    return total


def _temp_roots() -> set[str]:
    roots = {tempfile.gettempdir()}
    if os.name == "nt":
        roots |= {os.environ.get("TEMP", ""), os.environ.get("TMP", "")}
    return {r for r in roots if r and os.path.isdir(r)}


def check_temp_growth(threshold_gb: float = _TEMP_THRESHOLD_GB) -> Dict[str, Any]:
    cap = int(threshold_gb * 2 * (1024**3))
    total = 0
    largest = []
    for root in _temp_roots():
        root_size = _walk_size(root, cap_bytes=cap)
        total += root_size
        largest.append({"root": root, "gb": round(root_size / (1024**3), 2)})
    total_gb = total / (1024**3)
    largest.sort(key=lambda d: d["gb"], reverse=True)
    if total_gb >= float(threshold_gb):
        return {
            "ok": False,
            "detail": {
                "issue": "temp_growth",
                "destructive": True,
                "temp_gb": round(total_gb, 2),
                "roots": largest[:3],
            },
        }
    return {
        "ok": True,
        "detail": {
            "issue": "temp_growth",
            "temp_gb": round(total_gb, 2),
            "roots": largest[:3],
        },
    }


def check_event_log_errors(
    window_minutes: int = 60, threshold: int = _EVENTLOG_ERROR_THRESHOLD
) -> Dict[str, Any]:
    """Count recent Error-level entries in System/Application logs. Heavy — runs
    in a thread. Report-only; the remedy is human judgement, not an auto-kill."""
    try:
        from .system_recovery import tail_event_log
    except Exception:
        return {"ok": True, "detail": {"issue": "event_log_storm", "available": False}}
    try:
        events = tail_event_log(window_minutes=int(window_minutes), max_events=500)
        errors = [e for e in events if e.get("level") == "Error"]
        if len(errors) >= int(threshold):
            return {
                "ok": False,
                "detail": {
                    "issue": "event_log_storm",
                    "destructive": False,
                    "errors": len(errors),
                    "window_minutes": int(window_minutes),
                    "samples": [
                        f"{e['time']} {e['source']} {e['message'][:120]}"
                        for e in errors[:5]
                    ],
                },
            }
        return {
            "ok": True,
            "detail": {"issue": "event_log_storm", "errors": len(errors)},
        }
    except Exception as exc:
        return {
            "ok": True,
            "detail": {
                "issue": "event_log_storm",
                "available": False,
                "error": str(exc),
            },
        }


_SYSTEM_PROBES = {
    "disk_space": check_disk_space,
    "memory_pressure": check_memory_pressure,
    "runaway_process": check_runaway_processes,
    "temp_growth": check_temp_growth,
    "event_log_storm": check_event_log_errors,
}

# TTL cache: whole-machine probes sample every process (psutil is O(n) on
# Windows, ~10-16s for a few hundred processes). The command center polls the
# overview every 10s; re-running the full probe set per poll would stall the
# API. Cache results for `_PROBE_CACHE_TTL` seconds and only re-scan after.
_PROBE_CACHE_TTL = 30.0
_probe_cache: Dict[str, Any] = {"ts": 0.0, "results": {}}
import threading

_probe_cache_lock = threading.Lock()


def run_system_probes(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """Run all whole-computer probes. Returns {probe_name: result}.
    Results are cached for _PROBE_CACHE_TTL seconds (see module note above).
    The freshness check, probe execution, and cache write all happen under ONE
    lock hold so concurrent callers cannot stampede — a waiter blocks on the
    lock, then reads the fresh cache written by the first caller and returns
    immediately instead of re-running the full 10-16s probe set."""
    import time as _time

    with _probe_cache_lock:
        now = _time.time()
        if (
            not force
            and _probe_cache.get("results")
            and (now - _probe_cache["ts"]) < _PROBE_CACHE_TTL
        ):
            return dict(_probe_cache["results"])
        results = {}
        for name, fn in _SYSTEM_PROBES.items():
            try:
                results[name] = fn()
            except Exception as exc:
                results[name] = {
                    "ok": True,
                    "detail": {"issue": name, "available": False, "error": str(exc)},
                }
        _probe_cache["ts"] = _time.time()
        _probe_cache["results"] = dict(results)
        return results
