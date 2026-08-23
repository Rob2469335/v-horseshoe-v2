import time
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, List

import psutil

log = logging.getLogger("zenith_healing")
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

ALLOWED_SERVICES = {"llamacpp", "backend", "qdrant", "Qdrant"}


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


def kill_process_by_port(port: int) -> Dict[str, Any]:
    """Kill any process listening on the specified network port (skips self)."""
    try:
        port = int(port)
    except (ValueError, TypeError):
        return {"ok": False, "error": f"Invalid port: {port}"}

    my_pid = psutil.Process().pid
    killed = []

    for conn in psutil.net_connections(kind="inet"):
        try:
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN and conn.pid and conn.pid != my_pid:
                proc = psutil.Process(conn.pid)
                pname = (proc.name() or "").lower()
                if any(nt in pname for nt in _NEVER_TOUCH):
                    continue
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except psutil.TimeoutExpired:
                    proc.kill()
                killed.append({"pid": conn.pid, "name": proc.name(), "port": port})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed:
        return {"ok": True, "action": "kill_process_by_port", "killed": killed}
    return {"ok": False, "action": "kill_process_by_port", "error": f"No process found listening on port {port}"}


def kill_process_by_name(pattern: str) -> Dict[str, Any]:
    """Kill processes whose executable name or cmdline matches pattern."""
    if not pattern or len(pattern.strip()) < 3:
        return {"ok": False, "error": "Pattern too short or empty"}

    pattern = pattern.strip().lower()
    if any(nt in pattern or pattern in nt for nt in _NEVER_TOUCH):
        return {"ok": False, "error": f"Pattern '{pattern}' targets protected system process"}

    my_pid = psutil.Process().pid
    killed = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = proc.info["pid"]
            if pid == my_pid:
                continue
            name = (proc.info["name"] or "").lower()
            if any(nt in name for nt in _NEVER_TOUCH):
                continue
            cmdline = " ".join(proc.info["cmdline"] or []).lower()
            if pattern in name or pattern in cmdline:
                p = psutil.Process(pid)
                p.terminate()
                try:
                    p.wait(timeout=3.0)
                except psutil.TimeoutExpired:
                    p.kill()
                killed.append({"pid": pid, "name": proc.info["name"]})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed:
        return {"ok": True, "action": "kill_process_by_name", "killed": killed}
    return {"ok": False, "action": "kill_process_by_name", "error": f"No processes matching '{pattern}' found"}


def clean_directory(target_dir: str, extensions: List[str] = None, max_age_hours: int = 24) -> Dict[str, Any]:
    """Clean stale temporary or cache files within the project boundary."""
    try:
        target = (PROJECT_ROOT / target_dir).resolve()
        if not str(target).startswith(str(PROJECT_ROOT)):
            return {"ok": False, "error": f"Path '{target_dir}' escapes project root"}
        if not target.exists() or not target.is_dir():
            return {"ok": False, "error": f"Directory '{target_dir}' does not exist"}

        cutoff = time.time() - (max_age_hours * 3600)
        ext_set = set()
        if extensions:
            for e in extensions:
                ext_set.add(e.lower() if e.startswith(".") else f".{e.lower()}")

        removed = []
        for f in target.rglob("*"):
            if f.is_file():
                if ext_set and f.suffix.lower() not in ext_set:
                    continue
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                        removed.append(str(f.relative_to(PROJECT_ROOT)))
                except Exception as ex:
                    log.warning(f"Could not remove {f}: {ex}")

        return {"ok": True, "action": "clean_directory", "removed_count": len(removed), "removed": removed[:20]}
    except Exception as exc:
        return {"ok": False, "action": "clean_directory", "error": str(exc)}


def restart_service(service_name: str) -> Dict[str, Any]:
    """Restart a bounded, pre-approved internal service or daemon."""
    if service_name not in ALLOWED_SERVICES:
        return {"ok": False, "action": "restart_service", "error": f"'{service_name}' not in allowed service list: {sorted(ALLOWED_SERVICES)}"}

    s_lower = service_name.lower()
    if s_lower == "llamacpp":
        from swarm_os.healing.recovery_engine import restart_llamacpp
        return restart_llamacpp({"service": "llamacpp"})
    elif s_lower == "backend":
        from swarm_os.healing.recovery_engine import restart_backend
        return restart_backend({"service": "backend"})
    elif s_lower == "qdrant":
        try:
            subprocess.run(["net", "stop", "Qdrant"], capture_output=True, timeout=15, text=True)
            res_start = subprocess.run(["net", "start", "Qdrant"], capture_output=True, timeout=15, text=True)
            ok = res_start.returncode == 0
            return {"ok": ok, "action": "restart_service", "service": "Qdrant", "output": res_start.stdout}
        except Exception as e:
            return {"ok": False, "action": "restart_service", "service": "Qdrant", "error": str(e)}

    return {"ok": False, "action": "restart_service", "error": f"Unhandled service: {service_name}"}


RECOVERY_PRIMITIVES = {
    "kill_process_by_port": kill_process_by_port,
    "kill_process_by_name": kill_process_by_name,
    "clean_directory": clean_directory,
    "restart_service": restart_service,
}
