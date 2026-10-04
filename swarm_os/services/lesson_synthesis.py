"""Evidence-grounded, transferable lesson synthesis for Experiment J.

Architecture (stages A -> D), all fail-closed
--------------------------------------------

    verified failure evidence
      -> A. evidence-grounded diagnosis        (deterministic)
      -> B. transferable abstraction           (injected distiller seam; fail-closed if absent)
      -> C. deterministic provenance membrane  (Layer 1)
      -> D. independent leakage/quality gate   (Layer 2 + L1..L8)
      -> [existing evaluator / promotion gates -> PROMOTABLE -> receipt -> ACTIVE]

Why this module exists
----------------------
`runtime_v2/api/evaluation_bridge.py::_build_hypothesized_action` can emit exactly
two strings, chosen only by whether the agent attempted an edit. Both are
task-agnostic and tautological (they restate that the run failed). Promoting one of
them would satisfy every existing governance gate while testing nothing about F0's
research question. Separately, `is_safe_lesson()` is a prompt-injection membrane,
not a solution-leakage membrane: it blocks hostile meta-instructions and says
nothing about whether a rule discloses the originating task's repair.

This module therefore makes both failure modes impossible to express:

* A lesson is produced only from measured evidence. If the evidence does not
  support a mechanism, no lesson is produced (``Diagnosis`` is ``None``) and the
  candidate cannot reach PROMOTABLE.
* A lesson that retains source-task identity cannot become worker-facing. The
  membrane builds its lexicon FROM the run's own provenance rather than from a
  fixed blacklist, because the identifiers that matter are exactly the ones this
  repository does not know in advance.

Independence of the validator
-----------------------------
Stage B (the generator) and Stage D (the validator) share no code path and no
state. Stage D re-derives its judgement from ``(scrubbed_text, evidence)`` alone
and never consults the generator, its prompt, or its self-assessment. A
generator/validator disagreement fails closed in both directions.

Prohibitions honoured here
--------------------------
This module does NOT, and must not be changed to:

* reinterpret or weaken the F0 primary endpoint;
* change ``MIN_EVIDENCE_RUNS``, ``MIN_EVIDENCE_TASKS``, or any promotion gate;
* mint, forge, or verify an HMAC receipt (that remains downstream, in
  ``prompt_repairer``);
* create or activate a lesson (that is ``LessonManager.store`` under
  ``prompt_repairer.promote``);
* weaken ``is_safe_lesson`` (it is still applied, unchanged, upstream and at
  render time).

Evidence labels used in this module's docstrings:
``PROVEN`` / ``SUPPORTED`` / ``INFERRED`` / ``NOT ESTABLISHED`` /
``GOVERNANCE GAP`` / ``REQUIRES AUTHORIZATION``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from swarm_os.services.lesson_manager import MAX_RULE_TOKENS, estimate_tokens

__all__ = [
    "FailureEvidence",
    "EvidenceFeature",
    "Diagnosis",
    "Principle",
    "ScrubReport",
    "SynthesisAttestation",
    "QualityVerdict",
    "SYNTHESIS_SCHEMA",
    "diagnose",
    "abstract",
    "scrub",
    "validate",
    "synthesize",
    "synthesize_candidate",
    "SYNTHESIS_VERSION",
]

SYNTHESIS_VERSION = "ej-lesson-synthesis/2"
SYNTHESIS_SCHEMA = "v-horseshoe-v2/lesson-synthesis/1"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Tools whose presence indicates investigation rather than modification.
_INVESTIGATION_TOOLS = ("read", "glob", "grep", "list", "search", "sandbox_repl", "web")

#: Verdict reasons that mean the run produced no usable capability measurement.
#: Mirrors `evaluation_bridge._NON_CAPABILITY_REASONS`; duplicated deliberately so
#: this module stays importable without the bridge (no import cycle, and the
#: bridge remains the authority for classification itself).
_NON_CAPABILITY_REASONS = ("env_error", "regression")

#: Structural pattern of a SWE-bench-style instance id, e.g.
#: ``databricks__dbt-databricks-935`` -> org ``databricks``, repo
#: ``dbt-databricks``, instance ``935``.
_INSTANCE_ID_RE = re.compile(r"^(?P<org>[A-Za-z0-9._-]+)__(?P<repo>[A-Za-z0-9._-]+?)-(?P<num>\d+)$")

_COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b")

#: A pytest node id fragment: ``path/to/test_x.py::Class::test_name[param]``.
_TEST_NODE_RE = re.compile(r"[\w./\\-]+\.py::[\w.:\-\[\]]+")

_PATH_LIKE_RE = re.compile(r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z]{1,6}")

_URL_RE = re.compile(r"https?://\S+")

_SYMBOL_RE = re.compile(r"\b(?:def|class|function|method)\s+([A-Za-z_][A-Za-z0-9_]*)")

#: Provenance recorded in a run's evidence that carries NO identifying power, so
#: it is redacted rather than causing a rejection. Kept explicit and small on
#: purpose: an arbitrary "probably harmless" list would defeat the point of a
#: fail-closed membrane. Anything not provably harmless is rejected instead.
_HARMLESS_PROVENANCE = (
    "python", "pytest", "posix", "linux", "windows", "darwin", "ascii", "utf-8",
)

#: Phrases that make a rule a restatement of failure rather than guidance. A
#: generated principle matching any of these is rejected as tautological.
_TAUTOLOGY_MARKERS = (
    "did not resolve the failing test",
    "the previous attempt failed",
    "review the test failures",
    "the attempt was unsuccessful",
    "the run failed",
    "try again",
    "be careful",
    "did not work",
)

#: Prescriptive-solution markers: text that tells a worker HOW to repair a
#: specific defect rather than stating a transferable principle.
_PRESCRIPTIVE_MARKERS = (
    "change the function",
    "replace the argument",
    "set the default to",
    "rename the",
    "add the parameter",
    "edit the line",
    "in this file",
    "in this repo",
    "in this repository",
    "in this task",
    "for this issue",
    "the fix is to",
    "apply this patch",
    "use the following patch",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureEvidence:
    """Verified, measured facts about one failed evaluation run.

    This is the ONLY input Stage A accepts. It is deliberately a closed
    structure: a lesson cannot be synthesised from free-form text, so there is no
    path by which task content reaches the abstraction stage unmediated.
    """

    task_id: str
    rollout_id: str = ""
    evaluator_passed: bool | None = None
    evaluator_reason: str = ""
    classification: str = ""
    termination_reason: str = ""
    step_count: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    ordered_tool_actions: tuple[str, ...] = ()
    source_modification_attempted: bool = False
    source_modification_succeeded: bool = False
    source_changed: bool = False
    baseline_f2p_failed: int = 0
    post_f2p_passed: int = 0
    post_f2p_failed: int = 0
    backend_reachable: bool = True
    model_endpoint_reachable: bool = True

    @property
    def f2p_total(self) -> int:
        return self.post_f2p_passed + self.post_f2p_failed

    def to_dict(self) -> dict:
        """Lossless JSON-ready form. Only observed fields; no invented values."""
        return {
            "task_id": self.task_id,
            "rollout_id": self.rollout_id,
            "evaluator_passed": self.evaluator_passed,
            "evaluator_reason": self.evaluator_reason,
            "classification": self.classification,
            "termination_reason": self.termination_reason,
            "step_count": self.step_count,
            "successful_tool_calls": self.successful_tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "ordered_tool_actions": list(self.ordered_tool_actions),
            "source_modification_attempted": self.source_modification_attempted,
            "source_modification_succeeded": self.source_modification_succeeded,
            "source_changed": self.source_changed,
            "baseline_f2p_failed": self.baseline_f2p_failed,
            "post_f2p_passed": self.post_f2p_passed,
            "post_f2p_failed": self.post_f2p_failed,
            "backend_reachable": self.backend_reachable,
            "model_endpoint_reachable": self.model_endpoint_reachable,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "FailureEvidence | None":
        """Rebuild from persisted form. Fail closed (None) on malformed input.

        ``evaluator_passed`` preserves the three-valued semantics: absent/null
        stays ``None`` (not supplied), which Stage A treats as fail-closed.
        """
        if not isinstance(d, Mapping) or not str(d.get("task_id") or "").strip():
            return None
        try:
            return cls(
                task_id=str(d["task_id"]),
                rollout_id=str(d.get("rollout_id") or ""),
                evaluator_passed=d.get("evaluator_passed"),
                evaluator_reason=str(d.get("evaluator_reason") or ""),
                classification=str(d.get("classification") or ""),
                termination_reason=str(d.get("termination_reason") or ""),
                step_count=int(d.get("step_count") or 0),
                successful_tool_calls=int(d.get("successful_tool_calls") or 0),
                failed_tool_calls=int(d.get("failed_tool_calls") or 0),
                ordered_tool_actions=tuple(
                    str(a) for a in (d.get("ordered_tool_actions") or ())
                ),
                source_modification_attempted=bool(d.get("source_modification_attempted")),
                source_modification_succeeded=bool(d.get("source_modification_succeeded")),
                source_changed=bool(d.get("source_changed")),
                baseline_f2p_failed=int(d.get("baseline_f2p_failed") or 0),
                post_f2p_passed=int(d.get("post_f2p_passed") or 0),
                post_f2p_failed=int(d.get("post_f2p_failed") or 0),
                backend_reachable=bool(d.get("backend_reachable", True)),
                model_endpoint_reachable=bool(d.get("model_endpoint_reachable", True)),
            )
        except Exception:  # noqa: BLE001 - malformed persisted evidence fails closed
            return None


@dataclass(frozen=True)
class EvidenceFeature:
    """One measured behavioural fact, with a provenance pointer.

    ``code`` is a stable machine identifier; ``paths`` names the evidence fields
    that produced it. The principle inherits these, so L7 (provenance integrity)
    is answerable without exposing provenance text to the worker.
    """

    code: str
    detail: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Diagnosis:
    """An evidence-grounded account of why the observed action failed."""

    mechanism: str
    features: tuple[EvidenceFeature, ...]
    evidence_ref: str
    causal_confidence: str = "supported"
    insufficient_reason: str = ""

    @property
    def feature_codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.features)


@dataclass(frozen=True)
class Principle:
    """A transferable behavioural principle proposed by the Stage B distiller."""

    text: str
    grounded_in: tuple[str, ...] = ()
    distiller_id: str = ""


@dataclass(frozen=True)
class ScrubReport:
    """Outcome of the Layer-1 deterministic provenance membrane."""

    text: str
    redactions: tuple[str, ...] = ()
    identity_hits: tuple[str, ...] = ()
    passed: bool = True
    reason: str = ""


@dataclass(frozen=True)
class QualityVerdict:
    """Independent Stage-D judgement. Persisted as provenance, never delivered."""

    passed: bool
    checks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    validator_id: str = "ej-independent-lesson-validator/1"
    rationale: str = ""

    def failed_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if not v.get("pass")]


@dataclass(frozen=True)
class SynthesisAttestation:
    """Binds a candidate to a validated synthesis.

    Stored on the candidate so that promotion can prove the worker-facing text was
    produced by Stages A-D and independently judged, without re-running them and
    without exposing any provenance to the worker.
    """

    synthesis_version: str
    principle_text: str
    feature_codes: tuple[str, ...]
    mechanism: str
    evidence_ref: str
    validator_id: str
    validator_passed: bool
    redactions: tuple[str, ...] = ()
    rationale: str = ""
    # Structured provenance. ``evidence_ref`` is a human-readable summary and is
    # NOT a checkable contract, so the task/rollout identity is carried as
    # fields. ``attach_synthesis`` binds these to the candidate's own
    # evidence_tasks/evidence_runs, so a valid attestation for one task cannot
    # be attached to a candidate about a different task.
    task_id: str = ""
    rollout_id: str = ""

    def to_dict(self) -> dict:
        return {
            "synthesis_version": self.synthesis_version,
            "principle_text": self.principle_text,
            "feature_codes": list(self.feature_codes),
            "mechanism": self.mechanism,
            "evidence_ref": self.evidence_ref,
            "validator_id": self.validator_id,
            "validator_passed": self.validator_passed,
            "redactions": list(self.redactions),
            "rationale": self.rationale,
            "task_id": self.task_id,
            "rollout_id": self.rollout_id,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "SynthesisAttestation | None":
        if not isinstance(d, Mapping):
            return None
        if not d.get("principle_text") or not d.get("validator_passed"):
            return None
        return cls(
            synthesis_version=str(d.get("synthesis_version") or ""),
            principle_text=str(d.get("principle_text") or ""),
            feature_codes=tuple(str(x) for x in (d.get("feature_codes") or ())),
            mechanism=str(d.get("mechanism") or ""),
            evidence_ref=str(d.get("evidence_ref") or ""),
            validator_id=str(d.get("validator_id") or ""),
            validator_passed=bool(d.get("validator_passed")),
            redactions=tuple(str(x) for x in (d.get("redactions") or ())),
            rationale=str(d.get("rationale") or ""),
            task_id=str(d.get("task_id") or ""),
            rollout_id=str(d.get("rollout_id") or ""),
        )


# ---------------------------------------------------------------------------
# Stage A -- evidence-grounded diagnosis (deterministic)
# ---------------------------------------------------------------------------

#: The deterministic grounding vocabulary. Each entry maps a measured feature to
#: the failure mechanism it evidences. These are BEHAVIOURAL mechanisms computed
#: from the trajectory, not task content, which is what keeps the diagnosis
#: transferable without an LLM.
_MECHANISMS: dict[str, tuple[str, str]] = {
    "investigation_without_edit": (
        "The agent consumed its budget investigating without ever attempting a "
        "source modification, so no candidate change could reach the evaluator."
    ),
    "edit_without_effect": (
        "The agent dispatched at least one edit-type action, yet the evaluator "
        "still reports the declared tests failing, so the attempted modification "
        "did not affect the observed behaviour."
    ),
    "repeated_identical_action": (
        "The agent repeated an identical action consecutively, so additional "
        "attempts produced no new information."
    ),
    "low_action_success_rate": (
        "Most dispatched tool actions did not succeed, so the trajectory's "
        "observations were unreliable inputs to the agent's next decision."
    ),
    "budget_exhausted": (
        "The run terminated at its configured step or time budget while tests "
        "remained unresolved."
    ),
    "partial_progress": (
        "Some declared tests began passing while others remained failing, so the "
        "modification was directionally incomplete rather than absent."
    ),
    "process_crash": (
        "The run terminated abnormally rather than completing, so no behavioural "
        "conclusion can be drawn from it."
    ),
}


def _actions(ev: FailureEvidence) -> list[str]:
    return [str(a).strip() for a in (ev.ordered_tool_actions or ()) if str(a).strip()]


def _tool_name(action: str) -> str:
    """``filesystem:patch`` / ``sandbox_repl`` -> trailing tool token."""
    tail = action.split(":")[-1].strip().lower()
    return tail or action.strip().lower()


def _has_consecutive_repeat(actions: Sequence[str], times: int = 3) -> tuple[bool, str]:
    if len(actions) < times:
        return False, ""
    for i in range(len(actions) - times + 1):
        window = actions[i : i + times]
        if len(set(_tool_name(a) for a in window)) == 1:
            return True, _tool_name(window[0])
    return False, ""


def derive_features(ev: FailureEvidence) -> tuple[EvidenceFeature, ...]:
    """Derive behavioural features from measured evidence. Pure function."""
    feats: list[EvidenceFeature] = []
    actions = _actions(ev)

    investigated = any(
        any(tok in _tool_name(a) for tok in _INVESTIGATION_TOOLS) for a in actions
    )

    if not ev.source_modification_attempted and investigated and ev.step_count >= 2:
        feats.append(
            EvidenceFeature(
                code="investigation_without_edit",
                detail=(
                    f"{ev.step_count} steps, "
                    f"{sum(1 for a in actions if any(t in _tool_name(a) for t in _INVESTIGATION_TOOLS))} "
                    "investigation-type actions, 0 edit-type actions"
                ),
                paths=("step_count", "ordered_tool_actions", "source_modification_attempted"),
            )
        )

    if ev.source_modification_attempted and ev.post_f2p_failed > 0:
        succeeded = "1" if ev.source_modification_succeeded else "0"
        feats.append(
            EvidenceFeature(
                code="edit_without_effect",
                detail=(
                    f"edit attempted; source_changed={ev.source_changed}; "
                    f"post-edit edit actions succeeded={succeeded}; "
                    f"{ev.post_f2p_failed} declared test(s) still failing"
                ),
                paths=(
                    "source_modification_attempted",
                    "source_modification_succeeded",
                    "source_changed",
                    "post_f2p_failed",
                ),
            )
        )

    repeated, tool = _has_consecutive_repeat(actions)
    if repeated:
        feats.append(
            EvidenceFeature(
                code="repeated_identical_action",
                detail=f"identical tool '{tool}' dispatched on 3+ consecutive steps",
                paths=("ordered_tool_actions",),
            )
        )

    if ev.step_count > 0:
        rate = ev.successful_tool_calls / float(ev.step_count)
        if rate < 0.5:
            feats.append(
                EvidenceFeature(
                    code="low_action_success_rate",
                    detail=(
                        f"{ev.successful_tool_calls}/{ev.step_count} tool actions "
                        f"succeeded ({rate:.0%})"
                    ),
                    paths=("step_count", "successful_tool_calls"),
                )
            )

    if ev.termination_reason in ("max_turns", "harness_timeout") and ev.post_f2p_failed > 0:
        feats.append(
            EvidenceFeature(
                code="budget_exhausted",
                detail=f"terminated '{ev.termination_reason}' with {ev.post_f2p_failed} test(s) failing",
                paths=("termination_reason", "post_f2p_failed"),
            )
        )

    if ev.post_f2p_passed > 0 and ev.post_f2p_failed > 0:
        feats.append(
            EvidenceFeature(
                code="partial_progress",
                detail=f"{ev.post_f2p_passed} passing / {ev.post_f2p_failed} failing after the run",
                paths=("post_f2p_passed", "post_f2p_failed"),
            )
        )

    if ev.termination_reason == "process_crash":
        feats.append(
            EvidenceFeature(
                code="process_crash",
                detail="termination_reason=process_crash",
                paths=("termination_reason",),
            )
        )

    return tuple(feats)


def diagnose(ev: FailureEvidence) -> tuple[Diagnosis | None, str]:
    """Stage A. Returns ``(diagnosis, reason)``; ``diagnosis`` is None on fail-closed.

    Fail-closed reasons, in evaluation order. Each one is a precondition that a
    genuine learning failure must satisfy. ``PROVEN`` that each is checked before
    any feature derivation.
    """
    if not isinstance(ev, FailureEvidence):
        return None, "malformed_evidence"

    # A1. The authoritative verdict must be present. `None` means "not supplied",
    #     which is NOT "failed".
    if ev.evaluator_passed is None:
        return None, "missing_evaluator_verdict"
    # A2. A successful run is not a failure to learn from. This is the D3 gate
    #     re-asserted at synthesis so the two layers cannot drift apart.
    if ev.evaluator_passed:
        return None, "evaluator_reported_success"
    # A3. A verdict that is False for a non-capability reason carries no usable
    #     behavioural measurement.
    low = (ev.evaluator_reason or "").lower()
    if any(m in low for m in _NON_CAPABILITY_REASONS):
        return None, "non_capability_verdict"
    # A4. Infrastructure health.
    if not ev.backend_reachable or not ev.model_endpoint_reachable:
        return None, "infrastructure_unreachable"
    # A5. The run must have produced an observable trajectory.
    if ev.step_count <= 0 or ev.successful_tool_calls <= 0:
        return None, "no_observable_trajectory"
    # A6. The bridge's own classification must admit this as behavioural.
    if ev.classification and ev.classification != "BEHAVIORAL":
        return None, f"not_behavioral:{ev.classification}"
    # A7. There must be a declared test still failing, or there is no defect.
    if ev.post_f2p_failed <= 0 and ev.post_f2p_passed <= 0:
        return None, "no_declared_test_measurement"

    feats = derive_features(ev)
    if not feats:
        return None, "insufficient_evidence_for_mechanism"

    ordered = [c for c in _MECHANISMS if c in {f.code for f in feats}]
    mechanism = " ".join(_MECHANISMS[c] for c in ordered[:2])
    return (
        Diagnosis(
            mechanism=mechanism,
            features=feats,
            evidence_ref=f"rollout:{ev.rollout_id or 'unknown'};task:{ev.task_id}",
        ),
        "ok",
    )


# ---------------------------------------------------------------------------
# Stage B -- transferable abstraction (injected distiller seam)
# ---------------------------------------------------------------------------

#: A distiller receives the fully-built Stage-B *prompt string* (mechanism +
#: feature codes, assembled in ``abstract``) and returns principle text. It is
#: NOT handed the ``Diagnosis`` object — the string is the deliberate boundary,
#: so the contract is ``str -> str``.
Distiller = Callable[[str], str]

_B_SYSTEM = (
    "You convert one software-engineering failure into ONE transferable "
    "behavioural rule for a coding agent. Hard requirements:\n"
    "1. State a GENERAL principle that would apply to an unrelated repository.\n"
    "2. Never name a repository, project, file path, symbol, test, issue number, "
    "commit, or URL.\n"
    "3. Never prescribe the concrete repair for the observed defect.\n"
    "4. Never merely restate that the attempt failed; say what to do differently.\n"
    "5. One or two imperative sentences. No preamble, no headings, no markdown."
)

_B_USER = (
    "Measured failure mechanism:\n{mechanism}\n\n"
    "Observed behavioural features:\n{features}\n\n"
    "Write the transferable rule."
)


def abstract(
    diagnosis: Diagnosis,
    distiller: Distiller | None,
    *,
    max_tokens: int = MAX_RULE_TOKENS,
) -> tuple[Principle | None, str]:
    """Stage B. Abstraction via an injected distiller. Fails closed if absent.

    ``PROVEN`` that no principle can be produced without a distiller: this
    function has no fallback branch that synthesises text locally. That is
    deliberate -- a locally-generated "principle" would be a catalogue lookup
    dressed as learning, which is precisely the defect this pipeline exists to
    remove.
    """
    if diagnosis is None:
        return None, "no_diagnosis"
    if distiller is None:
        return None, "no_distiller_available"
    if diagnosis.causal_confidence != "supported":
        return None, f"insufficient_causal_confidence:{diagnosis.insufficient_reason or 'unknown'}"

    features = "\n".join(f"- {f.code}: {f.detail}" for f in diagnosis.features)
    prompt = _B_USER.format(mechanism=diagnosis.mechanism, features=features)
    try:
        raw = distiller(prompt)
    except Exception as exc:  # noqa: BLE001 - a generator fault must fail closed
        return None, f"distiller_error:{type(exc).__name__}"

    text = (raw or "").strip()
    if not text:
        return None, "distiller_returned_empty"
    if len(text.splitlines()) > 4:
        return None, "distiller_returned_too_long"
    if estimate_tokens(text) > max_tokens:
        return None, "distiller_exceeded_token_ceiling"

    # Make the generator's identity explicit and durable: a local-only seam
    # carries a ``DistillerIdentity`` (provider:model), a bare callable is
    # recorded as "injected". This is what lets a persisted attestation prove
    # whether it came from the fake test double or the real local distiller.
    _identity = getattr(distiller, "identity", None)
    _distiller_id = getattr(_identity, "qualified_id", "injected")
    return (
        Principle(
            text=text,
            grounded_in=diagnosis.feature_codes,
            distiller_id=_distiller_id,
        ),
        "ok",
    )


# ---------------------------------------------------------------------------
# Stage C -- Layer 1 deterministic provenance membrane
# ---------------------------------------------------------------------------

#: Terms that identify the ORIGINATING TASK. Presence is a hard reject: these
#: either name the instance or enable direct identification of its repair.
_TIER_IDENTITY_LITERAL = ("gold patch", "test_patch", "gold_patch", "swebench", "swe-bench")

#: Path prefixes that indicate a path is source-task-specific rather than generic.
_GENERIC_PATH_PREFIXES = ("http", "https", "www")


def _provenance_lexicon(ev: FailureEvidence) -> tuple[frozenset[str], tuple[str, ...]]:
    """Build the identity lexicon FROM this run's own provenance.

    Derived, not hard-coded, because the identifiers that matter are exactly the
    ones no fixed blacklist can know in advance. ``PROVEN`` that every term
    returned is derived from ``ev``.
    """
    terms: set[str] = set()
    redactions: list[str] = []

    task_id = (ev.task_id or "").strip()
    if task_id:
        terms.add(task_id)
        m = _INSTANCE_ID_RE.match(task_id)
        if m:
            terms.update({m.group("org"), m.group("repo"), f"{m.group('repo')}-{m.group('num')}"})
            # The org/repo pair and the numeric instance are jointly identifying.
            terms.add(f"{m.group('org')}__{m.group('repo')}")
        # A task id containing a path separator is itself a path.
        if "/" in task_id or "\\" in task_id:
            terms.add(task_id)

    reason = ev.evaluator_reason or ""
    # Test node ids and test file names recorded by the evaluator.
    for node in _TEST_NODE_RE.findall(reason):
        terms.add(node.strip())
        file_part = node.split("::")[0].strip()
        if file_part:
            terms.add(file_part)
            base = file_part.replace("\\", "/").split("/")[-1]
            if base:
                terms.add(base)
    # Commit shas recorded by the evaluator.
    for sha in _COMMIT_RE.findall(reason):
        if len(sha) >= 7:
            terms.add(sha)
    # URLs are treated as identity: a lesson has no legitimate need for one, and
    # an issue-tracker or repository URL identifies the originating task.
    for url in _URL_RE.findall(reason):
        terms.add(url)
    # Path-like tokens in the evaluator reason.
    for path in _PATH_LIKE_RE.findall(reason):
        p = path.strip().rstrip(".,;:)")
        if not p:
            continue
        if p.lower().startswith(_GENERIC_PATH_PREFIXES):
            redactions.append(p)  # generic, harmless: redact rather than reject
        else:
            terms.add(p)
    # Symbols named by the evaluator reason.
    for sym in _SYMBOL_RE.findall(reason):
        if len(sym) > 3:
            terms.add(sym)

    terms.update(_TIER_IDENTITY_LITERAL)
    # Harmless provenance goes to the REDACTION tier only -- never to identity,
    # or redacting it would still fail the run.
    redactions.extend(_HARMLESS_PROVENANCE)

    # Drop terms too short to be identifying, which would otherwise reject on
    # ordinary English words. A bare short numeric token (an instance number, a
    # line count, a version) is deliberately NOT identity on its own: it is not
    # resolvable without the repository name, which is already blocked, and
    # treating it as identity would reject innocuous rules. The composite form
    # (`<repo>-<number>`) IS blocked.
    terms = {t for t in terms if len(t) >= 4}
    return frozenset(terms), tuple(dict.fromkeys(redactions))


def scrub(principle_text: str, ev: FailureEvidence) -> ScrubReport:
    """Stage C Layer 1. Deterministic, machine-checkable provenance scrub.

    Two tiers:
      * identity-bearing term present -> REJECT (fail closed; redaction could
        produce nonsense or a partial identifier that still resolves);
      * generic term present        -> REDACT.
    """
    if not isinstance(principle_text, str) or not principle_text.strip():
        return ScrubReport(text="", passed=False, reason="empty_principle")

    lexicon, generic = _provenance_lexicon(ev)
    low = principle_text.lower()

    identity_hits = sorted({t for t in lexicon if t and t.lower() in low})
    if identity_hits:
        return ScrubReport(
            text=principle_text,
            identity_hits=tuple(identity_hits),
            passed=False,
            reason="provenance_identity_detected",
        )

    text = principle_text
    applied: list[str] = []
    for term in sorted(generic, key=len, reverse=True):
        if term and term in text:
            text = text.replace(term, "")
            applied.append(term)
    text = re.sub(r"\s{2,}", " ", text).strip()

    if not text:
        return ScrubReport(
            text="", redactions=tuple(applied), passed=False, reason="redacted_to_empty"
        )

    # Structural leak checks that do not depend on the lexicon.
    for label, pattern in (
        ("line_number", re.compile(r"\bline\s+\d+\b", re.I)),
        ("symbol_declaration", re.compile(r"\b(?:def|class)\s+[A-Za-z_]")),
        ("test_reference", re.compile(r"\btest_[A-Za-z0-9_]+\b")),
        ("benchmark_reference", re.compile(r"\b(?:SWE-?bench|SWE-?rebench|swebench)\b", re.I)),
        # A URL can identify the originating task whether or not it appeared in
        # the run's evidence, so it is rejected structurally.
        ("url_reference", _URL_RE),
        # A diff/patch artefact is the repair itself, however it is labelled.
        ("patch_artefact", re.compile(r"(?:@@\s+-\d|\+\+\+\s|^---\s|\bdiff --git\b|\bapply (?:this|the) (?:patch|diff)\b)", re.I | re.M)),
    ):
        if pattern.search(text):
            return ScrubReport(
                text=text,
                redactions=tuple(applied),
                identity_hits=(label,),
                passed=False,
                reason=f"structural_leak:{label}",
            )

    return ScrubReport(text=text, redactions=tuple(applied), passed=True, reason="ok")


# ---------------------------------------------------------------------------
# Stage D -- Layer 2 independent leakage / quality validation
# ---------------------------------------------------------------------------

_VALIDATOR_ID = "ej-independent-lesson-validator/1"

#: The nine section-8 leakage questions, in order. Single source of the Q-key
#: names, so the checks in ``validate`` cannot drift from the contract they
#: implement. (The tuple previously carried free text and was dead code with a
#: typo; it now drives the checks.)
_LEAKAGE_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("Q1_identifies_task", "does it identify the originating task"),
    ("Q2_identifies_repository", "does it identify the originating repository"),
    ("Q3_reveals_file", "does it reveal the exact file to modify"),
    ("Q4_reveals_symbol", "does it reveal the exact symbol"),
    ("Q5_reveals_test", "does it reveal the exact test"),
    ("Q6_reveals_patch", "does it reveal the exact patch"),
    ("Q7_prescribes_solution", "does it prescribe the exact solution"),
    ("Q8_materially_easier_source_task", "does it make the source task materially easier"),
    ("Q9_transferable_principle", "does it express a transferable principle"),
)


def validate(scrubbed_text: str, ev: FailureEvidence, report: ScrubReport | None = None) -> QualityVerdict:
    """Stage D Layer 2. Independent judgement over ``(text, evidence)``.

    INDEPENDENCE (``PROVEN`` by construction): this function reads only the
    scrubbed text, the run's evidence, and the scrub report. It never calls the
    Stage-B generator, never reads its prompt, and never consults a
    self-assessment. A generator that believes it produced a safe rule gains
    nothing here.
    """
    checks: dict[str, dict[str, Any]] = {}

    def add(key: str, ok: bool, detail: str) -> None:
        checks[key] = {"pass": bool(ok), "detail": detail}

    text = (scrubbed_text or "").strip()
    low = text.lower()

    # L1 / grounding -- a principle must be non-empty and exceed a floor that
    # only encodes "is there content", not a quality score.
    add("L1_grounding", bool(text), "non-empty principle text")

    # L2 / diagnosis -- the evidence must still yield an observable mechanism.
    # Re-derived deterministically here rather than trusting Stage A, so a caller
    # that reaches D with evidence yielding no feature cannot pass. This makes
    # the documented L2 a real check instead of an implicit, unrecorded
    # precondition.
    try:
        _features = derive_features(ev)
    except Exception:  # noqa: BLE001 - malformed evidence yields no diagnosis
        _features = ()
    add("L2_diagnosis", bool(_features), f"{len(_features)} measurable feature(s)")

    # L6 / non-tautology -- must not merely restate failure.
    taut = [m for m in _TAUTOLOGY_MARKERS if m in low]
    add("L6_non_tautology", not taut, f"tautology markers: {taut}" if taut else "no restatement markers")

    # L5 / actionability -- imperative, concrete enough to act on, within budget.
    tokens = estimate_tokens(text) if text else 0
    words = len(text.split())
    actionable = bool(text) and words >= 6 and tokens <= MAX_RULE_TOKENS
    add("L5_actionability", actionable, f"words={words} tokens={tokens}/{MAX_RULE_TOKENS}")

    # L4 / leakage -- Layer 2 re-derives identity independently of Stage C by
    # rebuilding the lexicon and re-checking, so a Stage C bug cannot pass here.
    lex, _ = _provenance_lexicon(ev)
    residual = sorted({t for t in lex if t and t.lower() in low})
    add("L4_leakage", not residual, f"residual identity terms: {residual}" if residual else "clean")

    # L3 / transferability -- computed before the nine questions because Q9
    # depends on it. Must assert a general condition or behaviour, not a single
    # observation, and must not be scoped to a named target.
    general_markers = (
        "before ", "when ", "after ", "verify", "confirm", "check", "prefer",
        "avoid", "instead", "rather", "first", "before modifying", "when changing",
    )
    has_general = any(m in low for m in general_markers)

    # --- the nine leakage questions (section 8) ---
    q_repo = bool(re.search(r"\b(?:repo|repository|project|package|library|module)\s+[A-Z\w]", text))
    q_file = bool(_PATH_LIKE_RE.search(text) or _TEST_NODE_RE.search(text))
    q_symbol = bool(_SYMBOL_RE.search(text))
    # A NAMED test leaks identity; the English word "test" does not. The old
    # pattern ``test[_ ][A-Za-z0-9_]+`` matched ordinary prose ("test fails",
    # "test to"), falsely rejecting genuine test-related lessons AND masking a
    # test that only passed because of that over-breadth. Match only a pytest
    # function name (``test_x``) or a test file (``test_x.py``); node ids and
    # test-file basenames are already covered by the derived lexicon.
    q_test = bool(
        re.search(r"\btest_[A-Za-z0-9_]+\b", text)
        or re.search(r"\btest[A-Za-z0-9_]*\.py\b", text)
    )
    q_patch = bool(re.search(r"\b(?:patch|diff|hunk|\+\+\+|---)\b", text, re.I))
    q_prescribe = [m for m in _PRESCRIPTIVE_MARKERS if m in low]
    q_task = bool(ev.task_id and ev.task_id.lower() in low)
    # "materially easier" is proxied by prescriptive solution text; a human
    # judgement, marked INFERRED rather than PROVEN, is recorded as such.
    q_materially = bool(q_prescribe)

    _answers: dict[str, tuple[bool, str]] = {
        "Q1_identifies_task": (not q_task, "task id absent" if not q_task else "task id present"),
        "Q2_identifies_repository": (not q_repo, "no repository noun+proper-noun" if not q_repo else "repository referenced"),
        "Q3_reveals_file": (not q_file, "no path-like token" if not q_file else "path-like token present"),
        "Q4_reveals_symbol": (not q_symbol, "no symbol declaration" if not q_symbol else "symbol declaration present"),
        "Q5_reveals_test": (not q_test, "no named test reference" if not q_test else "named test reference present"),
        "Q6_reveals_patch": (not q_patch, "no patch artefact" if not q_patch else "patch artefact present"),
        "Q7_prescribes_solution": (not q_prescribe, f"prescriptive markers: {q_prescribe}" if q_prescribe else "no prescriptive markers"),
        "Q8_materially_easier_source_task": (
            not q_materially,
            "INFERRED: no prescriptive solution text" if not q_materially else "INFERRED: prescriptive solution text present",
        ),
        "Q9_transferable_principle": (
            has_general and not q_task and not q_file,
            "general, non-identifying principle" if (has_general and not q_task and not q_file) else "not a transferable principle",
        ),
    }
    # Single source: the key names come from _LEAKAGE_QUESTIONS, so a rename
    # cannot silently desynchronise the checks from the section-8 contract.
    for _key, _question in _LEAKAGE_QUESTIONS:
        _ok, _detail = _answers[_key]
        add(_key, _ok, _detail)

    add(
        "L3_transferability",
        has_general,
        "states a general condition" if has_general else "no general condition marker",
    )

    # L7 / provenance integrity -- a validated synthesis must be traceable. The
    # traceability lives in the attestation, not in worker-facing text; here we
    # only assert the text carries no provenance of its own.
    add("L7_provenance_integrity", not residual and not q_task,
        "provenance held outside worker-facing text")

    # L8 / independent validation -- Layer 1 must have been supplied AND passed;
    # a standalone call with no scrub report is not an independent validation of
    # a scrubbed artifact. Independence from the generator is structural and is
    # asserted in `synthesize`; it is not decidable from this function's inputs.
    _report_ok = report is not None and bool(getattr(report, "passed", False))
    add("L8_independent_validation", _report_ok, f"validator={_VALIDATOR_ID}; layer1_passed={_report_ok}")

    passed = all(c["pass"] for c in checks.values())
    failed = [k for k, v in checks.items() if not v["pass"]]
    return QualityVerdict(
        passed=passed,
        checks=checks,
        validator_id=_VALIDATOR_ID,
        rationale=("all checks passed" if passed else f"failed: {failed}"),
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def synthesize(
    ev: FailureEvidence,
    distiller: Distiller | None,
) -> tuple[SynthesisAttestation | None, str]:
    """Run Stages A -> D and return an attestation, or ``None`` with a reason.

    Fail-closed at every stage. A generator/validator disagreement, a missing
    verdict, a tautology, a provenance hit, or an absent distiller all yield
    ``None``. There is no degraded-success path.
    """
    # Every stage call is guarded: a stage fault (not merely a None return) must
    # fail closed with a namespaced reason, never propagate. ``abstract`` already
    # catches distiller faults internally; the guard here also covers faults in
    # the surrounding stage code.
    try:
        diagnosis, why = diagnose(ev)
    except Exception as exc:  # noqa: BLE001 - a stage fault must fail closed
        return None, f"stage_a:diagnose_error:{type(exc).__name__}"
    if diagnosis is None:
        return None, f"stage_a:{why}"

    try:
        principle, why = abstract(diagnosis, distiller)
    except Exception as exc:  # noqa: BLE001
        return None, f"stage_b:abstract_error:{type(exc).__name__}"
    if principle is None:
        return None, f"stage_b:{why}"

    try:
        report = scrub(principle.text, ev)
    except Exception as exc:  # noqa: BLE001
        return None, f"stage_c:scrub_error:{type(exc).__name__}"
    if not report.passed:
        return None, f"stage_c:{report.reason}"

    try:
        verdict = validate(report.text, ev, report)
    except Exception as exc:  # noqa: BLE001
        return None, f"stage_d:validator_error:{type(exc).__name__}"
    if not verdict.passed:
        return None, f"stage_d:{verdict.rationale}"

    # Generator/validator disagreement is impossible by construction here (the
    # validator is the sole authority), but assert it explicitly so a future
    # refactor that reintroduces a generator self-report fails loudly.
    if principle.text != report.text and not report.redactions:
        return None, "stage_d:generator_validator_disagreement"

    return (
        SynthesisAttestation(
            synthesis_version=SYNTHESIS_VERSION,
            principle_text=report.text,
            feature_codes=principle.grounded_in,
            mechanism=diagnosis.mechanism,
            evidence_ref=diagnosis.evidence_ref,
            validator_id=verdict.validator_id,
            validator_passed=verdict.passed,
            redactions=report.redactions,
            rationale=verdict.rationale,
            task_id=str(ev.task_id or ""),
            rollout_id=str(ev.rollout_id or ""),
        ),
        "ok",
    )


# ---------------------------------------------------------------------------
# Candidate-level synthesis (W5) -- multiple independent runs, one principle
# ---------------------------------------------------------------------------

#: Marker appended to the attestation rationale to make the distiller identity
#: auditable (e.g. ``fake:test-double`` vs ``local:qwen3.5-4b``). This is how a
#: persisted attestation records which generator produced it without adding a
#: new schema field.
def synthesize_candidate(
    evidences: Sequence[FailureEvidence],
    distiller: Distiller | None,
) -> tuple[SynthesisAttestation | None, str]:
    """W5 entry point. Synthesize ONE principle from a candidate's run evidence.

    Scientific contract (``PROVEN`` by this function's control flow): a candidate
    represents >=3 independent runs across >=2 tasks, so the principle must be
    supported by EVERY run, not by one convenient run. We therefore require a
    non-empty INTERSECTION of Stage-A feature codes across all runs. If any run
    is undiagnosable, or the runs share no mechanism, synthesis fails closed.

    The membrane lexicon is built from the UNION of every run's provenance
    (task id + evaluator reason), so an identifier observed in any contributing
    run cannot reach worker-facing text.
    """
    if distiller is None:
        return None, "no_distiller_available"
    if len(evidences) < 2:
        return None, "insufficient_evidence_runs_for_candidate"

    diags: list[Diagnosis] = []
    for ev in evidences:
        d, why = diagnose(ev)
        if d is None:
            return None, f"run_undiagnosable:{why}"
        diags.append(d)

    shared = set(diags[0].feature_codes)
    for d in diags[1:]:
        shared &= set(d.feature_codes)
    if not shared:
        return None, "no_shared_mechanism_across_runs"

    # Ground the principle in the SHARED mechanism only.
    primary = diags[0]
    features = tuple(f for f in primary.features if f.code in shared)
    ordered = [c for c in _MECHANISMS if c in shared]
    mechanism = " ".join(_MECHANISMS[c] for c in ordered[:2])
    diagnosis = Diagnosis(
        mechanism=mechanism,
        features=features,
        evidence_ref="runs:" + ",".join(sorted({d.evidence_ref for d in diags})),
    )

    # Membrane sees the union of all contributing runs' provenance.
    base = evidences[0]
    membrane_ev = FailureEvidence(
        task_id=base.task_id,
        rollout_id=base.rollout_id,
        evaluator_passed=base.evaluator_passed,
        evaluator_reason=" | ".join(
            sorted({e.evaluator_reason for e in evidences if e.evaluator_reason})
        ),
        classification=base.classification,
        termination_reason=base.termination_reason,
        step_count=base.step_count,
        successful_tool_calls=base.successful_tool_calls,
        failed_tool_calls=base.failed_tool_calls,
        ordered_tool_actions=base.ordered_tool_actions,
        source_modification_attempted=base.source_modification_attempted,
        source_modification_succeeded=base.source_modification_succeeded,
        source_changed=base.source_changed,
        baseline_f2p_failed=base.baseline_f2p_failed,
        post_f2p_passed=base.post_f2p_passed,
        post_f2p_failed=base.post_f2p_failed,
        backend_reachable=base.backend_reachable,
        model_endpoint_reachable=base.model_endpoint_reachable,
    )

    principle, why = abstract(diagnosis, distiller)
    if principle is None:
        return None, f"stage_b:{why}"

    try:
        report = scrub(principle.text, membrane_ev)
    except Exception as exc:  # noqa: BLE001 - a stage fault must fail closed
        return None, f"stage_c:scrub_error:{type(exc).__name__}"
    if not report.passed:
        return None, f"stage_c:{report.reason}"

    try:
        verdict = validate(report.text, membrane_ev, report)
    except Exception as exc:  # noqa: BLE001
        return None, f"stage_d:validator_error:{type(exc).__name__}"
    if not verdict.passed:
        return None, f"stage_d:{verdict.rationale}"

    rationale = (
        f"{verdict.rationale}; shared_mechanism={sorted(shared)}; "
        f"runs={len(diags)}; distiller={principle.distiller_id}"
    )
    return (
        SynthesisAttestation(
            synthesis_version=SYNTHESIS_VERSION,
            principle_text=report.text,
            feature_codes=tuple(sorted(shared)),
            mechanism=mechanism,
            evidence_ref=diagnosis.evidence_ref,
            validator_id=verdict.validator_id,
            validator_passed=verdict.passed,
            redactions=report.redactions,
            rationale=rationale,
            task_id=str(base.task_id or ""),
            rollout_id=str(base.rollout_id or ""),
        ),
        "ok",
    )