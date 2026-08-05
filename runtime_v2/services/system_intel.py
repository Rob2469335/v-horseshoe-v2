"""Read-only system intelligence tools — the swarm's whole-computer command center.

Provides analysis-only access to the host machine: hardware inventory, running
processes/services, network connections, disk usage, installed applications,
startup items, Windows Event Log, and read-only registry queries. No writes, no
process termination, no destructive operations — callers route these through
`asyncio.to_thread` because psutil/winreg are blocking.
"""
from __future__ import annotations

import logging
import os
import platform
import socket
from typing import Any, Dict

import psutil

log = logging.getLogger(__name__)


def _ok(result: Any) -> Dict[str, Any]:
    return {"ok": True, "result": result}


def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": str(message)}


# ---------------------------------------------------------------------------
# Hardware & OS inventory
# ---------------------------------------------------------------------------

def system_inventory() -> Dict[str, Any]:
    try:
        boot = psutil.boot_time()
        vmem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        cpu_freq = psutil.cpu_freq()
        info: Dict[str, Any] = {
            "hostname": socket.gethostname(),
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "uptime_seconds": round(max(0, __import__("time").time() - boot), 1),
            "boot_time_iso": __import__("datetime").datetime.fromtimestamp(boot).isoformat(),
            "cpu_physical_cores": psutil.cpu_count(logical=False),
            "cpu_logical_cores": psutil.cpu_count(logical=True),
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "cpu_freq_mhz": round(cpu_freq.current, 1) if cpu_freq else None,
            "ram_total_gb": round(vmem.total / (1024**3), 2),
            "ram_used_gb": round(vmem.used / (1024**3), 2),
            "ram_percent": vmem.percent,
            "swap_total_gb": round(swap.total / (1024**3), 2),
            "swap_percent": swap.percent,
            "disks": [],
            "network_interfaces": [],
        }
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                info["disks"].append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024**3), 2),
                    "used_gb": round(usage.used / (1024**3), 2),
                    "free_gb": round(usage.free / (1024**3), 2),
                    "percent": usage.percent,
                })
            except Exception:
                continue
        addrs = psutil.net_if_addrs()
        io_counters = psutil.net_io_counters(pernic=True)
        for name, snics in addrs.items():
            ipv4 = [a.address for a in snics if a.family == socket.AF_INET]
            if not ipv4:
                continue
            entry: Dict[str, Any] = {"name": name, "ipv4": ipv4}
            counter = io_counters.get(name)
            if counter:
                entry.update({
                    "bytes_sent": counter.bytes_sent,
                    "bytes_recv": counter.bytes_recv,
                })
            info["network_interfaces"].append(entry)
        return _ok(info)
    except Exception as exc:
        return _err(exc)


