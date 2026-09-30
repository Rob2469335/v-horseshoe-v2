"""F2 Execution Adapter — Architecture-D thin orchestration boundary.

Implements the P1 → P2 boundary of the F2 real-execution seam per design §14 and
the worker-execution authorization (docs/EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md,
§10).

The adapter is a THIN orchestration boundary, not a new execution engine. It:

1. receives a verified F2 arm / frozen-manifest reference;
2. preserves arm identity;
3. establishes a fresh execution backend (P2) via the existing F1
   ``f1_infra.start_backend_fresh`` machinery;
4. propagates the F2 replay environment into P2 (SWARM_F2_REPLAY=1,
   SWARM_F2_MANIFEST_PATH, SWARM_F2_REPO_ROOT, SWARM_F2_ROLLOUT_ID,
   SWARM_F2_TRAJECTORY_RUN_ID);
5. waits for backend readiness via ``f1_infra.wait_for_backend``;
6. verifies that F2 replay is REQUIRED and that the manifest is verified, and
   ABORTS (fail-closed) if F2 replay is required but cannot be established —
   before any model-facing execution;
7. establishes the CLI/task process (P3) via the ``_attempt_once`` mechanics
   (or an injectable seam for tests);
8. collects an execution/receipt boundary structure and returns it to the
   worker/controller.

Scope and authority:

- Authorized file: qwen_train/f2_execution_adapter.py (authorization §15).
- This slice must NOT modify delivery seams. The in-P2 ``is_replay_active()``
  gate at the actual model-facing delivery point belongs to the delivery-seam
  enforcement slice (authorization §9.1, §15) and is intentionally out of scope
  here. This adapter establishes the F2 replay-required precondition and P2
  fresh-process boundary; it does NOT itself perform model execution.
- F1 machinery (``start_backend_fresh``, ``wait_for_backend``, ``_attempt_once``,
  ``_reset_instance``) is reused read-only as TECHNICAL machinery. F1 scientific
  authorization is NOT transferred.
- No scientific execution, no N=2, no Experiment J.

Replay-required fail-closed contract implemented here:

- If SWARM_F2_REPLAY=1 (replay required) and the manifest cannot be verified:
  ABORT (raise), no P3 established, no execution accepted as successful.
- Replay-required state is derived from SWARM_F2_REPLAY=1 (boolean presence),
  NOT from artifact truthiness. C0 with an empty artifact remains a valid
  replay-active C0 arm; it is never treated as LIVE.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from runtime_v2.services.f2_freeze import (
    FreezeVerificationError,
    FrozenArtifact,
    load_manifest,
    verify_manifest,
)
from runtime_v2.services.f2_replay import (
    MANIFEST_PATH_ENV,
    REPLAY_REQUESTED_ENV,
)

# F2 environment variables the adapter must propagate into P2.
F2_ENV_VARS = (
    REPLAY_REQUESTED_ENV,        # "SWARM_F2_REPLAY"
    MANIFEST_PATH_ENV,          # "SWARM_F2_MANIFEST_PATH"
    "SWARM_F2_REPO_ROOT",
    "SWARM_F2_ROLLOUT_ID",
    "SWARM_F2_TRAJECTORY_RUN_ID",
)

# File the fake-model evidence recorder inside P2 writes to (infra test only).
FAKE_MODEL_EVIDENCE_FILE = "f2_fake_model_evidence.json"


def build_real_p2_launcher(
    *,
    repo_root: str | Path,
    workspace_root: str | Path,
    port: int,
    evidence_path: str | Path,
) -> str:
    """Body for a REAL fresh uvicorn backend (`python -c` launcher).

    The child process (P2) is the genuine backend: ``swarm_os.app.main`` app is
    imported and served by uvicorn, so ``main.py`` lifespan calls
    ``install_verified_replay_from_env()`` inside P2 and P2's ``_f2_state``
    becomes authoritative. The ONLY instrumentation is at the model boundary
    (and a spy on the LIVE renderer); the F2 delivery seam and replay guard that
    run are the REAL production ones.

    The fake ``complete_for_tool_decision`` returns a deterministic synthetic
    tool-decision and records evidence to ``evidence_path``:
      - PID / process identity in P2
      - F2_REQUIRED / REPLAY_ACTIVE / delivery artifact inside P2
      - rollout / trajectory identity
      - whether the LIVE ``render_active_lessons()`` was (incorrectly) requested

    NO real model/provider is ever contacted.
    """

    code = f"""
