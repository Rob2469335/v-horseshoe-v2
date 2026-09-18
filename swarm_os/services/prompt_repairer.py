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
import uuid
import time
import asyncio
from enum import Enum
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Any

from swarm_os.healing.diagnostician import Diagnostician
from swarm_os.services.lesson_manager import (
    ActiveLesson,
    MAX_ACTIVE_TOKENS,
    MAX_RULE_TOKENS,
    _is_contradiction,
    _jaccard_tokens,
    estimate_tokens,
    get_lesson_manager,
)
from swarm_os.lib.atomic_io import atomic_write_text

GOVERNANCE_VERSION = 1
# Default evidence governance: at least 3 INDEPENDENT trajectories (unique run
# ids) are required before a candidate may even be created. Configurable, but
# the default is deliberately strict — one or two failures never promote.
MIN_EVIDENCE_RUNS = 3

_REPO_ROOT = Path(__file__).parent.parent.parent
_DATA_DIR = _REPO_ROOT / "data"
_CANDIDATES_FILE = _DATA_DIR / "prompt_repairer_candidates.json"
_SNAPSHOTS_FILE = _DATA_DIR / "prompt_repairer_snapshots.json"
_AUDIT_LOG_FILE = _DATA_DIR / "prompt_repairer_audit.jsonl"


def _journal_file() -> Path:
    """Transaction journal for crash-safe promotion. Derived from _DATA_DIR at
    call time so tests that redirect _DATA_DIR also isolate the journal."""
    return _DATA_DIR / "prompt_repairer_journal.jsonl"


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
    def __init__(self, task_id: str = "c01", timeout: int = 60):
        self.task_id = task_id
        self.timeout = timeout

    async def __call__(self, candidate: dict) -> dict:
        try:
            from qwen_train.run_curriculum import load_items, _attempt_once
        except ImportError:
            raise RuntimeError("qwen_train module not available for evaluation")

        items = load_items()
        item = next((i for i in items if i.get("id") == self.task_id), None)
        if not item:
            raise ValueError(f"Task {self.task_id} not found in curriculum")

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

    def _hash_candidate(self, cand: dict) -> str:
        """Deterministically hash the governed content of a candidate."""
        import hashlib
        content = f"{cand.get('id', '')}:{cand.get('trigger', '')}:{cand.get('action', '')}:{GOVERNANCE_VERSION}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

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

    def process_failure(self, run_id: str, component: str, failure_reason: str, hypothesized_action: str, task_id: str = "") -> str:
        """Process a failure (OBSERVED -> EVIDENCE_GATHERING)."""
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
                "evidence_runs": [{"run_id": run_id, "hypothesis": hypothesized_action}],
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
            
            # Independence enforcement
            existing_runs = [e["run_id"] if isinstance(e, dict) else e for e in cand["evidence_runs"]]
            if run_id in existing_runs:
                return "ignored: duplicate_run"
                
            cand["evidence_runs"].append({"run_id": run_id, "hypothesis": hypothesized_action})
            if task_id and task_id not in cand["evidence_tasks"]:
                cand["evidence_tasks"].append(task_id)

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
            
        # 9. EVALUATION MUST PROTECT AGAINST OVERFITTING
        if not eval_res.get("pass") or eval_res.get("unrelated_regression"):
            self._change_state(cand, CandidateState.EVALUATION_FAILED, "did not pass evaluation constraints")
            return "rejected: evaluation_failed"

        # Explicit PROMOTABLE state
        eval_res["rule_hash"] = self._hash_candidate(cand)
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

        eval_res = cand.get("eval_result", {})
        if not eval_res.get("pass"):
            self._change_state(cand, CandidateState.REJECTED, "missing explicit pass in eval_result")
            return "rejected: missing_evaluation"

        # Verify evaluation receipt identity and freshness
        current_hash = self._hash_candidate(cand)
        if eval_res.get("rule_hash") != current_hash:
            self._change_state(cand, CandidateState.REJECTED, "rule hash mismatch - candidate mutated after evaluation")
            return "rejected: forged_or_mutated_evaluation"
            
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