def _process_entry(proc: psutil.Process) -> Dict[str, Any]:
    try:
        mem = proc.memory_info()
        return {
            "pid": proc.pid,
            "name": proc.name(),
            "status": proc.status(),
            "cpu_percent": round(proc.cpu_percent(interval=0.05), 1),
            "memory_mb": round(mem.rss / (1024**2), 1) if mem else 0,
            "username": proc.username(),
            "exe": proc.exe(),
            "cmdline": " ".join(proc.cmdline())[:300],
            "started_iso": __import__("datetime").datetime.fromtimestamp(proc.create_time()).isoformat() if proc.create_time() else None,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def process_list(sort: str = "memory", top: int = 40) -> Dict[str, Any]:
    try:
        sort = str(sort or "memory").lower().strip()
        entries = [_process_entry(p) for p in psutil.process_iter()]
        entries = [e for e in entries if e]
        key = "memory_mb" if "mem" in sort else ("cpu_percent" if "cpu" in sort else ("name" if "name" in sort else "pid"))
        reverse = key not in ("name", "pid")
        entries.sort(key=lambda e: e.get(key, 0), reverse=reverse)
        return _ok({"count": len(entries), "sort": key, "processes": entries[: max(1, int(top))]})
    except Exception as exc:
        return _err(exc)


def service_list() -> Dict[str, Any]:
    if not hasattr(psutil, "win_service_iter"):
        return _err("Windows services API not available on this platform")
    services = []
    try:
        for svc in psutil.win_service_iter():
            try:
                info = svc.as_dict()
                services.append({
                    "name": info.get("name"),
                    "display_name": info.get("display_name"),
                    "status": info.get("status"),
                    "start_type": info.get("start_type"),
                    "binpath": info.get("binpath", "")[:250],
                    "pid": info.get("pid"),
                })
            except Exception:
                continue
        services.sort(key=lambda s: str(s.get("name", "")).lower())
        return _ok({"count": len(services), "services": services})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def net_connections() -> Dict[str, Any]:
    try:
        conns = []
        for c in psutil.net_connections(kind="all"):
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
            proc = None
            if c.pid:
                try:
                    proc = psutil.Process(c.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    proc = f"pid:{c.pid}"
            conns.append({
                "fd": c.fd,
                "family": "IPv4" if c.family == socket.AF_INET else ("IPv6" if c.family == socket.AF_INET6 else str(c.family)),
                "type": "TCP" if c.type == socket.SOCK_STREAM else ("UDP" if c.type == socket.SOCK_DGRAM else str(c.type)),
                "local": laddr,
                "remote": raddr,
                "status": c.status,
                "process": proc,
            })
        return _ok({"count": len(conns), "connections": conns})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Disk analysis
# ---------------------------------------------------------------------------

def _dir_size(path: str, max_depth: int = 2, top: int = 20) -> Dict[str, Any]:
    """Walk a directory tree and return largest subdirectories/files by size."""
    from collections import defaultdict
    from pathlib import Path as _Path
    root = _Path(path).resolve()
    if not root.is_dir():
        return _err(f"Not a directory: {path}")
    dir_sizes: Dict[str, int] = defaultdict(int)
    file_sizes: list[tuple[int, str]] = []
    banned = {".git", ".venv", "__pycache__", ".ruff_cache", "node_modules", ".swarm_brain", "models", "logs", "data", ".pytest_cache", ".mypy_cache"}
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = _Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in banned]
        for fname in filenames:
            try:
                fp = _Path(dirpath) / fname
                size = fp.stat().st_size
            except OSError:
                continue
            total += size
            rel_path = fp.relative_to(root)
            parts = rel_path.parts
            acc = ""
            for p in parts[:-1]:
                acc = os.path.join(acc, p)
                dir_sizes[acc] = dir_sizes.get(acc, 0) + size
            if len(file_sizes) < top * 4:
                file_sizes.append((size, str(rel_path).replace(os.sep, "/")))
    file_sizes.sort(reverse=True)
    dir_entries = sorted(dir_sizes.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return _ok({
        "root": str(root),
        "total_bytes": total,
        "total_gb": round(total / (1024**3), 3),
        "largest_dirs": [{"path": d.replace(os.sep, "/"), "bytes": s, "gb": round(s / (1024**3), 3)} for d, s in dir_entries],
        "largest_files": [{"path": fp, "bytes": s, "gb": round(s / (1024**3), 3)} for s, fp in file_sizes[:top]],
    })


def disk_analyzer(path: str = ".", max_depth: int = 2, top: int = 20) -> Dict[str, Any]:
    return _dir_size(str(path), max_depth=int(max_depth or 2), top=int(top or 20))


# ---------------------------------------------------------------------------
# Installed applications & startup items (Windows registry, read-only)
# ---------------------------------------------------------------------------

_UNINSTALL_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
)

_STARTUP_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
)


def _open_key(subkey: str, hive: str):
    import winreg
    hive_map = {
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKCR": winreg.HKEY_CLASSES_ROOT,
    }
    return winreg.OpenKey(hive_map.get(hive.upper(), winreg.HKEY_LOCAL_MACHINE), subkey)


def _iter_registry_values(subkey: str, hive: str) -> list[tuple[str, Any]]:
    import winreg
    values = []
    try:
        key = _open_key(subkey, hive)
    except OSError:
        return values
    try:
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                values.append((str(name), value))
            except OSError:
                break
            i += 1
    finally:
        winreg.CloseKey(key)
    return values