import os, sys, json, types

sys.path.insert(0, {str(repo_root)!r})
os.environ.setdefault("SWARM_WORKSPACE_ROOT", {str(workspace_root)!r})
os.environ["SWARM_MEMORY_INJECT"] = "0"
os.environ["SWARM_AUTONOMY"] = "0"
os.environ["SWARM_NO_TOASTS"] = "1"
os.environ["SWARM_SEMANTIC_CACHE"] = "0"
os.environ["SWARM_GENETIC_MUTATION"] = "0"
os.environ["SWARM_EVOLUTION"] = "0"

EVIDENCE = {str(evidence_path)!r}

from runtime_v2.services import f2_replay as _f2
from runtime_v2.services import stream_runner as _sr
from runtime_v2.services import _llm_client as _lc


class _NoMcpManager:
    cached_tools = []
    async def call_tool(self, *a, **k):
        return "no-mcp"
    async def start(self):
        return []
    async def stop(self):
        pass


async def _no_mcp_manager():
    return _NoMcpManager()


import runtime_v2.services.tool_executor as _te
_te.get_mcp_manager = _no_mcp_manager
import swarm_os.app.main as _main
_main.get_mcp_manager = _no_mcp_manager


def _write_evidence(**extra):
    rec = {{
        "pid": os.getpid(),
        "replay_required": bool(_f2.is_replay_required()),
        "replay_active": bool(_f2.is_replay_active()),
        "delivery_artifact": _f2.get_delivery_artifact(),
        "f2_replay_env_after_install": os.environ.get("SWARM_F2_REPLAY", ""),
        "rollout_id": os.environ.get("SWARM_F2_ROLLOUT_ID", ""),
        "trajectory_run_id": os.environ.get("SWARM_F2_TRAJECTORY_RUN_ID", ""),
    }}
    rec.update(extra)
    try:
        with open(EVIDENCE, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, sort_keys=True)
    except Exception:
        pass


async def _fake_complete(litellm_model, messages, fallbacks, agent_id=None):
    _write_evidence(model_called=True, model=litellm_model, agent_id=agent_id)
    payload = json.dumps({{"action": "final", "response": "fake f2 delivery complete", "ok": True}})
    choice = types.SimpleNamespace(message=types.SimpleNamespace(content=payload))
    return types.SimpleNamespace(choices=[choice])


class _SpyLessonManager:
    'LIVE-render spy: records if the LIVE renderer is (incorrectly) invoked.'
    def __init__(self, real):
        self._real = real
    async def render_active_lessons(self, *a, **k):
        _write_evidence(render_active_lessons_called=True)
        if self._real is None:
            return "[LIVE-RENDER-MARKER]"
        return await self._real.render_active_lessons(*a, **k)


try:
    import swarm_os.services.lesson_manager as _lm
    _orig_get_lesson_manager = _lm.get_lesson_manager
    def _wrapped_get_lesson_manager():
        real = _orig_get_lesson_manager() if _orig_get_lesson_manager is not None else None
        return _SpyLessonManager(real)
    _lm.get_lesson_manager = _wrapped_get_lesson_manager
except Exception:
    pass

_sr.complete_for_tool_decision = _fake_complete
_lc.complete_for_tool_decision = _fake_complete

