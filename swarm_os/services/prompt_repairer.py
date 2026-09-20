"""Governance layer between raw trajectory experience and the worker prompt.
Enforces the 11/10 standard invariants:
1. PS classification required (Fail Closed)
2. 3 independent trajectories for evidence threshold (Independence)
3. Controlled evaluation gate (Fail Closed)
4. Contradiction blocking (Deterministic)
5. Atomic promotion & exact state rollback
6. Explicit state machine
7. Promotion Proof Object
8. Audit Trail (Append-only)
9. Memory Membrane (Inject Protection)
10. Strict 300 token budget
"""

from __future__ import annotations
import json
import logging
import uuid
import time
import asyncio
import os
from enum import Enum
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Any

_log = logging.getLogger(__name__)

from swarm_os.healing.diagnostician import Diagnostician
from swarm_os.services.lesson_manager import (
    ActiveLesson,
    MAX_ACTIVE_TOKENS,
    MAX_RULE_TOKENS,
    _is_contradiction,
    _jaccard_tokens,
    clear_eval_context,
    estimate_tokens,
    get_lesson_manager,
    register_eval_context,
)
from swarm_os.lib.atomic_io import atomic_write_text

GOVERNANCE_VERSION = 1
# Default evidence governance: at least 3 INDEPENDENT trajectories (unique run
# ids) are required before a candidate may even be created. Configurable, but
# the default is deliberately strict — one or two failures never promote.
MIN_EVIDENCE_RUNS = 3
# Genuine task diversity: 3 runs of the SAME task are not 3 independent
# observations. Promotion requires at least 2 distinct task identities.
MIN_EVIDENCE_TASKS = 2
# Bounded evaluation tick: at most ONE controlled evaluation attempt per
# candidate per this window (prevents re-evaluation storms). Configurable.
EVAL_ATTEMPT_COOLDOWN_S = 3600

_REPO_ROOT = Path(__file__).parent.parent.parent
_DATA_DIR = _REPO_ROOT / "data"
_CANDIDATES_FILE = _DATA_DIR / "prompt_repairer_candidates.json"
_SNAPSHOTS_FILE = _DATA_DIR / "prompt_repairer_snapshots.json"
_AUDIT_LOG_FILE = _DATA_DIR / "prompt_repairer_audit.jsonl"


def _journal_file() -> Path:
    """Transaction journal for crash-safe promotion. Derived from _DATA_DIR at
    call time so tests that redirect _DATA_DIR also isolate the journal."""
    return _DATA_DIR / "prompt_repairer_journal.jsonl"


def _rollout_log_file() -> Path:
    """Append-only per-rollout log (OUTSIDE the governed path).

    Retains each arm's numbers regardless of the promotion verdict, so an
    honest INSUFFICIENT_EVIDENCE / NOT_IMPROVED stop does not lose the data
    step 8 (25-rollout measurement) needs. Appends only — never truncates.
    """
    return _DATA_DIR / "prompt_repairer_rollouts.jsonl"


def _append_rollout(rec: dict) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_rollout_log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        _log.debug("rollout log write failed: %s", exc)


def _git_commit() -> str:
    """Short HEAD hash, so rollout records can be compared honestly across
    harness/code changes (arm_config_hash alone doesn't cover code)."""
    try:
        import subprocess

        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _arm_config_hash(swe: dict, arm: str, lesson_on: bool) -> str:
    """Stable hash of the arm's config so runs can be pooled only when the arm
    is identical across tasks (steps 3–6 must not change the arm)."""
    import hashlib

    base = (
        f"{swe.get('instance_id')}|{swe.get('base_commit')}|{swe.get('test_cmd')}"
        f"|{arm}|{GOVERNANCE_VERSION}|lesson={bool(lesson_on)}|{_git_commit()}"
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _rollout_purpose() -> str:
    """Label for rollout records (e.g. 'canary' vs 'eval'), from the env so a
    canary run is never pooled into the 25-rollout measurement."""
    import os

    return os.environ.get("SWARM_ROLLOUT_PURPOSE", "eval")


def _git_dirty() -> bool:
    """True if the working tree has uncommitted changes, so a rollout record's
    git_commit is never trusted as containing the code that produced it."""
    try:
        import subprocess

        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=5,
        )
        return bool(r.stdout.strip())
    except Exception:
        return True  # unknown -> fail safe as dirty


# --- Trusted evaluation-receipt authority (Phase 2/3) -----------------------
# The receipt is an HMAC over the candidate's COMPLETE canonical governance
# state, keyed by a secret that lives OUTSIDE candidate state. A party who can
# edit the candidate JSON can compute the public state hash but CANNOT forge a
# valid signature without the key, so it cannot manufacture a PASS receipt.
EVALUATOR_ID = "benchmark-evaluator"
EVALUATOR_VERSION = "1"


def _receipt_key() -> bytes | None:
    """Return the TRUSTED receipt-signing key from ``SWARM_RECEIPT_KEY`` ONLY.

    The secret is never auto-generated and never stored beside candidate state,
    so an actor who can read or modify the candidate data directory cannot
    obtain signing authority. When the trusted key is absent the receipt
    authority FAILS CLOSED: signing returns None and verification refuses, so
    no evaluation receipt can be minted and no promotion can occur without an
    operator-provisioned secret.
    """
    import os

    env_key = os.environ.get("SWARM_RECEIPT_KEY")
    if env_key:
        return env_key.encode("utf-8")
    return None


class CandidateState(str, Enum):
    OBSERVED = "OBSERVED"
    HYPOTHESIS = "HYPOTHESIS"
    EVIDENCE_GATHERING = "EVIDENCE_GATHERING"
    CANDIDATE = "CANDIDATE"
    EVALUATING = "EVALUATING"
    PROMOTABLE = "PROMOTABLE"
    ACTIVE = "ACTIVE"
    MONITORED = "MONITORED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"
    CONTRADICTED = "CONTRADICTED"
    SUPERSEDED = "SUPERSEDED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


@dataclass
class PromotionProof:
    candidate_id: str
    hypothesis_id: str
    evidence_ids: List[str]
    unique_run_ids: List[str]
    task_ids: List[str]
    evaluation_id: str
    evaluation_status: str
    evaluation_metrics: dict
    baseline_metrics: dict
    candidate_metrics: dict
    token_count: int
    conflict_check: str
    governance_version: int
    timestamp: float


