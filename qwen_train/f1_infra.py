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

Evidence-First Architecture (2026-09-25):
- RAW OBSERVATION and DERIVED INTERPRETATION are structurally separated.
- Capability credit is machine-enforced: invalidity → zero credit → UNKNOWN.
- Every derived artifact is traceable to immutable evidence.
- UNKNOWN is a valid scientific result; it must never silently become FAIL.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "data" / "f1_evidence"


# ---------------------------------------------------------------------------
# Process State Model (Phase 6)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Evidence Schema — evidence identity (Priority 4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceIdentity:
    """Identity for an F1 observation invocation.

    invocation_id: unique per execution (timestamp+PID ensure uniqueness).
    content_hash: deterministic hash of the raw observation fields — this IS
    content-addressed and can be used to verify the observation was not mutated.
    """
    invocation_id: str
    attempt_id: str
    created_at: str
    content_hash: str = ""

    @staticmethod
    def create(attempt_id: str, invocation_id: str = "") -> "EvidenceIdentity":
        ts = datetime.now(timezone.utc).isoformat()
        if not invocation_id:
            raw = f"{attempt_id}:{ts}:{os.getpid()}"
            invocation_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return EvidenceIdentity(
            invocation_id=invocation_id, attempt_id=attempt_id, created_at=ts,
        )

    @staticmethod
    def from_observation(attempt_id: str, obs_fields: dict,
                         invocation_id: str = "") -> "EvidenceIdentity":
        """Create identity with content-addressed hash of observation fields."""
        ts = datetime.now(timezone.utc).isoformat()
        if not invocation_id:
            raw = f"{attempt_id}:{ts}:{os.getpid()}"
            invocation_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        # Content hash: deterministic from observation fields only
        canonical = json.dumps(obs_fields, sort_keys=True, default=str)
        content_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return EvidenceIdentity(
            invocation_id=invocation_id, attempt_id=attempt_id,
            created_at=ts, content_hash=content_hash,
        )


# ---------------------------------------------------------------------------
# Provenance (Priority 4)
# ---------------------------------------------------------------------------

@dataclass
class Provenance:
    """Truthful lineage: what evidence produced this manifest.

    source_evidence: file paths or records that fed the manifest
    attempt_id: the F1 attempt this evidence belongs to
    parent_invocation_id: set when this manifest derives from a prior one
    tooling_version: which version of the read-before-write guard was active
    """
    source_evidence: list = field(default_factory=list)
    attempt_id: str = ""
    experiment_id: str = "experiment_j_f1"
    parent_invocation_id: Optional[str] = None
    tooling_version: str = ""
    generator: str = "f1_infra"
    generator_version: str = "1.0.0"


# ---------------------------------------------------------------------------
# F1 Pilot Endpoint Constants (frozen per F1-OP-002 / F1-OP-003 / F1-OP-004a)
# ---------------------------------------------------------------------------

F1_QUALIFYING_OPERATIONS = frozenset({"write", "patch", "edit", "create"})
F1_RELEVANT_FILE_SET = frozenset({"swarm_os/lib/paths.py"})
F1_HORIZON_STEPS = 12