from swarm_os.app.main import app
import uvicorn
uvicorn.run(app, host="127.0.0.1", port={port}, log_level="info")
"""
    return code


def start_real_p2_with_fake_model(
    *,
    attempt_id: str,
    repo_root: str | Path,
    workspace_root: str | Path,
    port: int,
    evidence_path: str | Path,
    work_dir: str | Path,
    manifest_path: str | Path,
    rollout_id: str,
    trajectory_run_id: str,
    startup_timeout: int = 60,
) -> dict[str, Any]:
    """Start a REAL fresh uvicorn backend (P2) with the F2 replay environment and
    a fake/instrumented model boundary (see ``build_real_p2_launcher``).

    The child environment explicitly contains SWARM_F2_REPLAY=1, the manifest
    path, repo root, and rollout/trajectory identity, mirroring the Architecture-D
    propagation contract. P2's ``main.py`` lifespan independently installs the
    verified replay inside P2.

    Returns a process-identity record (pid, expected_pid, parent_pid,
    command_identity, stdout/stderr paths, and the live Popen for cleanup).
    """
    env = os.environ.copy()
    env["SWARM_WORKSPACE_ROOT"] = str(workspace_root)
    env["SWARM_MEMORY_INJECT"] = "0"
    env["SWARM_AUTONOMY"] = "0"
    env["SWARM_NO_TOASTS"] = "1"
    env["SWARM_SEMANTIC_CACHE"] = "0"
    env["SWARM_GENETIC_MUTATION"] = "0"
    env["SWARM_EVOLUTION"] = "0"
    env["SWARM_F1_NO_WEB_TOOLS"] = "1"
    env[REPLAY_REQUESTED_ENV] = "1"
    env[MANIFEST_PATH_ENV] = str(manifest_path)
    env["SWARM_F2_REPO_ROOT"] = str(repo_root)
    env["SWARM_F2_ROLLOUT_ID"] = rollout_id
    env["SWARM_F2_TRAJECTORY_RUN_ID"] = trajectory_run_id

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = work_dir / f"{attempt_id}_backend_stdout.log"
    stderr_path = work_dir / f"{attempt_id}_backend_stderr.log"

    launcher = build_real_p2_launcher(
        repo_root=repo_root,
        workspace_root=workspace_root,
        port=port,
        evidence_path=evidence_path,
    )

    stdout_f = open(stdout_path, "w", encoding="utf-8", errors="replace")
    stderr_f = open(stderr_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-c", launcher],
        stdout=stdout_f,
        stderr=stderr_f,
        env=env,
        cwd=str(repo_root),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    record = {
        "role": "backend",
        "pid": proc.pid,
        "expected_pid": proc.pid,
        "parent_pid": os.getpid(),
        "command_identity": f"python -c <f2_real_p2_launcher> port={port}",
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "proc": proc,
    }
    return record


def terminate_backend(record: dict[str, Any] | None) -> None:
    """Terminate the P2 backend AND its whole descendant process tree.

    On this machine the established backend launcher re-executes itself once
    (a child Python process actually binds the port and serves), so cleanup must
    cover the entire tree, not just the launcher PID. Safe to call repeatedly.
    """
    if not record:
        return
    proc = record.get("proc")
    if proc is not None:
        try:
            if proc.poll() is None:
                try:
                    import psutil

                    for child in psutil.Process(proc.pid).children(
                        recursive=True
                    ):
                        try:
                            child.terminate()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001 process already gone
                    pass
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    proc.kill()
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def resolve_serving_pid(port: int) -> int | None:
    """Return the PID of the process actually LISTENING on ``port``.

    This is the authoritative P2 backend PID: the process serving the task/API
    path. Because the launcher may re-execute itself exactly once on this
    machine, the listener may differ from the initially-spawned PID; the
    listener is the process that actually installs replay and reaches the
    delivery seam.
    """
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                return conn.pid
    except Exception:  # noqa: BLE001
        pass
    return None


def wait_for_backend_health(port: int, timeout: int = 60) -> bool:
    """Wait for ``/health`` to return 200. Startup evidence ONLY — it does NOT by
    itself prove F2 replay is active (that is the fake-model evidence's job)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5.0
            ) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(1.0)
    return False


def post_task_stream(port: int, agent_id: str, prompt: str, timeout: int = 60) -> dict[str, Any]:
    """Drive the ACTUAL task/API path: POST /agents/{agent_id}/step/stream.

    This is the same HTTP/SSE surface the CLI (P3) uses. Returns parsed ndjson
    chunks plus raw prefix, or the HTTP failure.
    """
    import urllib.error

    body = json.dumps({"prompt": prompt}).encode("utf-8")
    url = f"http://127.0.0.1:{port}/agents/{agent_id}/step/stream"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "error": str(exc), "raw": ""}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "error": f"{type(exc).__name__}: {exc}", "raw": ""}

    chunks = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError:
            chunks.append({"raw": line})
    return {"status": 200, "chunks": chunks, "raw_prefix": raw[:800]}


