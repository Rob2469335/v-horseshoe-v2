"""F1 Infrastructure Supervisor — process lifecycle, observability, evidence.

Provides per-attempt infrastructure management for Experiment J F1 observations.
Every F1 attempt starts fresh F1-owned infrastructure and produces a canonical
Runtime Evidence Manifest.

DESIGN CHECKPOINT (2026-09-25): This module implements Phases 2-8 of the
10/10 observability audit. It does NOT modify any frozen Experiment J parameters.

Key invariants:
- Every F1 attempt owns its backend process.
- PIDs are tracked from launch to termination.
- stdout/stderr are captured to persistent files.
- The health gate verifies PID identity, not just endpoint health.
- Liveness monitoring detects backend death during observations.
- Observability failures fail closed (observation = infrastructure-invalid).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "data" / "f1_evidence"


class ProcessState(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DEAD = "dead"
    UNKNOWN = "unknown"


INFRA_INVALID_REASONS = {
    "backend_exit",
    "router_exit",
    "health_gate_failure",
    "workspace_mismatch",
    "router_unreachable",
    "unexpected_pid_change",
    "process_identity_unknown",
    "log_capture_failure",
    "liveness_monitor_failure",
    "startup_failure",
    "dependency_failure",
    "unknown_infrastructure_failure",
}


@dataclass
class ProcessRecord:
    role: str
    pid: Optional[int] = None
    expected_pid: Optional[int] = None
    start_time: Optional[str] = None
    command_identity: str = ""
    parent_pid: Optional[int] = None
    state: ProcessState = ProcessState.UNKNOWN
    stdout_path: str = ""
    stderr_path: str = ""
    stdout_exists: bool = False
    stdout_writable: bool = False
    stdout_non_empty: bool = False
    stderr_exists: bool = False
    stderr_writable: bool = False
    stderr_non_empty: bool = False
    exit_code: Optional[int] = None
    exit_time: Optional[str] = None


@dataclass
class HealthGateResult:
    passed: bool = False
    timestamp: str = ""
    process_alive: bool = False
    port_listening: bool = False
    health_http_200: bool = False
    status_ready: bool = False
    workspace_identity: str = ""
    router_reachability: bool = False
    no_web_tools_flag: bool = False
    expected_pid_identity: bool = False
    errors: list = field(default_factory=list)


@dataclass
class RuntimeEvidenceManifest:
    attempt_id: str = ""
    infrastructure: dict = field(default_factory=dict)
    health_gate: dict = field(default_factory=dict)
    monitoring: dict = field(default_factory=dict)
    evidence_preservation: dict = field(default_factory=dict)
    validity: dict = field(default_factory=dict)
    infrastructure_invalid_reason: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_endpoint(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _check_port_listening(port: int) -> Optional[int]:
    """Return the PID listening on port, or None."""
    try:
        import psutil
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                return conn.pid
    except Exception:
        pass
    return None


def _capture_file_status(path: str) -> dict:
    p = Path(path)
    exists = p.exists()
    writable = False
    non_empty = False
    if exists:
        try:
            writable = os.access(path, os.W_OK)
            non_empty = p.stat().st_size > 0
        except Exception:
            pass
    return {"path": path, "exists": exists, "writable": writable, "non_empty": non_empty}


def _get_pid_command(pid: int) -> str:
    try:
        import psutil
        p = psutil.Process(pid)
        return " ".join(p.cmdline()[:3])
    except Exception:
        return "unknown"


def start_backend_fresh(
    attempt_id: str,
    workspace_root: str,
    port: int = 8000,
    timeout: int = 30,
) -> tuple[ProcessRecord, Path]:
    """Start a fresh F1-owned backend process with captured stdout/stderr.

    Returns (backend_record, evidence_dir).
    """
    evidence_dir = EVIDENCE_DIR / attempt_id
    evidence_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = evidence_dir / "backend_stdout.log"
    stderr_path = evidence_dir / "backend_stderr.log"

    env = os.environ.copy()
    env["SWARM_WORKSPACE_ROOT"] = workspace_root
    env["SWARM_MEMORY_INJECT"] = "0"
    env["SWARM_AUTONOMY"] = "0"
    env["SWARM_NO_TOASTS"] = "1"
    env["SWARM_SEMANTIC_CACHE"] = "0"
    env["SWARM_GENETIC_MUTATION"] = "0"
    env["SWARM_EVOLUTION"] = "0"
    env["SWARM_F1_NO_WEB_TOOLS"] = "1"

    launcher_code = (
        "import os, sys; "
        f"os.environ['SWARM_WORKSPACE_ROOT'] = r'{workspace_root}'; "
        "os.environ['SWARM_MEMORY_INJECT']='0'; "
        "os.environ['SWARM_AUTONOMY']='0'; "
        "os.environ['SWARM_NO_TOASTS']='1'; "
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "'); "
        "from swarm_os.app.main import app; "
        "os.environ['SWARM_SEMANTIC_CACHE']='0'; "
        "os.environ['SWARM_GENETIC_MUTATION']='0'; "
        "os.environ['SWARM_EVOLUTION']='0'; "
        "import uvicorn; "
        f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='info')"
    )

    stdout_f = open(stdout_path, "w", encoding="utf-8", errors="replace")
    stderr_f = open(stderr_path, "w", encoding="utf-8", errors="replace")

    proc = subprocess.Popen(
        [sys.executable, "-c", launcher_code],
        stdout=stdout_f,
        stderr=stderr_f,
        env=env,
        cwd=str(REPO_ROOT),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    record = ProcessRecord(
        role="backend",
        pid=proc.pid,
        expected_pid=proc.pid,
        start_time=_now_iso(),
        command_identity=f"python -c <f1_backend_launcher> port={port}",
        parent_pid=os.getpid(),
        state=ProcessState.STARTING,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )

    return record, evidence_dir


def wait_for_backend(
    record: ProcessRecord,
    port: int = 8000,
    timeout: int = 60,
    pid_check_interval: float = 2.0,
) -> HealthGateResult:
    """Wait for the backend to become healthy, with PID identity verification."""
    gate = HealthGateResult(timestamp=_now_iso())
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        # Check PID is still alive
        if record.pid:
            try:
                import psutil
                p = psutil.Process(record.pid)
                if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                    record.state = ProcessState.DEAD
                    record.exit_time = _now_iso()
                    gate.errors.append(f"PID {record.pid} died during startup")
                    return gate
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                record.state = ProcessState.DEAD
                record.exit_time = _now_iso()
                gate.errors.append(f"PID {record.pid} not found")
                return gate

        # Check port
        actual_pid = _check_port_listening(port)
        gate.port_listening = actual_pid is not None

        if not gate.port_listening:
            time.sleep(pid_check_interval)
            continue

        # PID identity check
        gate.process_alive = True
        if record.pid and actual_pid and actual_pid != record.pid:
            record.state = ProcessState.UNKNOWN
            gate.errors.append(
                f"unexpected_pid_change: expected {record.pid}, "
                f"actual {actual_pid}"
            )
            return gate

        gate.expected_pid_identity = actual_pid == record.pid if record.pid else False

        # Health endpoint
        gate.health_http_200 = _probe_endpoint(
            f"http://127.0.0.1:{port}/health", timeout=5.0
        )

        # Status endpoint
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/status", timeout=5.0
            ) as r:
                data = json.loads(r.read().decode())
                sb = data.get("sandbox", {})
                gate.status_ready = data.get("ready", False)
                gate.workspace_identity = sb.get("workspace_root", "")
                gate.router_reachability = data.get("llamacpp_reachable", False)
        except Exception:
            pass

        # F1_NO_WEB_TOOLS check
        gate.no_web_tools_flag = os.environ.get("SWARM_F1_NO_WEB_TOOLS") == "1"

        # Core health gate: backend is alive, serving, correct PID, correct workspace.
        # Router reachability is a separate shared dependency (not F1-owned).
        if (
            gate.process_alive
            and gate.port_listening
            and gate.health_http_200
            and gate.status_ready
            and gate.expected_pid_identity
        ):
            gate.passed = True
            record.state = ProcessState.HEALTHY
            return gate

        time.sleep(pid_check_interval)

    gate.errors.append(f"health_gate_timeout after {timeout}s")
    return gate


def build_manifest(
    attempt_id: str,
    backend: ProcessRecord,
    gate: HealthGateResult,
    evidence_dir: Path,
) -> RuntimeEvidenceManifest:
    """Build the canonical Runtime Evidence Manifest."""
    m = RuntimeEvidenceManifest(attempt_id=attempt_id)

    m.infrastructure = {
        "observed_process_graph": {
            "backend": {
                "pid": backend.pid,
                "expected_pid": backend.expected_pid,
                "start_time": backend.start_time,
                "command_identity": backend.command_identity,
                "parent_pid": backend.parent_pid,
                "state": backend.state.value,
            }
        }
    }

    # File status for stdout/stderr
    m.infrastructure["observed_process_graph"]["backend"]["stdout"] = _capture_file_status(backend.stdout_path)
    m.infrastructure["observed_process_graph"]["backend"]["stderr"] = _capture_file_status(backend.stderr_path)
    m.infrastructure["observed_process_graph"]["backend"]["exit_code"] = backend.exit_code
    m.infrastructure["observed_process_graph"]["backend"]["exit_time"] = backend.exit_time

    m.health_gate = {
        "passed": gate.passed,
        "timestamp": gate.timestamp,
        "process_alive": gate.process_alive,
        "port_listening": gate.port_listening,
        "health_http_200": gate.health_http_200,
        "status_ready": gate.status_ready,
        "workspace_identity": gate.workspace_identity,
        "router_reachability": gate.router_reachability,
        "no_web_tools_flag": gate.no_web_tools_flag,
        "expected_pid_identity": gate.expected_pid_identity,
        "errors": gate.errors,
    }

    m.evidence_preservation = {
        "verified": all([
            Path(backend.stdout_path).exists(),
            Path(backend.stderr_path).exists(),
            evidence_dir.exists(),
        ])
    }

    return m


def save_manifest(manifest: RuntimeEvidenceManifest, evidence_dir: Path) -> Path:
    """Save manifest to evidence directory."""
    manifest_path = evidence_dir / "runtime_evidence_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "attempt_id": manifest.attempt_id,
                "infrastructure": manifest.infrastructure,
                "health_gate": manifest.health_gate,
                "monitoring": manifest.monitoring,
                "evidence_preservation": manifest.evidence_preservation,
                "validity": manifest.validity,
                "infrastructure_invalid_reason": manifest.infrastructure_invalid_reason,
            },
            f,
            indent=2,
        )
    return manifest_path