def find_qualifying_first_edit(
    tool_calls: list,
    relevant_file_set: Optional[frozenset] = None,
    horizon_steps: Optional[int] = None,
) -> Optional[int]:
    """Scan ATIF tool-call records in ascending step order for the first
    qualifying edit: an edit-type filesystem action whose target resolves
    to a file in ``relevant_file_set``.

    A qualifying edit is an action (write/patch/edit/create) targeting
    the relevant file. The patch does NOT need to be accepted or the
    file mutated - the action itself is the endpoint per F1-OP-004a.

    R11: the relevant file set and the horizon are PARAMETERS. Omitting them
    preserves the F1 pilot behaviour byte-identically (F1_RELEVANT_FILE_SET /
    F1_HORIZON_STEPS); a confirmatory F2 task MUST pass its own frozen set,
    because the F1 constant names exactly one file in one repository and would
    silently score every other task as "no qualifying edit". Prefer
    :func:`qwen_train.f2_endpoint.qualifying_first_edit`, which additionally
    binds the set to a SHA-256 and refuses treatment-derived provenance.

    Returns the ATIF step_id of the first qualifying edit, or None.
    """
    relevant = (
        F1_RELEVANT_FILE_SET
        if relevant_file_set is None
        else frozenset(relevant_file_set)
    )
    horizon = F1_HORIZON_STEPS if horizon_steps is None else int(horizon_steps)
    if not tool_calls:
        return None
    # Filter to only dict entries (ATIF step records), skip string tool names
    dict_tcs = [tc for tc in tool_calls if isinstance(tc, dict)]
    if not dict_tcs:
        return None
    # Sort by step_id (ATIF trajectory step) to ensure ascending order
    sorted_tcs = sorted(dict_tcs, key=lambda tc: tc.get("extra", {}).get("step_id", tc.get("extra", {}).get("turn", 0)))
    for tc in sorted_tcs:
        fn = tc.get("function_name", "")
        if fn != "filesystem":
            continue
        args = tc.get("arguments", {})
        operation = args.get("operation", "")
        if operation not in F1_QUALIFYING_OPERATIONS:
            continue
        target = args.get("path", "") or args.get("file_path", "")
        # Normalize: strip repo prefix if present, compare basename
        target_normalized = target.replace("\\", "/")
        for rel_path in relevant:
            if target_normalized.endswith(rel_path):
                extra = tc.get("extra", {})
                # Prefer step_id (ATIF step ordinal) over turn (agent loop iteration)
                step_id = extra.get("step_id") or extra.get("turn")
                if step_id is not None:
                    step_id = int(step_id)
                    # Respect the frozen horizon: an endpoint beyond it is not an
                    # endpoint, and no later step can become one.
                    if step_id > horizon:
                        return None
                    return step_id
    return None


# ---------------------------------------------------------------------------
# Raw Observation (Priority 3)
# ---------------------------------------------------------------------------

@dataclass
class RawObservation:
    """Immutable runtime facts. Never overwritten by interpretation."""
    backend_pid: Optional[int] = None
    expected_pid: Optional[int] = None
    start_time: str = ""
    end_time: str = ""
    port_listening: bool = False
    health_http_200: bool = False
    status_ready: bool = False
    workspace_identity: str = ""
    router_reachability: bool = False
    pid_alive: bool = False
    process_state: str = "unknown"
    stdout_path: str = ""
    stderr_path: str = ""
    exit_code: Optional[int] = None
    tool_calls: list = field(default_factory=list)
    timed_out: bool = False
    cli_ok: bool = False
    elapsed_s: float = 0.0
    infra_invalid_reason: str = ""
    monitor_events: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Derived Classification / Interpretation (Priority 3)
# ---------------------------------------------------------------------------

@dataclass
class DerivedClassification:
    """Machine-enforced classification derived from RawObservation."""
    validity_infrastructure: str = "UNKNOWN"
    capability_credit_positive: int = 0
    capability_credit_negative: int = 0
    capability_repair: str = "UNKNOWN"
    capability_tool_selection: str = "UNKNOWN"
    capability_debugging: str = "UNKNOWN"
    decision_boundary_reached: bool = False
    f1_endpoint_step: Optional[int] = None  # ATIF step of qualifying first edit, or None
    classification_version: str = "1.0"
    classified_at: str = ""

    def to_dict(self) -> dict:
        return {
            "validity_infrastructure": self.validity_infrastructure,
            "capability_credit": {
                "positive": self.capability_credit_positive,
                "negative": self.capability_credit_negative,
            },
            "capability_repair": self.capability_repair,
            "capability_tool_selection": self.capability_tool_selection,
            "capability_debugging": self.capability_debugging,
            "decision_boundary_reached": self.decision_boundary_reached,
            "f1_endpoint_step": self.f1_endpoint_step,
            "classification_version": self.classification_version,
            "classified_at": self.classified_at,
        }


# ---------------------------------------------------------------------------
# Scientific Rules — machine-enforced (Priority 5)
# ---------------------------------------------------------------------------