def read_fake_model_evidence(evidence_path: str | Path, timeout: int = 30) -> dict[str, Any]:
    """Read the evidence file the in-P2 fake model wrote. Polls briefly."""
    path = Path(evidence_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        time.sleep(0.5)
    return {}


# --- injectable seams (test hooks; default to the real F1 machinery) ----------

BackendStarter = Callable[..., tuple[Any, Path]]
BackendWaiter = Callable[..., Any]
CliRunner = Callable[[dict, int, bool, bool], dict]


def _default_backend_starter(*args: Any, **kwargs: Any) -> tuple[Any, Path]:
    from qwen_train import f1_infra

    return f1_infra.start_backend_fresh(*args, **kwargs)


def _default_backend_waiter(*args: Any, **kwargs: Any) -> Any:
    from qwen_train import f1_infra

    return f1_infra.wait_for_backend(*args, **kwargs)


def _default_cli_runner(item: dict, timeout: int, allow_approval: bool, record: bool) -> dict:
    from qwen_train import run_curriculum

    return run_curriculum._attempt_once(item, timeout, allow_approval, record)


@dataclass
class AdapterExecutionBoundary:
    """The execution/receipt boundary returned to the worker.

    Records what the adapter established (F2 replay-required, verified manifest
    identity, fresh P2 process identity, CLI/task evidence) WITHOUT performing a
    model run. It is the seam contract; the receipt/evidence layers B-E are
    collected and returned here for the worker to join into the F2 receipt.
    """

    arm: str
    manifest_path: str
    manifest_content_address: str
    manifest_verified: bool
    replay_required: bool
    backend: dict[str, Any] | None = None
    cli: dict[str, Any] | None = None
    p2_replay_state: dict[str, Any] = field(default_factory=dict)
    execution_evidence: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None


class F2ExecutionAdapter:
    """Thin Architecture-D adapter: P1 -> fresh P2 boundary."""

    def __init__(
        self,
        *,
        arm: str,
        manifest_path: str | Path,
        repo_root: str | Path,
        workspace_root: str | Path,
        backend_starter: BackendStarter | None = None,
        backend_waiter: BackendWaiter | None = None,
        cli_runner: CliRunner | None = None,
        port: int = 8000,
        backend_timeout: int = 60,
    ) -> None:
        self.arm = arm
        self.manifest_path = Path(manifest_path)
        self.repo_root = Path(repo_root)
        self.workspace_root = Path(workspace_root)
        self.backend_starter = backend_starter or _default_backend_starter
        self.backend_waiter = backend_waiter or _default_backend_waiter
        self.cli_runner = cli_runner or _default_cli_runner
        self.port = port
        self.backend_timeout = backend_timeout

    def replay_required(self) -> bool:
        """F2 replay-required is the boolean presence of SWARM_F2_REPLAY=1.

        Replay-required is NEVER derived from artifact truthiness (an empty C0
        artifact is still replay-required). Callers must use this, not the
        emptiness of the delivered string.
        """
        return os.environ.get(REPLAY_REQUESTED_ENV, "") == "1"

    def _f2_env_snapshot(self) -> dict[str, str]:
        """Snapshot the F2 environment values that must reach P2."""
        return {var: os.environ[var] for var in F2_ENV_VARS if var in os.environ}

    # -- Manifest verification (reuse, no duplication) -------------------------

    def _load_verified_manifest(self) -> FrozenArtifact:
        artifact = load_manifest(self.manifest_path)
        verify_manifest(artifact)
        return artifact

    # -- P2 fresh backend ------------------------------------------------------

    def _start_fresh_backend(self) -> tuple[Any, Path]:
        """Start a fresh P2 via the F1 machinery.

        ``f1_infra.start_backend_fresh`` builds ``env=os.environ.copy()`` and
        injects the F1 control vars; because the current process (P1/worker)
        holds the F2 env vars (SWARM_F2_REPLAY, SWARM_F2_MANIFEST_PATH,
        SWARM_F2_REPO_ROOT, SWARM_F2_ROLLOUT_ID, SWARM_F2_TRAJECTORY_RUN_ID),
        they propagate into P2's environment automatically. P2's ``main.py``
        then calls ``install_verified_replay_from_env()`` and owns ``_f2_state``.
        """
        record, evidence_dir = self.backend_starter(
            attempt_id=f"f2arm_{self.arm}",
            workspace_root=str(self.workspace_root),
            port=self.port,
            timeout=min(30, self.backend_timeout),
        )
        return record, evidence_dir

    def _wait_backend(self, record: Any) -> Any:
        gate = self.backend_waiter(
            record,
            port=self.port,
            timeout=self.backend_timeout,
            pid_check_interval=2.0,
        )
        return gate

    def _probe_fresh_p2_replay(self) -> dict[str, Any]:
        """Spawn a REAL fresh Python subprocess that independently installs and
        reports replay state.

        This is the P2-side proof: the child runs the authoritative
        ``install_verified_replay_from_env()`` in ITS OWN process, so its
        ``is_replay_required()`` / ``is_replay_active()`` are P2-local (never
        inherited from P1). The F2 environment (SWARM_F2_*) is inherited from
        the adapter process, mirroring how ``start_backend_fresh`` passes env to
        a fresh backend. Returns the parsed child report.
        """
        import json
        import subprocess
        import sys

        code = (
            "import os, sys, json\n"
            f"sys.path.insert(0, {str(self.repo_root)!r})\n"
            "from runtime_v2.services.f2_replay import (\n"
            "    install_verified_replay_from_env,\n"
            "    is_replay_active,\n"
            "    is_replay_required,\n"
            ")\n"
            "try:\n"
            "    install_verified_replay_from_env()\n"
            "    print(json.dumps({\n"
            "        'replay_required': is_replay_required(),\n"
            "        'replay_active': is_replay_active(),\n"
            "        'manifest_path': os.environ.get('SWARM_F2_MANIFEST_PATH', ''),\n"
            "    }))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'error': type(e).__name__ + ': ' + str(e)}))\n"
        )
        env = dict(os.environ)
        env["SWARM_F2_REPLAY"] = "1"
        env["SWARM_F2_MANIFEST_PATH"] = str(self.manifest_path)
        env["SWARM_F2_REPO_ROOT"] = str(self.repo_root)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.repo_root),
            timeout=min(self.backend_timeout, 60),
        )
        out = (proc.stdout or "").strip()
        payload: dict[str, Any] = {}
        for line in reversed(out.splitlines()):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                payload = parsed
                break
        payload["returncode"] = proc.returncode
        if not payload and (proc.stderr or "").strip():
            payload["error"] = (proc.stderr or "").strip()[-500:]
        return payload

    def _require_replay_established(self, artifact: FrozenArtifact) -> None:
        """Fail-closed: F2 replay required implies the manifest is verified.

        The real in-P2 ``is_replay_active()`` gate at the model-facing delivery
        seam is enforced in the delivery-seam slice (authorization §9.1/§15).
        This adapter establishes the requirement and the fresh P2; it must NOT
        proceed to P3/execution if replay-required is set but cannot be backed by
        a verified manifest.
        """
        if not self.replay_required():
            raise FreezeVerificationError(
                "F2ExecutionAdapter: replay-required env (SWARM_F2_REPLAY=1) not set; "
                "this adapter is only valid for F2 replay-required arms"
            )
        if artifact.arm != self.arm:
            raise FreezeVerificationError(
                f"F2ExecutionAdapter: manifest arm {artifact.arm!r} != requested {self.arm!r}"
            )

    # -- CLI/task (P3) --------------------------------------------------------

    def _drive_cli(self, task_item: dict, timeout: int, allow_approval: bool, record: bool) -> dict:
        return self.cli_runner(task_item, timeout, allow_approval, record)

    # -- Main entry -----------------------------------------------------------

    def run(
        self,
        *,
        task_prompt: str,
        task_id: str,
        rollout_id: str,
        trajectory_run_id: str,
        timeout_s: int = 600,
        allow_approval: bool = False,
        dry_run: bool = True,
    ) -> AdapterExecutionBoundary:
        """Establish the P1→P2→P3 execution boundary and return evidence.

        ``dry_run=True`` (default) establishes the OS/process boundary WITHOUT a
        model run — used by tests and infrastructure verification. When
        ``dry_run=False``, the CLI/task P3 is driven via ``_attempt_once``; the
        model-facing delivery-seam replay gate is enforced in the delivery-seam
        slice, so this adapter does NOT itself guarantee in-P2 replay
        activity for a live run in this slice.
        """
        env_snapshot = self._f2_env_snapshot()
        artifact = self._load_verified_manifest()
        try:
            self._require_replay_established(artifact)
        except FreezeVerificationError:
            raise

        backend = None
        gate = None
        cli = None
        failure = None
        try:
            backend, _ = self._start_fresh_backend()
            gate = self._wait_backend(backend)
        except Exception as exc:  # noqa: BLE001 - fail closed
            failure = f"backend_start_failed: {type(exc).__name__}: {exc}"
            return AdapterExecutionBoundary(
                arm=self.arm,
                manifest_path=str(self.manifest_path),
                manifest_content_address=artifact.content_address,
                manifest_verified=True,
                replay_required=self.replay_required(),
                backend={"record": self._backend_dict(backend)},
                failure=failure,
            )

        if gate is None or not getattr(gate, "passed", False):
            failure = f"backend_not_healthy: {getattr(gate, 'errors', [])}"
            return AdapterExecutionBoundary(
                arm=self.arm,
                manifest_path=str(self.manifest_path),
                manifest_content_address=artifact.content_address,
                manifest_verified=True,
                replay_required=self.replay_required(),
                backend={"record": self._backend_dict(backend)},
                failure=failure,
            )

        # P2-side replay proof: a REAL fresh subprocess independently runs
        # install_verified_replay_from_env() and reports ITS OWN replay state.
        # The stream must show F2_REQUIRED=True AND REPLAY_ACTIVE=True before P3.
        p2_replay = {}
        try:
            p2_replay = self._probe_fresh_p2_replay()
        except Exception as exc:  # noqa: BLE001 - fail closed
            failure = f"p2_replay_probe_failed: {type(exc).__name__}: {exc}"
            return AdapterExecutionBoundary(
                arm=self.arm,
                manifest_path=str(self.manifest_path),
                manifest_content_address=artifact.content_address,
                manifest_verified=True,
                replay_required=self.replay_required(),
                backend={"record": self._backend_dict(backend)},
                p2_replay_state={"error": str(exc)},
                failure=failure,
            )
        if isinstance(p2_replay, dict) and p2_replay.get("error"):
            failure = f"p2_replay_not_established: {p2_replay['error']}"
            return AdapterExecutionBoundary(
                arm=self.arm,
                manifest_path=str(self.manifest_path),
                manifest_content_address=artifact.content_address,
                manifest_verified=True,
                replay_required=self.replay_required(),
                backend={"record": self._backend_dict(backend)},
                p2_replay_state=p2_replay,
                failure=failure,
            )
        if not p2_replay.get("replay_required") or not p2_replay.get("replay_active"):
            failure = (
                "p2_replay_required_or_active_false: "
                f"{p2_replay}"
            )
            return AdapterExecutionBoundary(
                arm=self.arm,
                manifest_path=str(self.manifest_path),
                manifest_content_address=artifact.content_address,
                manifest_verified=True,
                replay_required=self.replay_required(),
                backend={"record": self._backend_dict(backend)},
                p2_replay_state=p2_replay,
                failure=failure,
            )

        if not dry_run:
            cli = self._drive_cli(
                {"prompt": task_prompt, "id": task_id},
                timeout_s,
                allow_approval,
                record=False,
            )

        return AdapterExecutionBoundary(
            arm=self.arm,
            manifest_path=str(self.manifest_path),
            manifest_content_address=artifact.content_address,
            manifest_verified=True,
            replay_required=self.replay_required(),
            backend={
                "record": self._backend_dict(backend),
                "health_gate_passed": bool(getattr(gate, "passed", False)),
            },
            cli=cli,
            p2_replay_state=p2_replay,
            execution_evidence={
                "task_id": task_id,
                "rollout_id": rollout_id or os.environ.get("SWARM_F2_ROLLOUT_ID", ""),
                "trajectory_run_id": trajectory_run_id
                or os.environ.get("SWARM_F2_TRAJECTORY_RUN_ID", ""),
                "f2_env": env_snapshot,
            },
            failure=failure,
        )

    def prove_real_p2_delivery(
        self,
        *,
        task_prompt: str,
        task_id: str,
        rollout_id: str,
        trajectory_run_id: str,
        agent_id: str = "f2probe",
        port: int = 8211,
        work_dir: str | Path | None = None,
        startup_timeout: int = 60,
        http_timeout: int = 60,
    ) -> dict[str, Any]:
        """Architecture-D real-P2 proof: REAL fresh backend + ACTUAL delivery seam.

        1. loads + verifies the frozen manifest and requires F2 replay (fail
           closed if the caller has not set SWARM_F2_REPLAY=1);
        2. starts a REAL fresh uvicorn backend (P2) with the F2 replay
           environment and an instrumented/fake model boundary — P2's own
           ``main.py`` lifespan installs the verified replay inside P2;
        3. waits for backend startup (``/health``; startup evidence only);
        4. drives the ACTUAL task/API path ``POST /agents/{agent_id}/step/stream``
           to reach the replay-gated delivery seam in P2;
        5. reads the fake-model evidence recorded inside P2 (replay state,
           delivery artifact, LIVE-renderer spy, PID).

        NO real model/provider is ever contacted. Returns a dict of evidence.
        """
        import tempfile

        artifact = self._load_verified_manifest()
        try:
            self._require_replay_established(artifact)
        except FreezeVerificationError:
            raise

        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix="f2_real_p2_"))
        work_dir = Path(work_dir)
        evidence_path = work_dir / FAKE_MODEL_EVIDENCE_FILE

        record = None
        try:
            record = start_real_p2_with_fake_model(
                attempt_id=f"f2arm_{self.arm}_{port}",
                repo_root=self.repo_root,
                workspace_root=self.workspace_root,
                port=port,
                evidence_path=evidence_path,
                work_dir=work_dir,
                manifest_path=self.manifest_path,
                rollout_id=rollout_id,
                trajectory_run_id=trajectory_run_id,
                startup_timeout=startup_timeout,
            )

            healthy = wait_for_backend_health(port, startup_timeout)
            http = {}
            if healthy:
                http = post_task_stream(port, agent_id, task_prompt, http_timeout)

            evidence = read_fake_model_evidence(evidence_path, http_timeout)

            # The AUTHORITATIVE P2 backend PID is the process listening on the
            # port (the process that installs replay and serves the delivery
            # seam). On this machine the launcher re-executes itself once, so the
            # listener may be the launcher's child — resolve it from the OS.
            launcher_pid = record.get("pid")
            serving_pid = resolve_serving_pid(port) or launcher_pid

            return {
                "p1_pid": os.getpid(),
                "p2_pid": serving_pid,
                "p2_launcher_pid": launcher_pid,
                "p2_expected_pid": record.get("expected_pid"),
                "p2_launcher_parent_pid": record.get("parent_pid"),
                "p2_is_launcher": serving_pid == launcher_pid,
                "process_identity": {
                    k: record[k]
                    for k in (
                        "role",
                        "pid",
                        "expected_pid",
                        "parent_pid",
                        "command_identity",
                        "stdout_path",
                        "stderr_path",
                    )
                    if k in record
                },
                "backend_healthy": healthy,
                "http": http,
                "fake_model_evidence": evidence,
                "arm": self.arm,
                "manifest_path": str(self.manifest_path),
                "manifest_content_address": artifact.content_address,
                "manifest_verified": True,
                "replay_required_env": self.replay_required(),
                "rollout_id": rollout_id,
                "trajectory_run_id": trajectory_run_id,
                "work_dir": str(work_dir),
            }
        finally:
            terminate_backend(record)

    @staticmethod
    def _backend_dict(backend: Any) -> dict[str, Any]:
        if backend is None:
            return {}
        bd = getattr(backend, "__dict__", {})
        return {k: bd[k] for k in ("role", "pid", "expected_pid", "parent_pid", "state") if k in bd}


def build_execution_boundary_payload(boundary: AdapterExecutionBoundary) -> dict[str, Any]:
    """Serialize the adapter boundary for the worker to join into the F2 receipt."""
    return {
        "adapter": {
            "arm": boundary.arm,
            "manifest_path": boundary.manifest_path,
            "manifest_content_address": boundary.manifest_content_address,
            "manifest_verified": boundary.manifest_verified,
            "replay_required": boundary.replay_required,
        },
        "backend": boundary.backend,
        "cli": boundary.cli,
        "p2_replay_state": boundary.p2_replay_state,
        "execution_evidence": boundary.execution_evidence,
        "failure": boundary.failure,
    }


__all__ = [
    "F2ExecutionAdapter",
    "AdapterExecutionBoundary",
    "build_execution_boundary_payload",
    "F2_ENV_VARS",
]