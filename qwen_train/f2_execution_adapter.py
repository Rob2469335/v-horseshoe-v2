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
    "SWARM_F2_TRAJ_DIR",         # F2-OP-INFRA-004 §1 (D2): explicit absolute
                                # delivery-evidence dir; writer and reader must
                                # resolve to the same directory.
)

# File the fake-model evidence recorder inside P2 writes to (infra test only).
FAKE_MODEL_EVIDENCE_FILE = "f2_fake_model_evidence.json"

# F2-OP-INFRA-004 §1 (D2).
F2_TRAJ_DIR_ENV = "SWARM_F2_TRAJ_DIR"


def read_evidence_write_outcomes(stdout_path: str | Path | None) -> list[dict[str, Any]]:
    """Collect P2's explicit delivery-evidence write outcomes (F2-OP-INFRA-004 §2 / D3).

    ``AgentServiceV2`` emits one ``{"type": "f2_evidence_write_outcome", ...}`` JSON
    line per delivery on P2's stdout, precisely so the outcome survives even when
    the trajectory write itself is what failed. P1 already captures that stdout, so
    no new IPC is introduced. Returns an empty list when the file is unavailable.
    """
    if not stdout_path:
        return []
    try:
        raw = Path(stdout_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or "f2_evidence_write_outcome" not in line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("type") == "f2_evidence_write_outcome":
            out.append(rec)
    return out


def resolve_f2_traj_dir(workspace_root: Path | str) -> Path:
    """Absolute, deterministic delivery-evidence directory for one execution.

    F2-OP-INFRA-004 §1 (D2). This is ONE directory per execution (each execution
    owns its workspace), not a per-rollout redesign — per-rollout evidence files
    remain explicitly out of scope as W6.

    It lives INSIDE the isolated workspace so evidence never lands in the main
    repository, and it is absolute so it does not depend on P2's cwd.
    """
    return Path(workspace_root).resolve() / "data" / "trajectories"



def load_pool_row(instance_id: str) -> dict[str, Any]:
    """Read the authoritative curriculum row for ``instance_id`` (read-only).

    F2-OP-INFRA-004 §4 (W4). The curriculum pool is the single source of task
    truth; nothing here synthesizes or mutates it. Raises when the row is absent
    so a task can never be executed without declared identity.
    """
    pool = Path(__file__).resolve().parent / "curriculum" / "swe_pool.jsonl"
    for line in pool.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("instance_id") == instance_id:
            return row
    raise FreezeVerificationError(
        f"F2 fail-closed: no curriculum pool row for instance_id={instance_id!r}. "
        "Task identity must come from the declared pool, never be synthesized."
    )


def bind_task_environment(
    *,
    instance_id: str,
    task_id: str,
    workspace_root: str | Path,
    repo_root: str | Path,
    evaluator_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Bind one F2 arm execution to the EXISTING task-environment machinery.

    F2-OP-INFRA-004 §4 (W4). This REUSES, and does not reinvent, the F1 task
    contract from ``qwen_train/run_repair_task.py``:

      * ``_pool_env_meta``            — declared environment (base_image/install)
      * ``resolve_task_python``        — declared interpreter (fail-closed; never
                                         falls back to the project interpreter)
      * ``task_exec_plan``             — install steps + execution prerequisites
      * ``task_test_argv``             — test command argv
      * ``_preflight_evaluator_separation`` — evaluator outside the workspace
      * ``F1_AUTHORIZED_BASE_COMMIT``  — the F1-authorized harness base commit

    Establishes and returns: task_id, instance_id, repo, base_commit,
    base_image_name, isolated workspace, interpreter, install requirements, test
    command, and evaluator separation. Fails closed rather than degrading.

    The main repository can never become the task workspace.
    """
    from qwen_train import run_repair_task as _rrt

    row = load_pool_row(instance_id)
    env_meta = _rrt._pool_env_meta(instance_id)

    inst = dict(row)
    inst.setdefault("install", env_meta.get("install", ""))
    inst.setdefault("base_image_name", env_meta.get("base_image_name", ""))

    # Declared interpreter — raises rather than falling back to the project one.
    # ``resolve_task_python`` yields a launcher (e.g. ["py","-3.10"]); argv
    # construction needs the concrete executable, which is exactly what the
    # already-established ``_task_python_path`` bridge provides.
    launcher = _rrt.resolve_task_python(inst)
    task_py = _rrt._task_python_path(launcher)
    plan = _rrt.task_exec_plan(inst, task_py)

    workspace = Path(workspace_root).resolve()
    repo = Path(repo_root).resolve()

    # The main repository must NEVER be the mutable task workspace.
    if workspace == repo or repo in workspace.parents:
        raise FreezeVerificationError(
            f"F2 fail-closed: task workspace {workspace} is inside the main "
            f"repository {repo}. The main repository must never be the task workspace."
        )

    test_cmd = str(row.get("test_cmd") or "")
    if not test_cmd.strip():
        raise FreezeVerificationError(
            f"F2 fail-closed: curriculum row {instance_id!r} declares no test_cmd; "
            "an F2 execution must carry a declared verification command."
        )
    test_argv = _rrt.task_test_argv(task_py, test_cmd)

    evaluator_errors: list[str] = []
    if evaluator_dir is not None:
        evaluator_errors = _rrt._preflight_evaluator_separation(workspace, Path(evaluator_dir))
        if evaluator_errors:
            raise FreezeVerificationError(
                "F2 fail-closed: evaluator separation violated: "
                + "; ".join(evaluator_errors)
            )

    return {
        "task_id": task_id,
        "instance_id": instance_id,
        "repo": str(row.get("repo") or ""),
        "base_commit": str(row.get("base_commit") or ""),
        "base_image_name": str(inst.get("base_image_name") or ""),
        "image_name": str(row.get("image_name") or ""),
        "harness_base_commit": _rrt.F1_AUTHORIZED_BASE_COMMIT,
        "workspace_root": str(workspace),
        "interpreter": list(launcher),
        "interpreter_str": " ".join(str(p) for p in launcher),
        "task_python": str(task_py),
        "install_steps": plan.get("install_steps", []),
        "install_argv": plan.get("install_argv", []),
        "execution_prereqs": plan.get("execution_prereqs", []),
        "test_cmd": test_cmd,
        "test_argv": list(test_argv),
        "evaluator_dir": str(Path(evaluator_dir).resolve()) if evaluator_dir else "",
        "evaluator_separation_ok": not evaluator_errors,
        "pool_row_found": True,
    }


def build_real_p2_launcher(
    *,
    repo_root: str | Path,
    workspace_root: str | Path,
    port: int,
    evidence_path: str | Path,
    fake_model: bool = True,
) -> str:
    """Body for a REAL fresh uvicorn backend (`python -c` launcher).

    The child process (P2) is the genuine backend: ``swarm_os.app.main`` app is
    imported and served by uvicorn, so ``main.py`` lifespan calls
    ``install_verified_replay_from_env()`` inside P2 and P2's ``_f2_state``
    becomes authoritative. The F2 delivery seam and replay guard that run are the
    REAL production ones in both modes.

    ``fake_model=True``  — test mode: replaces ``complete_for_tool_decision`` with
      a deterministic synthetic response so the seam is provable without a model.
    ``fake_model=False`` — F2-OP-INFRA-004 §3 (W2) PRODUCTION mode: the model call
      is NOT replaced. P2 resolves and calls the EXISTING production model path
      (``stream_runner._call_llm`` → ``get_litellm_model``). No monkeypatch, no new
      model subsystem, no MCP-manager override.

    Both modes keep the LIVE-render spy, because proving the LIVE lesson renderer
    was never reached is a required fail-closed property, not a test convenience.
    """

    # F2-OP-INFRA-004 §3 (W2): in production mode NOTHING about the model call is
    # replaced — P2 uses the existing stream_runner._call_llm -> get_litellm_model
    # path and the real MCP manager. Substituted values are inserted verbatim by
    # the f-string below, so braces here need no escaping.
    if fake_model:
        _fake_patch = (
            "import runtime_v2.services.tool_executor as _te\n"
            "_te.get_mcp_manager = _no_mcp_manager\n"
            "import swarm_os.app.main as _main\n"
            "_main.get_mcp_manager = _no_mcp_manager\n"
            "...\n"
            "_sr.complete_for_tool_decision = _fake_complete\n"
            "_lc.complete_for_tool_decision = _fake_complete\n"
        )
    else:
        _fake_patch = (
            "# W2 PRODUCTION: real model call, real MCP manager — nothing patched.\n"
            "MODE = 'production_model'\n"
        )

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

{_fake_patch}
from swarm_os.app.main import app
import uvicorn
uvicorn.run(app, host="127.0.0.1", port={port}, log_level="info")
"""
    return code


def start_real_p2_production_model(
    *,
    attempt_id: str,
    repo_root: str | Path,
    workspace_root: str | Path,
    port: int,
    work_dir: str | Path,
    manifest_path: str | Path,
    rollout_id: str,
    trajectory_run_id: str,
    evidence_path: str | Path | None = None,
    startup_timeout: int = 60,
) -> dict[str, Any]:
    """F2-OP-INFRA-004 §3 (W2): the PRODUCTION model-facing P2.

    Identical to :func:`start_real_p2_with_fake_model` in every respect that the
    F2 contract governs — fresh process, F2 replay environment propagated, P2's
    ``main.py`` lifespan independently installing verified replay, the real
    delivery seam, the real replay guard, and the LIVE-render spy — with exactly
    one difference:

        ``complete_for_tool_decision`` is NOT replaced.

    P2 therefore resolves and calls the EXISTING production model path
    (``stream_runner._call_llm`` → ``get_litellm_model``). No new model subsystem,
    no model architecture/weight/config change, no MCP-manager override.

    The fake-model entry point is retained unchanged for the existing tests.
    """
    return _start_real_p2(
        attempt_id=attempt_id,
        repo_root=repo_root,
        workspace_root=workspace_root,
        port=port,
        evidence_path=evidence_path,
        work_dir=work_dir,
        manifest_path=manifest_path,
        rollout_id=rollout_id,
        trajectory_run_id=trajectory_run_id,
        startup_timeout=startup_timeout,
        fake_model=False,
    )


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
    fake_model: bool = True,
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
    return _start_real_p2(
        attempt_id=attempt_id,
        repo_root=repo_root,
        workspace_root=workspace_root,
        port=port,
        evidence_path=evidence_path,
        work_dir=work_dir,
        manifest_path=manifest_path,
        rollout_id=rollout_id,
        trajectory_run_id=trajectory_run_id,
        startup_timeout=startup_timeout,
        fake_model=fake_model,
    )


def _start_real_p2(
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
    fake_model: bool = True,
) -> dict[str, Any]:
    """Shared P2 launcher. ``fake_model`` selects the model boundary only."""
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
    # F2-OP-INFRA-004 §1 (D2): publish the explicit absolute evidence directory so
    # the P2 writer and the F2 worker reader resolve to the SAME directory.
    env[F2_TRAJ_DIR_ENV] = str(resolve_f2_traj_dir(workspace_root))

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = work_dir / f"{attempt_id}_backend_stdout.log"
    stderr_path = work_dir / f"{attempt_id}_backend_stderr.log"

    launcher = build_real_p2_launcher(
        repo_root=repo_root,
        workspace_root=workspace_root,
        port=port,
        evidence_path=evidence_path,
        fake_model=fake_model,
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

    Returns ``None`` when the owner cannot be determined. ``None`` means
    UNKNOWN — it never means "the launcher" (see ``_resolve_serving_identity``).
    """
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                return conn.pid
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_serving_identity(port: int, launcher_pid: Any) -> tuple[int, str]:
    """Establish the serving-process identity, or fail closed.

    Returns ``(serving_pid, source)``. Raises ``FreezeVerificationError`` when
    the LISTEN socket owner cannot be resolved.

    The launcher PID is NEVER substituted for the serving PID. This launcher may
    re-execute itself once on this machine, so relabelling it as the serving
    process would assert an identity that was never established — F2
    worker-execution authorization §10 item 5 requires the socket owner, and the
    §13 fail-closed matrix requires ABORT when execution process identity is
    unestablished. The launcher identity is recorded separately by the caller.
    """
    serving_pid = resolve_serving_pid(port)
    if serving_pid is None:
        raise FreezeVerificationError(
            "F2 fail-closed: could not establish the P2 serving-process identity for "
            f"port {port} (no LISTEN owner resolved). Launcher pid {launcher_pid!r} is "
            "recorded separately and is NOT substituted for the serving process."
        )
    return serving_pid, "socket_listener"


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

        F2-OP-INFRA-004 §1 (D2): the explicit evidence directory is published on
        the CHILD environment by ``_start_real_p2`` (``env[F2_TRAJ_DIR_ENV]``).
        It is deliberately NOT written into this process's ``os.environ`` here:
        mutating the parent environment would leak across unrelated callers and
        tests, and the backend inherits env from the caller's copy.
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
            # Process identity is NEVER guessed: unresolved stays unresolved and
            # fails closed. The launcher may re-exec itself once on this machine,
            # so it must not be relabelled as the serving process.
            serving_pid, serving_pid_source = _resolve_serving_identity(port, launcher_pid)

            return {
                "p1_pid": os.getpid(),
                "p2_pid": serving_pid,
                "p2_launcher_pid": launcher_pid,
                "p2_expected_pid": record.get("expected_pid"),
                "p2_launcher_parent_pid": record.get("parent_pid"),
                "p2_is_launcher": serving_pid == launcher_pid,
                "serving_pid_source": serving_pid_source,
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

    def execute_arm_real(
        self,
        *,
        task_prompt: str,
        task_id: str,
        rollout_id: str,
        trajectory_run_id: str,
        agent_id: str = "coder",
        port: int = 8211,
        work_dir: str | Path | None = None,
        startup_timeout: int = 60,
        http_timeout: int = 120,
    ) -> dict[str, Any]:
        """F2-OP-INFRA-004 §3 (W2): run one arm through the PRODUCTION model seam.

        This is the authorized production path. Unlike
        ``prove_real_p2_delivery`` it does NOT replace
        ``complete_for_tool_decision``: P2 runs the real
        ``stream_runner._call_llm`` → ``get_litellm_model`` path and the real MCP
        manager. Everything the F2 contract governs is identical to the
        proven test path:

        1. the frozen manifest is loaded and independently verified;
        2. F2 replay must already be required/established, else fail closed;
        3. a REAL fresh P2 backend is started with the F2 replay environment
           (including ``SWARM_F2_TRAJ_DIR``);
        4. backend readiness is gated on ``/health``;
        5. the task is driven over the EXISTING loopback HTTP/SSE surface
           ``POST /agents/{agent_id}/step/stream``;
        6. the serving-process identity is resolved from the LISTEN socket owner
           and fails closed when it cannot be established;
        7. P2's explicit evidence-write outcomes are collected;
        8. the backend is always terminated.

        Returns structured execution evidence for the F2 worker.
        """
        import tempfile

        artifact = self._load_verified_manifest()
        self._require_replay_established(artifact)

        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix="f2_real_p2_"))
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        record: dict[str, Any] | None = None
        try:
            record = start_real_p2_production_model(
                attempt_id=f"f2arm_{self.arm}_{port}",
                repo_root=self.repo_root,
                workspace_root=self.workspace_root,
                port=port,
                evidence_path=work_dir / FAKE_MODEL_EVIDENCE_FILE,
                work_dir=work_dir,
                manifest_path=self.manifest_path,
                rollout_id=rollout_id,
                trajectory_run_id=trajectory_run_id,
                startup_timeout=startup_timeout,
            )

            healthy = wait_for_backend_health(port, startup_timeout)
            http: dict[str, Any] = {}
            if healthy:
                http = post_task_stream(port, agent_id, task_prompt, http_timeout)

            launcher_pid = record.get("pid")
            # D4: never substitute the launcher for an unresolved serving PID.
            serving_pid, serving_pid_source = _resolve_serving_identity(port, launcher_pid)

            write_outcomes = read_evidence_write_outcomes(record.get("stdout_path"))
            evidence_failures = [
                o for o in write_outcomes if o.get("outcome") == "write_failed"
            ]

            return {
                "mode": "production_model",
                "model_monkeypatched": False,
                "p1_pid": os.getpid(),
                "p2_pid": serving_pid,
                "p2_launcher_pid": launcher_pid,
                "serving_pid_source": serving_pid_source,
                "p2_is_launcher": serving_pid == launcher_pid,
                "backend_healthy": healthy,
                "http": http,
                "arm": self.arm,
                "task_id": task_id,
                "manifest_path": str(self.manifest_path),
                "manifest_content_address": artifact.content_address,
                "manifest_verified": True,
                "replay_required_env": self.replay_required(),
                "rollout_id": rollout_id,
                "trajectory_run_id": trajectory_run_id,
                "traj_dir": os.environ.get(F2_TRAJ_DIR_ENV, ""),
                "evidence_write_outcomes": write_outcomes,
                "evidence_write_failures": evidence_failures,
                "process_identity": {
                    k: record[k]
                    for k in ("role", "pid", "expected_pid", "parent_pid",
                              "command_identity", "stdout_path", "stderr_path")
                    if k in record
                },
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