def classify_observation(obs: RawObservation) -> DerivedClassification:
    """Machine-enforced scientific rules. Rules A-G + F1 endpoint detection.

    RULE A: infrastructure_invalid → capability UNKNOWN
    RULE B: infrastructure_invalid → positive credit = 0
    RULE C: infrastructure_invalid → negative credit = 0
    RULE D: invalid → cannot count against model
    RULE E: capability credit requires reaching decision boundary
    RULE F: boundary not reached → UNKNOWN
    RULE G: UNKNOWN must remain explicit, never silently FAIL

    F1 ENDPOINT: A qualifying first edit (edit-type filesystem action on
    relevant_file_set) within ATIF steps 1-12 is an observed endpoint
    regardless of timeout, infrastructure validity, or patch acceptance.
    """
    cls = DerivedClassification(classified_at=_now_iso())

    is_infra_invalid = bool(obs.infra_invalid_reason)

    # F1 ENDPOINT DETECTION: scan trajectory for qualifying edit
    # Must occur BEFORE timeout classification per F1-OP-004a.
    endpoint_step = find_qualifying_first_edit(obs.tool_calls or [])
    cls.f1_endpoint_step = endpoint_step

    # RULE A + B + C: infrastructure-invalid → zero credit, UNKNOWN capability
    if is_infra_invalid:
        cls.validity_infrastructure = "INVALID"
        cls.capability_credit_positive = 0
        cls.capability_credit_negative = 0
        cls.capability_repair = "UNKNOWN"
        cls.capability_tool_selection = "UNKNOWN"
        cls.capability_debugging = "UNKNOWN"
        cls.decision_boundary_reached = endpoint_step is not None
        return cls

    # RULE D: invalid run cannot count as failure (if invalid for other reasons)
    if not obs.cli_ok and obs.timed_out:
        cls.validity_infrastructure = "INVALID"
        cls.capability_credit_positive = 0
        cls.capability_credit_negative = 0
        cls.capability_repair = "UNKNOWN"
        cls.capability_tool_selection = "UNKNOWN"
        cls.capability_debugging = "UNKNOWN"
        cls.decision_boundary_reached = endpoint_step is not None
        return cls

    # RULE E + F: no boundary reached → UNKNOWN
    boundary_reached = obs.cli_ok and not obs.timed_out
    if not boundary_reached:
        cls.validity_infrastructure = "VALID"
        cls.capability_credit_positive = 0
        cls.capability_credit_negative = 0
        cls.capability_repair = "UNKNOWN"
        cls.capability_tool_selection = "UNKNOWN"
        cls.capability_debugging = "UNKNOWN"
        cls.decision_boundary_reached = endpoint_step is not None
        return cls

    # Valid observation that reached boundary — capability remains UNKNOWN
    # until evaluator evidence is available. No automatic positive credit.
    cls.validity_infrastructure = "VALID"
    cls.decision_boundary_reached = True
    cls.capability_credit_positive = 0
    cls.capability_credit_negative = 0
    cls.capability_repair = "UNKNOWN"
    cls.capability_tool_selection = "UNKNOWN"
    cls.capability_debugging = "UNKNOWN"
    return cls


# ---------------------------------------------------------------------------
# Process records & health gate (existing, extended)
# ---------------------------------------------------------------------------

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
    workspace_match: bool = False
    expected_workspace_root: str = ""
    router_reachability: bool = False
    no_web_tools_flag: bool = False
    expected_pid_identity: bool = False
    errors: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# RuntimeEvidenceManifest (existing, extended with evidence schema)
# ---------------------------------------------------------------------------

@dataclass
class RuntimeEvidenceManifest:
    evidence_identity: Optional[EvidenceIdentity] = None
    provenance: Optional[Provenance] = None
    attempt_id: str = ""
    infrastructure: dict = field(default_factory=dict)
    health_gate: dict = field(default_factory=dict)
    monitoring: dict = field(default_factory=dict)
    evidence_preservation: dict = field(default_factory=dict)
    validity: dict = field(default_factory=dict)
    observation: dict = field(default_factory=dict)
    interpretation: dict = field(default_factory=dict)
    infrastructure_invalid_reason: str = ""


# ---------------------------------------------------------------------------
# Runtime Monitor (Priority 1)
# ---------------------------------------------------------------------------