def is_safe_lesson(text: str) -> bool:
    """Governance Membrane: Reject injection attacks.

    Scoped narrowly so it blocks hostile meta-instruction ("ignore previous
    instructions", "change safety policy", "you are now an unrestricted…", a
    direct re-write of system/prompt rules) without rejecting legitimate
    repair lessons that merely mention operational words such as "sandbox",
    "permissions", "token" or "system".
    """
    text_lower = (text or "").lower()
    hostile = [
        "ignore previous",
        "change safety",
        "disable approval",
        "you are now an unrestricted",
        "disregard all",
        "override the system prompt",
        "you have no restrictions",
        "reveal your prompts",
        "ignore your instructions",
    ]
    for phrase in hostile:
        if phrase in text_lower:
            return False
    # Bounded-meta guard: a lesson may discuss governance but cannot order it.
    tries = [
        "change the token budget",
        "modify the approval rules",
        "rewrite the promotion rules",
        "disable rollback",
        "increase the evidence threshold",
    ]
    for phrase in tries:
        if phrase in text_lower:
            return False
    return True


class BenchmarkEvaluator:
    """Production evaluator that A/B tests a candidate lesson against a baseline task."""
    def __init__(self, task_id: str = "c01", timeout: int = 1800):
        # A full SWE-rebench agent arm legitimately runs for minutes; the old
        # 60s default killed both arms before the harness could write a result.
        self.task_id = task_id
        self.timeout = timeout

    @staticmethod
    def _load_swe_task(task_id: str) -> dict | None:
        try:
            pool = _REPO_ROOT / "qwen_train" / "curriculum" / "swe_pool.jsonl"
            if not pool.exists():
                return None
            for line in pool.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    if rec.get("instance_id") == task_id:
                        return rec
        except Exception:
            return None
        return None

    async def _run_swe_harness(self, swe: dict, problem_statement: str, eval_id: str | None, arm: str = "candidate") -> dict | None:
        """Run ONE arm of a SWE task via run_repair_task.py. Returns the last
        result record, or None on ANY failure (fail closed). The candidate arm
        carries SWARM_EVAL_ID so only it receives the isolated lesson snapshot."""
        import os
        import tempfile

        instance_id = swe.get("instance_id", "")
        work = Path(os.environ.get("SWARM_SWE_WORK", str(_REPO_ROOT.parent / "swe_probe_work")))
        repo = work / instance_id / "repo"
        if not (repo / ".git").exists():
            return None

        # ---- preflight (fail closed, no verdict) ----
        preflight = await self._run_preflight()
        if not preflight.get("ok"):
            _append_rollout({
                "task_id": instance_id, "arm": arm,
                "failure_category": preflight.get("failure_category",
                                                  "endpoint_preflight_failed"),
                "verify_reason": preflight.get("verify_reason"),
                "timestamp": time.time(),
            })
            return None

        out = Path(tempfile.mkdtemp()) / "arm.jsonl"
        py = _REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        cmd = [
            str(py),
            str(_REPO_ROOT / "qwen_train" / "run_repair_task.py"),
            "--instance-id", instance_id,
            "--base-commit", str(swe.get("base_commit", "")),
            "--problem-statement", problem_statement,
            "--test-cmd", str(swe.get("test_cmd", "")),
            "--out", str(out),
        ]
        for f2p in (swe.get("fail_to_pass", []) or []):
            cmd += ["--f2p", str(f2p)]
        env = dict(os.environ)
        if eval_id:
            env["SWARM_EVAL_ID"] = eval_id
        else:
            env.pop("SWARM_EVAL_ID", None)
        # Harness-supplied SWE task identity (never model-derived).
        env["SWARM_TASK_ID"] = instance_id
        # Harness credential so the backend honors the task id (proves it came
        # from the harness, not the worker). SEPARATE from SWARM_RECEIPT_KEY so
        # a leak cannot forge promotion receipts.
        _hk = os.environ.get("SWARM_HARNESS_KEY")
        if _hk:
            env["SWARM_HARNESS_KEY"] = _hk
        proc = None
        rc = None
        output = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(_REPO_ROOT), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(self.timeout):
                output, _ = await proc.communicate()
            rc = proc.returncode
        except Exception as exc:  # noqa: BLE001
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            _log.warning("SWE_HARNESS_FAILURE %s", json.dumps({
                "task_id": swe.get("instance_id", ""), "arm": arm,
                "phase": "launch_or_timeout", "error": str(exc)[:300],
                "return_code": rc, "out_exists": out.exists(),
                "stdout_tail": output.decode("utf-8", "replace")[-3000:],
            }))
            return None
        try:
            lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines:
                raise ValueError("out file empty/absent")
            rec = json.loads(lines[-1])
            # ---- postcheck (harness-side before/after snapshots) ----
            pc = await self._run_postcheck(preflight)
            pc_ok = pc.get("ok", False)
            _append_rollout({
                "task_id": instance_id, "arm": arm,
                "evaluation_id": eval_id or "",
                "arm_config_hash": _arm_config_hash(swe, arm, bool(eval_id)),
                "run_id": rec.get("run_id", ""),
                "verdict": None if not pc_ok else rec.get("verdict"),
                "verify_reason": pc.get("verify_reason") if not pc_ok else rec.get("verify_reason"),
                "tools_used": rec.get("tools_used"),
                "f2p": rec.get("f2p"),
                "elapsed_s": rec.get("elapsed_s"),
                "edit_attempted": "filesystem" in (rec.get("tools_used") or []),
                "edit_valid": bool(rec.get("diff_stat")) if "diff_stat" in rec else None,
                "git_commit": _git_commit(),
                "dirty": _git_dirty(),
                "purpose": _rollout_purpose(),
                "failure_category": "postcheck_failed" if not pc_ok else (None if rec.get("verdict") else "task_failure"),
                "inference_endpoint": preflight.get("inference_endpoint"),
                "router_boot_id": preflight.get("boot_id"),
                "timeout_count": pc.get("timeout_count", 0),
                "timestamp": time.time(),
            })
            return rec
        except Exception as exc:  # noqa: BLE001
            _log.warning("SWE_HARNESS_FAILURE %s", json.dumps({
                "task_id": swe.get("instance_id", ""), "arm": arm,
                "return_code": rc, "out_exists": out.exists(),
                "parse_error": str(exc)[:200],
                "stdout_tail": output.decode("utf-8", "replace")[-3000:],
            }))
            _append_rollout({
                "task_id": swe.get("instance_id", ""), "arm": arm,
                "evaluation_id": eval_id or "",
                "arm_config_hash": _arm_config_hash(swe, arm, bool(eval_id)),
                "verdict": None, "failure_category": "harness_failure",
                "parse_error": str(exc)[:200], "timestamp": time.time(),
            })
            return None

    # ------------------------------------------------------------------
    # Checkpoint 3: endpoint preflight + post-arm validation
    # ------------------------------------------------------------------
    @staticmethod
    def _pin_config_path() -> Path:
        return Path(os.environ.get(
            "SWARM_PIN_CONFIG",
            str(Path(os.environ.get("TEMP", "")) / "opencode" / "prompt_repairer_pin.json"),
        ))

    @staticmethod
    def _props_hash(props: dict) -> str:
        import hashlib
        return hashlib.sha256(
            f"{props.get('build_info', '')}|{props.get('model_path', '')}"
            f"|{props.get('default_generation_settings', {}).get('n_ctx', 0)}"
            f"|{props.get('total_slots', 0)}".encode()
        ).hexdigest()[:16]

    async def _run_preflight(self) -> dict:
        """Pre-arm endpoint validation.  Fail closed, 10 s timeout, no retries.

        Returns ``{"ok": True, "boot_id": …, "inference_endpoint": …,
        "pre_counters": …}`` on pass, or
        ``{"ok": False, "failure_category": "endpoint_preflight_failed",
        "verify_reason": …}`` on failure.  The caller must write a rollout
        record on failure and return None — the arm never starts.
        """
        import httpx as _httpx

        pin_path = self._pin_config_path()
        if not pin_path.exists():
            return {"ok": False,
                    "failure_category": "endpoint_preflight_failed",
                    "verify_reason": f"pin config not found: {pin_path}"}
        try:
            pin = json.loads(pin_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False,
                    "failure_category": "endpoint_preflight_failed",
                    "verify_reason": f"pin config unreadable: {exc}"}

        tunnel_port = pin.get("tunnel_port", 8079)
        status_port = pin.get("status_port", 8095)

        # 1. :8079 listener owner must be ssh.exe
        try:
            import psutil
            ssh_found = False
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == tunnel_port and conn.status == "LISTEN":
                    proc = psutil.Process(conn.pid)
                    if proc.name().lower() == "ssh.exe":
                        ssh_found = True
                    else:
                        return {"ok": False,
                                "failure_category": "endpoint_preflight_failed",
                                "verify_reason": f":{tunnel_port} owned by {proc.name()}, not ssh.exe"}
                    break
            if not ssh_found:
                return {"ok": False,
                        "failure_category": "endpoint_preflight_failed",
                        "verify_reason": f":{tunnel_port} not listening"}
        except Exception as exc:
            return {"ok": False,
                    "failure_category": "endpoint_preflight_failed",
                    "verify_reason": f"port check failed: {exc}"}

        # 2. Status endpoint: pinned=true, record boot_id + counter snapshot
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                hk = os.environ.get("SWARM_HARNESS_KEY", "")
                resp = await client.get(
                    f"http://127.0.0.1:{status_port}/inference/status",
                    headers={"X-Swarm-Harness-Key": hk},
                )
                if resp.status_code != 200:
                    return {"ok": False,
                            "failure_category": "endpoint_preflight_failed",
                            "verify_reason": f"status endpoint returned {resp.status_code}"}
                status = resp.json()
        except Exception as exc:
            return {"ok": False,
                    "failure_category": "endpoint_preflight_failed",
                    "verify_reason": f"status endpoint unreachable: {exc}"}

        if not status.get("pinned"):
            return {"ok": False,
                    "failure_category": "endpoint_preflight_failed",
                    "verify_reason": "router not pinned (SWARM_ROUTER_PINNED=1 not set)"}

        boot_id = status.get("boot_id", "")
        pre_counters = {
            "completion_requests": status.get("completion_requests", 0),
            "pairs": dict(status.get("pairs", {})),
            "pairless": status.get("pairless", 0),
        }

        # 3. /props hash via the tunnel (not the router)
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                props = (await client.get(
                    f"http://127.0.0.1:{tunnel_port}/props")).json()
        except Exception as exc:
            return {"ok": False,
                    "failure_category": "endpoint_preflight_failed",
                    "verify_reason": f"/props unreachable: {exc}"}

        actual = self._props_hash(props)
        expected = self._props_hash(pin)
        if actual != expected:
            return {"ok": False,
                    "failure_category": "endpoint_preflight_failed",
                    "verify_reason": f"fingerprint mismatch "
                                     f"(expected={expected}, actual={actual}), "
                                     f"re-pin if intended"}

        return {"ok": True, "boot_id": boot_id,
                "inference_endpoint": actual, "pre_counters": pre_counters}

    async def _run_postcheck(self, preflight: dict) -> dict:
        """Post-arm endpoint validation.  Returns ``{"ok": True}`` or
        ``{"ok": False, "verify_reason": …}``."""
        import httpx as _httpx

        pin_path = self._pin_config_path()
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        tunnel_port = pin.get("tunnel_port", 8079)
        status_port = pin.get("status_port", 8095)

        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                hk = os.environ.get("SWARM_HARNESS_KEY", "")
                status = (await client.get(
                    f"http://127.0.0.1:{status_port}/inference/status",
                    headers={"X-Swarm-Harness-Key": hk},
                )).json()
        except Exception as exc:
            return {"ok": False, "verify_reason": f"post-arm status unreachable: {exc}"}

        # boot_id unchanged
        if status.get("boot_id") != preflight.get("boot_id"):
            return {"ok": False,
                    "verify_reason": "router restarted mid-arm (boot_id changed)"}

        # completion_requests delta > 0
        pre_cr = preflight["pre_counters"]["completion_requests"]
        post_cr = status.get("completion_requests", 0)
        if post_cr <= pre_cr:
            return {"ok": False,
                    "verify_reason": f"zero completions during arm "
                                     f"({post_cr} <= {pre_cr})"}

        # Exactly one distinct pair in the delta, matching the pinned pair
        pre_pairs = preflight["pre_counters"]["pairs"]
        post_pairs = status.get("pairs", {})
        delta_pairs = {}
        for k, v in post_pairs.items():
            d = v - pre_pairs.get(k, 0)
            if d > 0:
                delta_pairs[k] = d
        if len(delta_pairs) != 1:
            return {"ok": False,
                    "verify_reason": f"expected 1 distinct pair in delta, "
                                     f"got {len(delta_pairs)}: {delta_pairs}"}

        # pairless delta == 0
        pre_pl = preflight["pre_counters"]["pairless"]
        post_pl = status.get("pairless", 0)
        if post_pl > pre_pl:
            return {"ok": False,
                    "verify_reason": f"pairless responses during arm "
                                     f"({post_pl} > {pre_pl})"}

        # /props hash unchanged
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                props = (await client.get(
                    f"http://127.0.0.1:{tunnel_port}/props")).json()
            post_hash = self._props_hash(props)
            if post_hash != preflight.get("inference_endpoint"):
                return {"ok": False,
                        "verify_reason": f"/props hash changed mid-arm "
                                         f"({preflight['inference_endpoint']} -> {post_hash})"}
        except Exception as exc:
            return {"ok": False,
                    "verify_reason": f"post-arm /props check failed: {exc}"}

        return {"ok": True, "timeout_count": status.get("errors", 0) + status.get("aborted", 0)}

    async def _eval_swe(self, candidate: dict, task_id: str) -> dict:
        """Baseline vs candidate on the genuine SWE instance. Fail closed on
        missing task/instance/problem_statement/harness/timeout/result."""
        swe = self._load_swe_task(task_id)
        if not swe:
            raise ValueError(f"SWE task {task_id} not in swe_pool.jsonl")
        try:
            from qwen_train.swe_rebench_probe import fetch_instance

            hf = await asyncio.to_thread(fetch_instance, task_id)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot fetch SWE instance {task_id}: {exc}")
        ps = (hf or {}).get("problem_statement") or ""
        if not ps:
            raise RuntimeError(f"SWE instance {task_id} has no problem_statement")

        eval_id = candidate.get("evaluation_id")
        base = await self._run_swe_harness(swe, ps, eval_id=None, arm="baseline")
        cand = await self._run_swe_harness(swe, ps, eval_id=eval_id, arm="candidate")
        if base is None or cand is None:
            raise RuntimeError("missing SWE harness result (baseline or candidate)")

        base_ok = bool(base.get("verdict"))
        cand_ok = bool(cand.get("verdict"))
        improved = cand_ok and not base_ok
        return {
            "pass": improved,
            "unrelated_regression": base_ok and not cand_ok,
            "effectiveness": 1.0 if improved else 0.0,
            "baseline": base,
            "candidate": cand,
            "harness": {
                "task_id": task_id,
                "evaluation_id": eval_id,
                "test_cmd": swe.get("test_cmd", ""),
            },
        }

    @staticmethod
    def _is_swe_task(task_id: str) -> bool:
        """True if ``task_id`` is a SWE-rebench instance in swe_pool.jsonl."""
        try:
            pool = _REPO_ROOT / "qwen_train" / "curriculum" / "swe_pool.jsonl"
            if not pool.exists():
                return False
            needle = f'"instance_id": "{task_id}"'
            for line in pool.read_text(encoding="utf-8").splitlines():
                if line.strip() and needle in line:
                    return True
        except Exception:
            return False
        return False

    async def __call__(self, candidate: dict) -> dict:
        # Task identity travels WITH the candidate (canonical primary task).
        # Fall back to the configured default only when the candidate has none.
        task_id = str(candidate.get("task_id") or self.task_id or "").strip()
        if not task_id:
            raise ValueError("no task identity on candidate and no default evaluator task")

        # SWE-rebench tasks (real-repo repair) run the genuine per-instance
        # environment via the harness, baseline vs candidate (candidate carries
        # the isolated SWARM_EVAL_ID). Fail closed on any error.
        if self._is_swe_task(task_id):
            return await self._eval_swe(candidate, task_id)

        try:
            from qwen_train.run_curriculum import load_items, _attempt_once
        except ImportError:
            raise RuntimeError("qwen_train module not available for evaluation")

        items = load_items()
        item = next((i for i in items if i.get("id") == task_id), None)
        if not item:
            raise ValueError(f"Task {task_id} not found in curriculum")

        # 1. Baseline measurement
        baseline_res = await asyncio.to_thread(_attempt_once, item, self.timeout, True, False)

        # 2. Temporarily inject candidate rule
        manager = get_lesson_manager()
        from swarm_os.services.lesson_manager import EVAL_COLLECTION
        temp_lesson = ActiveLesson(
            rule=f"{candidate['trigger']}: {candidate['action']}",
            confidence=0.9,
            effectiveness=0.9,
            source_candidates=[candidate["id"]],
        )
        lid = await manager.store(temp_lesson, collection_name=EVAL_COLLECTION)

        # 3. Candidate measurement
        try:
            cand_res = await asyncio.to_thread(_attempt_once, item, self.timeout, True, False)
        finally:
            await manager.remove(lid, collection_name=EVAL_COLLECTION)

        cand_verified = cand_res.get("verified", False)
        baseline_verified = baseline_res.get("verified", False)
        
        if cand_res.get("timed_out"):
            raise asyncio.TimeoutError("Candidate run timed out")

        return {
            "pass": cand_verified,
            "unrelated_regression": baseline_verified and not cand_verified,
            "effectiveness": 1.0 if cand_verified else 0.0,
            "baseline": baseline_res,
            "candidate": cand_res
        }