def installed_apps() -> Dict[str, Any]:
    apps = {}
    # Each Uninstall subkey is a per-app key; iterate them
    for subkey, hive in _UNINSTALL_KEYS:
        import winreg
        try:
            parent = _open_key(subkey, hive)
        except OSError:
            continue
        try:
            idx = 0
            while True:
                try:
                    app_key_name = winreg.EnumKey(parent, idx)
                except OSError:
                    break
                idx += 1
                app_path = rf"{subkey}\{app_key_name}"
                vals = dict(_iter_registry_values(app_path, hive))
                display = vals.get("DisplayName")
                if not display or not isinstance(display, str):
                    continue
                apps[app_key_name.lower()] = {
                    "name": display,
                    "version": vals.get("DisplayVersion"),
                    "publisher": vals.get("Publisher"),
                    "install_date": vals.get("InstallDate"),
                    "install_location": vals.get("InstallLocation", ""),
                    "uninstall_string": (vals.get("UninstallString") or "")[:250],
                    "estimated_size_mb": (int(vals["EstimatedSize"]) // 1024) if isinstance(vals.get("EstimatedSize"), int) else None,
                    "hive": hive,
                }
        finally:
            winreg.CloseKey(parent)
    entries = sorted(apps.values(), key=lambda a: str(a.get("name", "")).lower())
    return _ok({"count": len(entries), "apps": entries})


def startup_items() -> Dict[str, Any]:
    items = []
    for subkey, hive in _STARTUP_KEYS:
        for name, value in _iter_registry_values(subkey, hive):
            if not isinstance(value, str):
                continue
            items.append({
                "name": name,
                "command": value[:300],
                "registry_key": subkey,
                "hive": hive,
            })
    items.sort(key=lambda i: str(i.get("name", "")).lower())
    return _ok({"count": len(items), "items": items})


def registry_query(subkey: str, hive: str = "HKLM") -> Dict[str, Any]:
    """Read-only registry enumeration of a single key's values."""
    if not subkey or not subkey.startswith("SOFTWARE") and "CurrentVersion" not in subkey:
        return _err("Only read-only registry queries under SOFTWARE are allowed.")
    try:
        values = _iter_registry_values(subkey, hive)
        if not values:
            return _ok({"subkey": subkey, "hive": hive, "count": 0, "values": []})
        return _ok({"subkey": subkey, "hive": hive, "count": len(values), "values": [{"name": n, "value": str(v)[:400]} for n, v in values]})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Windows Event Log (read-only)
# ---------------------------------------------------------------------------

def event_log_query(log: str = "System", max_events: int = 50, level: str = "") -> Dict[str, Any]:
    """Tail the Windows Event Log. level: Error|Warning|Information (optional filter)."""

    log_name = str(log or "System")
    try:
        import win32evtlog
    except ImportError:
        return _err("pywin32 not installed — cannot read the Event Log.")
    try:
        handle = win32evtlog.OpenEventLog(None, log_name)
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        events = []
        while len(events) < max_events:
            batch = win32evtlog.ReadEventLog(handle, flags, 0)
            if not batch:
                break
            for evt in batch:
                try:
                    ts = evt.TimeGenerated
                    msg = str(evt.StringInserts) if evt.StringInserts else str(evt.SourceName)
                    evt_level = {1: "Error", 2: "Warning", 4: "Information", 8: "Success", 16: "Failure"}.get(evt.EventType, "Unknown")
                    if level and evt_level.lower() != str(level).lower():
                        continue
                    events.append({
                        "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "level": evt_level,
                        "event_id": evt.EventID,
                        "source": evt.SourceName,
                        "category": evt.EventCategory,
                        "message": msg[:400],
                    })
                except Exception:
                    continue
                if len(events) >= max_events:
                    break
        win32evtlog.CloseEventLog(handle)
        return _ok({"log": log_name, "count": len(events), "events": events})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_HANDLERS = {
    "system_inventory": system_inventory,
    "process_list": process_list,
    "service_list": service_list,
    "net_connections": net_connections,
    "disk_analyzer": disk_analyzer,
    "installed_apps": installed_apps,
    "startup_items": startup_items,
    "registry_query": registry_query,
    "event_log_query": event_log_query,
}


def system_handler(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route `action=<name>` (with optional args) to a read-only system tool."""
    action = str(payload.get("action", "") or "").lower().strip()
    handler = _HANDLERS.get(action)
    if not handler:
        return _err(f"Unknown system action '{action}'. Available: {', '.join(sorted(_HANDLERS))}")
    kwargs = {k: v for k, v in payload.items() if k not in ("action", "tool", "capability")}
    try:
        return handler(**kwargs)
    except TypeError as exc:
        return _err(f"Invalid arguments for '{action}': {exc}")
    except Exception as exc:
        return _err(exc)