class RuntimeMonitor:
    """Continuous liveness monitor for F1-owned processes during observation.

    Monitors backend PID/port AND router port reachability.
    Detects death, PID changes, port disappearance, and router unreachable.
    Does NOT auto-restart. Records raw evidence of state transitions.
    """

    def __init__(self, backend: ProcessRecord, port: int = 8000,
                 check_interval: float = 5.0, router_port: Optional[int] = None):
        self._backend = backend
        self._port = port
        self._router_port = router_port
        self._interval = check_interval
        self._events: list = []
        self._alive = True
        self._router_alive = True
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def router_alive(self) -> bool:
        return self._router_alive

    @property
    def events(self) -> list:
        return list(self._events)

    def _record_event(self, event_type: str, detail: str) -> None:
        self._events.append({
            "timestamp": _now_iso(),
            "type": event_type,
            "detail": detail,
            "backend_pid": self._backend.pid,
        })

    def _check_once(self) -> bool:
        """Single liveness check. Returns False if backend is dead."""
        pid = self._backend.pid
        if not pid:
            self._alive = False
            self._record_event("process_check_failed", "no_pid_recorded")
            return False

        try:
            import psutil
            p = psutil.Process(pid)
            if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                self._alive = False
                self._record_event("process_dead",
                                   f"PID {pid} no longer running")
                return False
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._alive = False
            self._record_event("process_dead",
                               f"PID {pid} not found")
            return False

        actual_pid = _check_port_listening(self._port)
        if actual_pid is None:
            self._alive = False
            self._record_event("port_lost",
                               f"port {self._port} no longer listening")
            # Check router port even when backend port is lost
            if self._router_port:
                router_pid = _check_port_listening(self._router_port)
                if router_pid is None:
                    self._router_alive = False
                    self._record_event("router_unreachable",
                                       f"router port {self._router_port} no longer listening")
            return False

        if actual_pid != pid:
            self._alive = False
            self._record_event("pid_change",
                               f"expected {pid}, got {actual_pid}")
            return False

        # Check router port independently (may be dead even when backend is alive)
        if self._router_port:
            router_pid = _check_port_listening(self._router_port)
            if router_pid is None:
                self._router_alive = False
                self._record_event("router_unreachable",
                                   f"router port {self._router_port} no longer listening")
            else:
                self._router_alive = True

        return True

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            if not self._check_once():
                return

    def start(self) -> None:
        self._stop.clear()
        self._alive = True
        self._router_alive = True
        self._record_event("monitor_started",
                           f"watching PID {self._backend.pid} on port {self._port}"
                           + (f" router {self._router_port}" if self._router_port else ""))
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 2)
        self._record_event("monitor_stopped",
                           f"alive={self._alive}, events={len(self._events)}")


# ---------------------------------------------------------------------------
# Existing helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_endpoint(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _norm(p: str) -> str:
    """Canonical form for comparing two workspace paths.

    Separator- and case-insensitive, so a backend reporting the same directory
    in a different spelling still matches. Deliberately NOT the
    ``tool_executor._norm`` root-relative form: the health gate compares two
    ABSOLUTE roots, so stripping a root would erase the very difference it
    must detect.
    """
    return str(p or "").replace("\\", "/").rstrip("/").lower()


def _pid_is_owned_child(candidate: Optional[int], owner: Optional[int]) -> bool:
    """True when ``candidate`` is ``owner`` or a descendant of ``owner``.

    Ownership is required before F1 may record, monitor, or terminate a PID.
    A port occupant that is merely a different live process is NOT owned.
    """
    if not candidate or not owner:
        return False
    if candidate == owner:
        return True
    try:
        import psutil
        p = psutil.Process(candidate)
        for _ in range(8):
            parent = p.parent()
            if parent is None:
                return False
            if parent.pid == owner:
                return True
            p = parent
    except Exception:
        return False
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


# ---------------------------------------------------------------------------
# Backend lifecycle
# ---------------------------------------------------------------------------