class PromptRepairer:
    def __init__(self, diagnostician=None, lesson_manager=None, evaluator=None):
        self.diagnostician = diagnostician or Diagnostician()
        self.lesson_manager = lesson_manager or get_lesson_manager()
        self.evaluator = evaluator if evaluator is not None else BenchmarkEvaluator()
        self._candidates = self._load_json(_CANDIDATES_FILE, default={})
        self._snapshots = self._load_json(_SNAPSHOTS_FILE, default={})

        # Attack 17 fix: Recover orphaned EVALUATING states
        dirty = False
        for cid, cand in self._candidates.items():
            if cand.get("status") == CandidateState.EVALUATING.value:
                cand["status"] = CandidateState.CANDIDATE.value
                dirty = True
        if dirty:
            self._save_candidates()

        # Crash-safe promotion recovery: a journal row stuck at "qdrant_applied"
        # (i.e. the process died after Qdrant accepted the new lesson but before
        # "committed") is a DANGLING active lesson. Startup removes it and marks
        # the candidate back to PROMOTABLE so a future run re-promotes cleanly.
        # A "committed" row is proof the promotion completed — never rolled back.
        # (A caller must explicitly await repairer.recover_interrupted_promotions() on startup)

    # -- Crash-safe promotion journal --
    def _journal_append(self, phase: str, candidate_id: str, lesson_id: str = ""):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        row = {
            "phase": phase,  # begin | snapshot_saved | qdrant_applied | committed
            "candidate_id": candidate_id,
            "lesson_id": lesson_id,
            "timestamp": time.time(),
        }
        with open(_journal_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    async def recover_interrupted_promotions(self):
        journal = _journal_file()
        if not journal.exists():
            return
        try:
            pending = []  # (candidate_id, lesson_id) qualified for rollback
            for line in journal.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    self._audit("JOURNAL_CORRUPT", {"line": line[:200]})
                    continue
                phase = row.get("phase")
                if phase == "qdrant_applied":
                    # Did 'committed' follow this candidate later? If so the
                    # promotion completed — leave the lesson alone.
                    pending.append((row.get("candidate_id"), row.get("lesson_id", "")))
                elif phase == "committed":
                    pending = [
                        p for p in pending if p[0] != row.get("candidate_id")
                    ]
            for candidate_id, lesson_id in pending:
                if lesson_id:
                    try:
                        # Step 6 fix: Verify provenance before deletion
                        lessons = await self.lesson_manager.get_all()
                        for lesson in lessons:
                            if lesson.id == lesson_id:
                                if candidate_id in lesson.source_candidates:
                                    await self.lesson_manager.remove(lesson_id)
                                else:
                                    self._audit("ROLLBACK_PROVENANCE_MISMATCH", {"lesson_id": lesson_id, "candidate_id": candidate_id})
                    except Exception:
                        pass
                cand = self._candidates.get(candidate_id)
                if cand:
                    # Roll the candidate back to PROMOTABLE so a future
                    # promotion attempt re-runs cleanly. NEVER auto-activate.
                    if cand.get("status") == CandidateState.ACTIVE.value:
                        cand["status"] = CandidateState.PROMOTABLE.value
                        cand.pop("active_lesson_id", None)
                self._audit(
                    "RECOVERED_INTERRUPTED_PROMOTION",
                    {"candidate_id": candidate_id, "lesson_id": lesson_id},
                )
            if pending:
                self._save_candidates()
        except Exception as exc:
            self._audit("JOURNAL_RECOVERY_FAILED", {"error": str(exc)})

    async def evaluate_and_promote_eligible(self) -> dict:
        """Bounded orchestration seam (the missing 'learning tick').

        For each eligible CANDIDATE lesson, make at most ONE controlled
        evaluation attempt this window, then promote ONLY if the existing
        Promotion Gate accepts it. It never weakens thresholds, never bypasses
        the receipt/quarantine, and never auto-promotes merely because an
        evaluation ran. Idempotent via a per-candidate cooldown.
        """
        summary = {"considered": 0, "evaluated": 0, "promoted": 0, "skipped": 0, "failed": 0}
        now = time.time()
        for cid, cand in list(self._candidates.items()):
            if cand.get("status") != CandidateState.CANDIDATE.value:
                continue
            runs = {e.get("run_id") for e in cand.get("evidence_runs", []) if isinstance(e, dict)}
            tasks = {t for t in cand.get("evidence_tasks", []) if t}
            if len(runs) < MIN_EVIDENCE_RUNS or len(tasks) < MIN_EVIDENCE_TASKS:
                summary["skipped"] += 1
                continue
            last = float(cand.get("last_eval_attempt", 0) or 0)
            if now - last < EVAL_ATTEMPT_COOLDOWN_S:
                summary["skipped"] += 1
                continue
            cand["last_eval_attempt"] = now
            self._save_candidates()
            summary["considered"] += 1
            try:
                res = await self.evaluate_candidate(cid)
            except Exception as exc:  # noqa: BLE001 - audited, never fatal to the tick
                self._audit("EVAL_TICK_ERROR", {"candidate_id": cid, "error": str(exc)})
                summary["failed"] += 1
                continue
            if res == "evaluation_passed":
                summary["evaluated"] += 1
                if self._candidates[cid].get("status") == CandidateState.PROMOTABLE.value:
                    pres = await self.promote(cid)
                    if pres == "promoted":
                        summary["promoted"] += 1
                    _append_rollout({
                        "task_id": cand.get("task_id", ""), "candidate_id": cid,
                        "decision": pres, "purpose": _rollout_purpose(), "timestamp": time.time(),
                    })
            else:
                summary["failed"] += 1
                _append_rollout({
                    "task_id": cand.get("task_id", ""), "candidate_id": cid,
                    "decision": res, "purpose": _rollout_purpose(), "timestamp": time.time(),
                })
        if summary["considered"]:
            self._audit("EVAL_TICK", summary)
        return summary

    def _load_json(self, path: Path, default: Any) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return default

    def _save_candidates(self):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_CANDIDATES_FILE, json.dumps(self._candidates, indent=2))

    def _save_snapshots(self):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_SNAPSHOTS_FILE, json.dumps(self._snapshots, indent=2))

    def _canonical_state(self, cand: dict) -> str:
        """Deterministic canonical form of EVERY governance-relevant field.

        Binds the complete candidate state (rule, evidence runs + their
        hypotheses, task scope, activation scope, governance version), not a
        partial `id:trigger:action` subset.
        """
        ev = cand.get("evidence_runs", [])
        norm = []
        for e in ev:
            if isinstance(e, dict):
                norm.append(
                    {"run_id": str(e.get("run_id", "")), "rollout_id": str(e.get("rollout_id", "")), "hypothesis": str(e.get("hypothesis", ""))}
                )
            else:
                norm.append({"run_id": str(e), "rollout_id": "", "hypothesis": ""})
        payload = {
            "candidate_id": str(cand.get("id", "")),
            "hypothesis_id": str(cand.get("id", "")),
            "trigger": str(cand.get("trigger", "")),
            "action": str(cand.get("action", "")),
            "evidence_runs": norm,
            "evidence_tasks": [str(t) for t in cand.get("evidence_tasks", [])],
            "activation_scope": str(cand.get("activation_scope", cand.get("component", ""))),
            "governance_version": int(cand.get("governance_version", GOVERNANCE_VERSION)),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _hash_candidate(self, cand: dict) -> str:
        """Public state hash (NOT the authority — the HMAC receipt is)."""
        import hashlib

        return hashlib.sha256(self._canonical_state(cand).encode("utf-8")).hexdigest()

    def _sign_receipt(self, receipt: dict) -> str | None:
        import hashlib
        import hmac

        key = _receipt_key()
        if key is None:
            return None  # no trusted signing authority → cannot sign (fail closed)
        body = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()

    def _verify_receipt(self, receipt: dict, signature: str) -> bool:
        import hmac

        if not isinstance(receipt, dict) or not signature:
            return False
        expected = self._sign_receipt(receipt)
        if expected is None:
            return False  # no trusted signing authority → never verify (fail closed)
        try:
            return hmac.compare_digest(expected, str(signature))
        except Exception:
            return False

    def _audit(self, event_type: str, details: dict):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "event_type": event_type,
            "details": details
        }
        with open(_AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _change_state(self, cand: dict, new_state: CandidateState, reason: str = ""):
        old_state = cand["status"]
        cand["status"] = new_state.value
        self._audit("STATE_CHANGE", {
            "candidate_id": cand["id"],
            "old_state": old_state,
            "new_state": new_state.value,
            "reason": reason
        })
        self._save_candidates()

    def process_failure(self, run_id: str, component: str, failure_reason: str, hypothesized_action: str, task_id: str = "", source: str = "", rollout_id: str = "") -> str:
        """Process a failure (OBSERVED -> EVIDENCE_GATHERING)."""
        # Per-event proof of the harness-supplied identity + the calling exit
        # path. Logs ONLY run_id/task_id/source/component — never headers/keys.
        _log.info(
            "process_failure run_id=%s task_id=%r source=%s component=%s",
            run_id, task_id or "", source or "unknown", component,
        )
        self._audit("OBSERVED_FAILURE", {"run_id": run_id, "failure_reason": failure_reason[:100]})
        
        fix_class = self.diagnostician._classify_fix(failure_reason)
        if fix_class == "model_variability":
            self._audit("REJECTED_MV", {"run_id": run_id})
            return "rejected: model_variability"

        # Membrane check
        if not is_safe_lesson(hypothesized_action):
            self._audit("REJECTED_INJECTION", {"run_id": run_id})
            return "rejected: safety_membrane"

        # Truncate
        if len(failure_reason) > 2000:
            failure_reason = failure_reason[:2000]
        if len(hypothesized_action) > 2000:
            hypothesized_action = hypothesized_action[:2000]

        # Option A (evidence gating, 2026-09-20): only harness-tagged failures
        # (task_id supplied by the agent-loop exit path) may accumulate promotion
        # evidence. Untagged events are observed + audited (OBSERVED_FAILURE
        # above) but never create a candidate or append evidence. This covers
        # watch-loop too — it deliberately mints its own run ids, is never
        # task-tagged, and must not inflate MIN_EVIDENCE_RUNS. The explicit
        # watch-loop branch below is retained (its test pins the deliberate
        # decision) even though this guard makes it unreachable.
        if not task_id:
            return "ignored: untagged_event"

        # Check existing candidates for similarity
        matched_id = None
        for cid, cand in self._candidates.items():
            if _jaccard_tokens(cand["action"], hypothesized_action) > 0.6:
                matched_id = cid
                break

        if not matched_id:
            cid = uuid.uuid4().hex[:12]
            cand = {
                "id": cid,
                "trigger": failure_reason,
                "action": hypothesized_action,
                "component": component,
                "task_id": task_id or "",
                "evidence_runs": [{"run_id": run_id, "rollout_id": rollout_id, "hypothesis": hypothesized_action}] if source != "watch-loop" else [],
                "evidence_tasks": [task_id] if task_id else [],
                "status": CandidateState.EVIDENCE_GATHERING.value,
                "governance_version": GOVERNANCE_VERSION,
                "version": 1,
                "created_at": time.time(),
            }
            self._candidates[cid] = cand
            self._audit("HYPOTHESIS_CREATED", {"candidate_id": cid})
            self._save_candidates()
            return "evidence_added"
        else:
            cand = self._candidates[matched_id]
            
            # Independence enforcement: one harness rollout = at most one evidence run.
            # Key on the harness rollout id when present, else fall back to the
            # run id (local/non-harness events keep today's per-event semantics).
            existing_keys = []
            for e in cand["evidence_runs"]:
                if isinstance(e, dict):
                    existing_keys.append(e.get("rollout_id") or e.get("run_id", ""))
                else:
                    existing_keys.append(e)
            evidence_key = rollout_id or run_id
            if evidence_key in existing_keys:
                return "ignored: duplicate_run"
            # Watch-loop events mint fresh run_ids and must not inflate
            # evidence counts toward MIN_EVIDENCE_RUNS=3.
            if source == "watch-loop":
                self._audit("WATCH_LOOP_EVENT", {"run_id": run_id, "matched": matched_id})
                return "watch-loop: recorded, not counted"
                
            cand["evidence_runs"].append({"run_id": run_id, "rollout_id": rollout_id, "hypothesis": hypothesized_action})
            if task_id and task_id not in cand["evidence_tasks"]:
                cand["evidence_tasks"].append(task_id)
            # Canonical primary task identity (first task that produced the failure)
            if task_id and not cand.get("task_id"):
                cand["task_id"] = task_id

            self._audit("EVIDENCE_ADDED", {"candidate_id": matched_id, "run_id": run_id})

            if cand["status"] == CandidateState.EVIDENCE_GATHERING.value and len(cand["evidence_runs"]) >= MIN_EVIDENCE_RUNS:
                self._change_state(cand, CandidateState.CANDIDATE, f"Reached {MIN_EVIDENCE_RUNS} independent runs")
                return "candidate_created"
            
            self._save_candidates()
            return "evidence_added"

    def _is_contradiction_v1(self, r1: str, r2: str) -> bool:
        """Deterministic contradiction check for V1."""
        r1 = r1.lower()
        r2 = r2.lower()
        parts1 = r1.split(":", 1)
        parts2 = r2.split(":", 1)
        if len(parts1) == 2 and len(parts2) == 2:
            if _jaccard_tokens(parts1[0], parts2[0]) > 0.8:
                act1, act2 = parts1[1].strip(), parts2[1].strip()
                negations = {"never", "do not", "don't", "avoid", "stop", "no"}
                has_neg1 = any(n in act1.split() for n in negations)
                has_neg2 = any(n in act2.split() for n in negations)
                act1_clean = " ".join(w for w in act1.split() if w not in negations)
                act2_clean = " ".join(w for w in act2.split() if w not in negations)
                if has_neg1 != has_neg2 and _jaccard_tokens(act1_clean, act2_clean) > 0.5:
                    return True
        return False

    async def evaluate_candidate(self, candidate_id: str) -> str:
        """Run real evaluation. CANDIDATE -> EVALUATING -> PROMOTABLE / REJECTED"""
        if candidate_id not in self._candidates:
            return "not_found"
            
        cand = self._candidates[candidate_id]
        if cand["status"] != CandidateState.CANDIDATE.value:
            return f"rejected: invalid_state ({cand['status']})"

        if not self.evaluator:
            # Production fail-closed: without a real evaluator, reject evaluation!
            self._change_state(cand, CandidateState.REJECTED, "no evaluator provided")
            return "rejected: no_evaluator"

        self._change_state(cand, CandidateState.EVALUATING)

        # Register an IMMUTABLE per-request evaluation snapshot (isolation):
        # the candidate lesson is delivered only to the eval run that carries
        # this evaluation_id — never written to the global ACTIVE collection.
        evaluation_id = uuid.uuid4().hex
        register_eval_context(
            evaluation_id,
            str(cand.get("id", "")),
            str(cand.get("task_id", "")),
            f"{cand.get('trigger', '')}: {cand.get('action', '')}",
        )
        cand["evaluation_id"] = evaluation_id
        self._save_candidates()

        try:
            # evaluator must return dict with explicit PASS metric
            eval_res = await self.evaluator(cand)
            if not eval_res or not isinstance(eval_res, dict):
                self._change_state(cand, CandidateState.EVALUATION_FAILED, "invalid evaluation result format")
                return "rejected: invalid_evaluation_result"
        except asyncio.TimeoutError:
            self._change_state(cand, CandidateState.EVALUATION_FAILED, "evaluator timeout")
            return "rejected: evaluation_timeout"
        except Exception as e:
            self._change_state(cand, CandidateState.EVALUATION_FAILED, f"evaluator exception: {e}")
            return f"rejected: evaluation_exception ({e})"
        finally:
            # The snapshot is scoped to this evaluation request only.
            clear_eval_context(evaluation_id)
            
        # 9. EVALUATION MUST PROTECT AGAINST OVERFITTING
        if not eval_res.get("pass") or eval_res.get("unrelated_regression"):
            self._change_state(cand, CandidateState.EVALUATION_FAILED, "did not pass evaluation constraints")
            return "rejected: evaluation_failed"

        # FAIL CLOSED without a trusted signing authority: never mint a receipt
        # that could be forged because no key is provisioned.
        if _receipt_key() is None:
            self._change_state(
                cand,
                CandidateState.EVALUATION_FAILED,
                "no trusted signing authority (SWARM_RECEIPT_KEY unset)",
            )
            return "rejected: no_signing_authority"

        # Issue a TRUSTED receipt bound to the COMPLETE candidate state. The
        # HMAC is keyed outside candidate state, so a candidate-JSON editor can
        # compute the public state hash but cannot forge this signature.
        state_hash = self._hash_candidate(cand)
        receipt = {
            "eval_id": uuid.uuid4().hex,
            "candidate_id": str(cand.get("id", "")),
            "hypothesis_id": str(cand.get("id", "")),
            "state_hash": state_hash,
            "governance_version": GOVERNANCE_VERSION,
            "evaluator_id": EVALUATOR_ID,
            "evaluator_version": EVALUATOR_VERSION,
            "evaluation_task_id": str(getattr(self.evaluator, "task_id", "")),
            "evaluation_id": evaluation_id,
            "harness": eval_res.get("harness") if isinstance(eval_res.get("harness"), dict) else None,
            "baseline_verdict": (eval_res.get("baseline") or {}).get("verdict") if isinstance(eval_res.get("baseline"), dict) else None,
            "candidate_verdict": (eval_res.get("candidate") or {}).get("verdict") if isinstance(eval_res.get("candidate"), dict) else None,
            "pass": True,
        }
        eval_res["receipt"] = receipt
        eval_res["receipt_sig"] = self._sign_receipt(receipt)
        eval_res["rule_hash"] = state_hash
        eval_res["governance_version"] = GOVERNANCE_VERSION
        cand["eval_result"] = eval_res
        self._change_state(cand, CandidateState.PROMOTABLE, "evaluation passed")
        return "evaluation_passed"

    async def promote(self, candidate_id: str) -> str:
        """Promotion Gate: PROMOTABLE -> ACTIVE. Needs Proof Object."""
        if candidate_id not in self._candidates:
            return "not_found"
            
        cand = self._candidates[candidate_id]
        
        # State machine check
        if cand["status"] != CandidateState.PROMOTABLE.value:
            return f"rejected: invalid_state ({cand['status']})"

        # Attack 1/2/3/9 Fix: Final Gate invariant verification (Do not trust state alone)
        unique_runs = set()
        for ev in cand.get("evidence_runs", []):
            if isinstance(ev, dict):
                if ev.get("hypothesis") != cand["action"]:
                    self._change_state(cand, CandidateState.REJECTED, "mixed hypothesis in evidence runs")
                    return "rejected: mixed_hypothesis"
                unique_runs.add(ev.get("run_id"))
            else:
                self._change_state(cand, CandidateState.REJECTED, "legacy or unbounded evidence run")
                return "rejected: invalid_evidence_format"

        if len(unique_runs) < MIN_EVIDENCE_RUNS:
            self._change_state(cand, CandidateState.REJECTED, "failed final gate: insufficient unique evidence runs")
            return "rejected: insufficient_evidence"

        # Genuine task diversity — three runs of ONE task are not independent.
        distinct_tasks = {str(t) for t in cand.get("evidence_tasks", []) if t}
        if len(distinct_tasks) < MIN_EVIDENCE_TASKS:
            self._change_state(
                cand,
                CandidateState.REJECTED,
                f"insufficient task diversity ({len(distinct_tasks)} < {MIN_EVIDENCE_TASKS})",
            )
            return "rejected: insufficient_task_diversity"

        eval_res = cand.get("eval_result", {})
        if not eval_res.get("pass"):
            self._change_state(cand, CandidateState.REJECTED, "missing explicit pass in eval_result")
            return "rejected: missing_evaluation"

        # Verify the TRUSTED receipt: signature + evaluator identity + binding
        # to the CURRENT complete candidate state. A forged/missing/unsigned
        # receipt, or any post-evaluation mutation, is rejected here.
        receipt = eval_res.get("receipt")
        sig = eval_res.get("receipt_sig")
        if not isinstance(receipt, dict) or not self._verify_receipt(receipt, sig):
            self._change_state(cand, CandidateState.REJECTED, "missing or unauthenticated evaluation receipt")
            return "rejected: forged_or_mutated_evaluation"
        if receipt.get("evaluator_id") != EVALUATOR_ID or str(receipt.get("evaluator_version")) != EVALUATOR_VERSION:
            self._change_state(cand, CandidateState.REJECTED, "unauthorized evaluator identity")
            return "rejected: unauthorized_evaluator"
        if receipt.get("state_hash") != self._hash_candidate(cand):
            self._change_state(cand, CandidateState.REJECTED, "receipt does not bind current candidate state")
            return "rejected: forged_or_mutated_evaluation"
        if int(receipt.get("governance_version", 0)) != GOVERNANCE_VERSION:
            self._change_state(cand, CandidateState.REJECTED, "receipt governance version mismatch")
            return "rejected: stale_evaluation"
            
        if int(eval_res.get("governance_version", 0)) != GOVERNANCE_VERSION:
            self._change_state(cand, CandidateState.REJECTED, "evaluation was run under an older governance version")
            return "rejected: stale_evaluation"

        # Governance-version compatibility of the candidate itself
        if int(cand.get("governance_version", GOVERNANCE_VERSION)) != GOVERNANCE_VERSION:
            self._change_state(cand, CandidateState.REJECTED, "governance version mismatch")
            return "rejected: governance_version"

        rule_text = f"{cand['trigger']}: {cand['action']}"
        
        # Recheck safety at the final gate!
        if not is_safe_lesson(rule_text):
            self._change_state(cand, CandidateState.REJECTED, "candidate failed safety check at promotion")
            return "rejected: unsafe_lesson"
            
        tokens = estimate_tokens(rule_text)

        # Per-rule hard cap (governance invariant #1)
        if tokens > MAX_RULE_TOKENS:
            self._change_state(cand, CandidateState.REJECTED, f"exceeds MAX_RULE_TOKENS={MAX_RULE_TOKENS}")
            return "rejected: token_limit"

        # Check contradictions + duplicates deterministically against ALL active lessons
        existing_lessons = await self.lesson_manager.get_all()
        active = [l for l in existing_lessons if not l.superseded_by]

        for l in active:
            if self._is_contradiction_v1(rule_text, l.rule) or _is_contradiction(rule_text, l.rule):
                self._change_state(cand, CandidateState.CONTRADICTED, f"conflicts with {l.id}")
                return "rejected: contradiction"
        for l in active:
            if _jaccard_tokens(rule_text, l.rule) >= 0.85:
                # Same behavioral meaning already active → this is a duplicate,
                # not a second rule.
                self._change_state(cand, CandidateState.SUPERSEDED, f"duplicate of active lesson {l.id}")
                return "rejected: duplicate_lesson"

        # Check total token budget if added (governance invariant #2)
        total_active_tokens = sum(estimate_tokens(l.rule) for l in active)
        if total_active_tokens + tokens > MAX_ACTIVE_TOKENS:
            # We don't prune here to be safe, we reject
            self._change_state(cand, CandidateState.REJECTED, "adding this would exceed max active token budget")
            return "rejected: global_token_limit"

        # PROOF OBJECT
        proof = PromotionProof(
            candidate_id=candidate_id,
            hypothesis_id=cand["id"],
            evidence_ids=[e["run_id"] if isinstance(e, dict) else e for e in cand["evidence_runs"]],
            unique_run_ids=list(unique_runs),
            task_ids=cand.get("evidence_tasks", []),
            evaluation_id=uuid.uuid4().hex,
            evaluation_status="PASS",
            evaluation_metrics=eval_res,
            baseline_metrics=eval_res.get("baseline", {}),
            candidate_metrics=eval_res.get("candidate", {}),
            token_count=tokens,
            conflict_check="PASSED",
            governance_version=GOVERNANCE_VERSION,
            timestamp=time.time()
        )
        cand["promotion_proof"] = asdict(proof)

        # ATOMIC PROMOTION & ROLLBACK SUPPORT — journaled phases so a process
        # crash between persistence steps is recovered at startup, not only
        # rolled back by the in-process except path.
        snapshot_id = uuid.uuid4().hex[:12]
        self._snapshots[snapshot_id] = {
            "timestamp": time.time(),
            "candidate_id": candidate_id,
            "previous_active_lessons": [
                {
                    "id": l.id,
                    "rule": l.rule,
                    "confidence": l.confidence,
                    "effectiveness": l.effectiveness,
                    "source_candidates": l.source_candidates,
                    "version": l.version,
                    "created_at": l.created_at,
                    "last_verified_at": l.last_verified_at,
                    "superseded_by": l.superseded_by
                } for l in active
            ]
        }

        try:
            self._journal_append("begin", candidate_id)
            self._save_snapshots()
            self._journal_append("snapshot_saved", candidate_id)
        except Exception as e:
            self._change_state(cand, CandidateState.REJECTED, f"failed to persist snapshot: {e}")
            return f"rejected: snapshot_failed ({e})"

        # Store the new active lesson
        new_lesson = ActiveLesson(
            rule=rule_text,
            confidence=0.85,
            effectiveness=float(eval_res.get("effectiveness", 0.5)),
            source_candidates=[cand["id"]],
        )

        lid = None
        try:
            lid = await self.lesson_manager.store(new_lesson)
            self._journal_append("qdrant_applied", candidate_id, lid or "")
            cand["active_lesson_id"] = lid
            cand["snapshot_id"] = snapshot_id

            # Attack 13/14 Fix: If this step fails, Qdrant is already modified. We MUST catch it.
            self._change_state(cand, CandidateState.ACTIVE, "atomic promotion successful")
            self._journal_append("committed", candidate_id, lid or "")
            self._audit("PROMOTED", cand["promotion_proof"])
            return "promoted"
        except Exception as e:
            # If Qdrant succeeded but JSON write/audit failed, we MUST rollback Qdrant manually!
            if lid:
                try:
                    await self.lesson_manager.remove(lid)
                except Exception:
                    pass # Best effort rollback
            self._journal_append("rolled_back", candidate_id, lid or "")

            cand["status"] = CandidateState.PROMOTABLE.value # revert local state
            try:
                self._change_state(cand, CandidateState.REJECTED, f"promotion persistence failed: {e}")
                self._audit("PROMOTION_FAILED", {"error": str(e)})
            except Exception:
                pass
            return f"rejected: promotion_failed ({e})"

    async def rollback(self, snapshot_id: str) -> bool:
        """Restore the exact previous prompt state using a snapshot."""
        if snapshot_id not in self._snapshots:
            return False
            
        snapshot = self._snapshots[snapshot_id]
        prev_lessons = snapshot["previous_active_lessons"]
        
        # Remove ALL current active lessons
        current = await self.lesson_manager.get_all()
        for l in current:
            await self.lesson_manager.remove(l.id)
            
        # Restore old ones exactly
        for pl in prev_lessons:
            al = ActiveLesson(
                id=pl["id"],
                rule=pl["rule"],
                confidence=pl["confidence"],
                effectiveness=pl["effectiveness"],
                source_candidates=pl.get("source_candidates", []),
                version=pl.get("version", 1),
                created_at=pl.get("created_at", ""),
                last_verified_at=pl.get("last_verified_at", ""),
                superseded_by=pl.get("superseded_by")
            )
            await self.lesson_manager.store(al)
            
        # Mark candidate as rolled back
        cid = snapshot["candidate_id"]
        if cid in self._candidates:
            self._change_state(self._candidates[cid], CandidateState.RETIRED, "rolled back")
            
        self._audit("ROLLBACK", {"snapshot_id": snapshot_id})
        return True

_repairer_instance = None

def get_prompt_repairer() -> "PromptRepairer":
    global _repairer_instance
    if _repairer_instance is None:
        _repairer_instance = PromptRepairer()
    return _repairer_instance