def start_backend_fresh(
    attempt_id: str,
    workspace_root: str,
    port: int = 8000,
    timeout: int = 30,
) -> tuple[ProcessRecord, Path]:
    """Start a fresh F1-owned backend process with captured stdout/stderr."""
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
    expected_workspace_root: str = "",
) -> HealthGateResult:
    """Wait for the backend to become healthy, with PID identity verification.

    ``expected_workspace_root`` is optional for backward compatibility. When
    supplied, the backend's reported sandbox root MUST match it or the gate
    fails: liveness alone cannot prove the observation ran against the arm
    workspace it will be attributed to.
    """
    gate = HealthGateResult(timestamp=_now_iso())
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
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

        actual_pid = _check_port_listening(port)
        gate.port_listening = actual_pid is not None

        if not gate.port_listening:
            time.sleep(pid_check_interval)
            continue

        gate.process_alive = True
        if actual_pid and not _pid_is_owned_child(actual_pid, record.expected_pid):
            # Ownership gate: a port occupant F1 did not launch is never adopted
            # as F1-owned, even when it is healthy. R1 EJ-R1-001 adopted a
            # pre-existing dev backend this way, recorded its PID as F1's own,
            # attributed the observation to the wrong workspace, and then
            # terminated that foreign process during cleanup.
            record.state = ProcessState.UNKNOWN
            gate.errors.append(
                f"stale_backend_not_owned: port {port} served by pid {actual_pid}, "
                f"which is not the launched pid {record.expected_pid} nor its "
                f"descendant"
            )
            return gate

        if record.pid and actual_pid and actual_pid != record.pid:
            record.state = ProcessState.UNKNOWN
            gate.errors.append(
                f"unexpected_pid_change: expected {record.pid}, "
                f"actual {actual_pid}"
            )
            return gate

        gate.expected_pid_identity = _pid_is_owned_child(actual_pid, record.expected_pid)

        gate.health_http_200 = _probe_endpoint(
            f"http://127.0.0.1:{port}/health", timeout=5.0
        )

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

        gate.no_web_tools_flag = os.environ.get("SWARM_F1_NO_WEB_TOOLS") == "1"

        # Workspace identity is a REQUIRED precondition, not an advisory field.
        # A backend whose sandbox root is anything other than this observation's
        # isolated arm clone is serving the agent a different tree than the one
        # the manifest and lesson evidence will be attributed to. R1 EJ-R1-001
        # reached this exact state (project-root workspace, zero tool calls,
        # 1200s timeout) and the liveness-only gate passed it.
        if expected_workspace_root:
            gate.expected_workspace_root = expected_workspace_root
            gate.workspace_match = _norm(gate.workspace_identity) == _norm(
                expected_workspace_root
            )
            if not gate.workspace_match:
                gate.errors.append(
                    f"workspace_mismatch: backend reports "
                    f"{gate.workspace_identity!r}, expected "
                    f"{expected_workspace_root!r}"
                )

        if (
            gate.process_alive
            and gate.port_listening
            and gate.health_http_200
            and gate.status_ready
            and gate.expected_pid_identity
            and gate.router_reachability
            and (not expected_workspace_root or gate.workspace_match)
        ):
            gate.passed = True
            record.state = ProcessState.HEALTHY
            return gate

        time.sleep(pid_check_interval)

    gate.errors.append(f"health_gate_timeout after {timeout}s")
    return gate


# ---------------------------------------------------------------------------
# Manifest building (extended with evidence schema)
# ---------------------------------------------------------------------------

def build_manifest(
    attempt_id: str,
    backend: ProcessRecord,
    gate: HealthGateResult,
    evidence_dir: Path,
    *,
    observation: Optional[RawObservation] = None,
    classification: Optional[DerivedClassification] = None,
    monitor: Optional[RuntimeMonitor] = None,
    invocation_id: str = "",
) -> RuntimeEvidenceManifest:
    """Build the canonical Runtime Evidence Manifest with truthful provenance."""

    # Build evidence identity with content hash from observation
    if observation:
        obs_fields = {
            "backend_pid": observation.backend_pid,
            "expected_pid": observation.expected_pid,
            "cli_ok": observation.cli_ok,
            "timed_out": observation.timed_out,
            "elapsed_s": observation.elapsed_s,
            "infra_invalid_reason": observation.infra_invalid_reason,
            "workspace_identity": observation.workspace_identity,
            "pid_alive": observation.pid_alive,
        }
        evidence_identity = EvidenceIdentity.from_observation(
            attempt_id, obs_fields, invocation_id=invocation_id)
    else:
        evidence_identity = EvidenceIdentity.create(attempt_id, invocation_id=invocation_id)

    m = RuntimeEvidenceManifest(
        evidence_identity=evidence_identity,
        attempt_id=attempt_id,
    )

    # Provenance: truthful lineage from actual evidence
    source_evidence = []
    if backend.stdout_path:
        source_evidence.append({"type": "stdout_log", "path": backend.stdout_path})
    if backend.stderr_path:
        source_evidence.append({"type": "stderr_log", "path": backend.stderr_path})
    source_evidence.append({"type": "health_gate", "passed": gate.passed})
    if monitor:
        source_evidence.append({"type": "monitor_events", "count": len(monitor.events)})

    m.provenance = Provenance(
        source_evidence=source_evidence,
        attempt_id=attempt_id,
        experiment_id="experiment_j_f1",
        parent_invocation_id=None,
        generator="f1_infra",
        generator_version="1.1.0",
    )

    # Infrastructure (raw observation)
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
    m.infrastructure["observed_process_graph"]["backend"]["stdout"] = _capture_file_status(backend.stdout_path)
    m.infrastructure["observed_process_graph"]["backend"]["stderr"] = _capture_file_status(backend.stderr_path)
    m.infrastructure["observed_process_graph"]["backend"]["exit_code"] = backend.exit_code
    m.infrastructure["observed_process_graph"]["backend"]["exit_time"] = backend.exit_time

    # Health gate
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

    # Evidence preservation
    m.evidence_preservation = {
        "verified": all([
            Path(backend.stdout_path).exists(),
            Path(backend.stderr_path).exists(),
            evidence_dir.exists(),
        ]),
        "invocation_id": evidence_identity.invocation_id,
    }

    # Monitoring events
    if monitor:
        m.monitoring = {
            "events": monitor.events,
            "alive_at_stop": monitor.alive,
        }

    # Raw observation (Priority 3)
    if observation:
        m.observation = {
            "backend_pid": observation.backend_pid,
            "expected_pid": observation.expected_pid,
            "start_time": observation.start_time,
            "end_time": observation.end_time,
            "pid_alive": observation.pid_alive,
            "process_state": observation.process_state,
            "cli_ok": observation.cli_ok,
            "timed_out": observation.timed_out,
            "elapsed_s": observation.elapsed_s,
            "tool_calls": observation.tool_calls,
            "infra_invalid_reason": observation.infra_invalid_reason,
            "workspace_identity": observation.workspace_identity,
        }

    # Derived classification (Priority 3)
    if classification:
        m.interpretation = classification.to_dict()

    # Validity (backward-compatible)
    m.validity = {
        "infrastructure": classification.validity_infrastructure if classification else "UNKNOWN",
    }
    m.infrastructure_invalid_reason = (
        classification.validity_infrastructure if classification and
        classification.validity_infrastructure == "INVALID"
        else ""
    ) or observation.infra_invalid_reason if observation else ""

    return m


def save_manifest(manifest: RuntimeEvidenceManifest, evidence_dir: Path) -> Path:
    """Save manifest to evidence directory, keyed by invocation ID."""
    inv_id = manifest.evidence_identity.invocation_id if manifest.evidence_identity else "unknown"
    manifest_path = evidence_dir / f"runtime_evidence_manifest_{inv_id}.json"
    payload = {
        "invocation_id": manifest.evidence_identity.invocation_id if manifest.evidence_identity else "",
        "content_hash": manifest.evidence_identity.content_hash if manifest.evidence_identity else "",
        "attempt_id": manifest.attempt_id,
        "provenance": {
            "source_evidence": manifest.provenance.source_evidence if manifest.provenance else [],
            "attempt_id": manifest.provenance.attempt_id if manifest.provenance else "",
            "experiment_id": manifest.provenance.experiment_id if manifest.provenance else "",
            "generator": manifest.provenance.generator if manifest.provenance else "",
            "generator_version": manifest.provenance.generator_version if manifest.provenance else "",
            "parent_invocation_id": manifest.provenance.parent_invocation_id if manifest.provenance else None,
            "tooling_version": manifest.provenance.tooling_version if manifest.provenance else "",
        },
        "infrastructure": manifest.infrastructure,
        "health_gate": manifest.health_gate,
        "monitoring": manifest.monitoring,
        "evidence_preservation": manifest.evidence_preservation,
        "observation": manifest.observation,
        "interpretation": manifest.interpretation,
        "validity": manifest.validity,
        "infrastructure_invalid_reason": manifest.infrastructure_invalid_reason,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return manifest_path
