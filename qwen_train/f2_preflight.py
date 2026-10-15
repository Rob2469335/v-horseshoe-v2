"""F2 execution preflight: one command, deterministic, machine-readable.

Why this exists
---------------
Reaching a GO previously required an operator to remember ~20 environment
variables, know which of them are secrets, understand which readiness items are
repository-side and which are external, and infer what "blocked" even means.
That is an invitation to a false pass: a variable set to the wrong value, or a
string typed where evidence was required.

This module produces ONE deterministic report that separates six states which
must never be collapsed:

``IMPLEMENTED``
    Code exists in this repository and is covered by tests. Nothing is required
    from the operator.
``READY``
    Repository-side machinery is complete and CAN CONSUME the external evidence
    once it exists. This is a statement about capability, not about the
    experiment being runnable today.
``OPERATOR ACTION REQUIRED``
    The operator deliberately holds something back -- notably the receipt key,
    which is intentionally never provisioned in source control.
``PRIVILEGED HOST ACTION REQUIRED``
    Enforcing network egress denial needs a privileged host control. This
    repository can observe and verify isolation; it cannot impose it, and it does
    not pretend to.
``EXTERNAL EVIDENCE REQUIRED``
    Real base/gold executions, or a genuine ACTIVE lesson, must be produced by
    running something. No amount of repository work substitutes for them.
``AUTHORIZATION REQUIRED``
    An operator decision that has not been recorded. (Population acquisition is
    no longer in this class: ``F2-IMPL-AUTH-028/029/031`` record it, so the
    population gate below is evidence-blocked, not authorization-blocked.)

Nothing here fabricates. Every finding is either measured on this host, read from
the repository, or explicitly reported as absent. No secret value is ever read or
printed -- presence only.

Usage
-----
``python -m qwen_train.f2_preflight``            human-readable, exit 0/1
``python -m qwen_train.f2_preflight --json``     JSON only
``python -m qwen_train.f2_preflight --attestation PATH``
                                                   verify a supplied Q9 attestation
``python -m qwen_train.f2_preflight --attestation PATH
                                  --enforcement-capture PATH``
                                                   verify it the way the consuming gate
                                                   does, with the host capture that makes
                                                   a silently-dropping control visible
``python -m qwen_train.f2_preflight --conversion-record PATH``
                                                   verify a supplied conversion record
``python -m qwen_train.f2_preflight --provenance``
                                                   print the conversion-chain verdict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "IMPLEMENTED",
    "READY",
    "OPERATOR_ACTION",
    "PRIVILEGED_HOST",
    "EXTERNAL_EVIDENCE",
    "AUTHORIZATION",
    "NOT_EXECUTED",
    "CATEGORIES",
    "F2_REQUIRED_CONTROLS",
    "F2_ADAPTER_CONTROLS",
    "Finding",
    "run_preflight",
    "render",
    "main",
]

IMPLEMENTED = "IMPLEMENTED"
READY = "READY"
OPERATOR_ACTION = "OPERATOR ACTION REQUIRED"
PRIVILEGED_HOST = "PRIVILEGED HOST ACTION REQUIRED"
EXTERNAL_EVIDENCE = "EXTERNAL EVIDENCE REQUIRED"
AUTHORIZATION = "AUTHORIZATION REQUIRED"
NOT_EXECUTED = "NOT EXECUTED"

CATEGORIES = (
    IMPLEMENTED, READY, OPERATOR_ACTION, PRIVILEGED_HOST,
    EXTERNAL_EVIDENCE, AUTHORIZATION, NOT_EXECUTED,
)

#: Categories that BLOCK a GO. IMPLEMENTED and READY never block.
_BLOCKING = frozenset({OPERATOR_ACTION, PRIVILEGED_HOST, EXTERNAL_EVIDENCE, AUTHORIZATION})

#: Execution controls whose REQUIRED F2 value is established, with provenance.
#:
#: Only controls with a PROVEN required value belong here; a control the
#: repository does not require is deliberately absent rather than invented:
#:
#: * ``SWARM_SEMANTIC_CACHE=0`` -- a REQUIRED CONTROL in the frozen F0 design
#:   document (section 8, Contamination Framework, the "Semantic cache" row);
#: * the other six -- ``qwen_train/f2_execution_adapter.py:490-496``, the P2
#:   child environment the F2 arm is actually started with.
#:
#: ``SWARM_ROUTING_MODE`` and ``SWARM_ANALYSIS_CLOUD`` are NOT listed: no
#: repository source requires a value for them on the F2 path.
F2_REQUIRED_CONTROLS: dict[str, str] = {
    "SWARM_AUTONOMY": "0",
    "SWARM_EVOLUTION": "0",
    "SWARM_GENETIC_MUTATION": "0",
    "SWARM_SEMANTIC_CACHE": "0",
    "SWARM_MEMORY_INJECT": "0",
    "SWARM_NO_TOASTS": "1",
    "SWARM_F1_NO_WEB_TOOLS": "1",
}

#: The values the F2 execution adapter forces into the P2 child environment.
#:
#: Mirrors ``qwen_train/f2_execution_adapter.py:490-496`` (the ``env[...] = ...``
#: block, in source order) and the in-child launcher repeat at ``:298-303``.
#: These are copied rather than imported because the adapter assigns them as
#: inline literals with no module-level name.
F2_ADAPTER_CONTROLS: dict[str, str] = {
    "SWARM_MEMORY_INJECT": "0",
    "SWARM_AUTONOMY": "0",
    "SWARM_NO_TOASTS": "1",
    "SWARM_SEMANTIC_CACHE": "0",
    "SWARM_GENETIC_MUTATION": "0",
    "SWARM_EVOLUTION": "0",
    "SWARM_F1_NO_WEB_TOOLS": "1",
}

#: Every proven control above is a 0/1 flag, so any other string is
#: unrecognised and therefore fails closed rather than being coerced.
_F2_CONTROL_VALUES = frozenset({"0", "1"})


@dataclass(frozen=True)
class Finding:
    """One preflight observation."""

    category: str
    ident: str
    status: str
    detail: str
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "id": self.ident,
            "status": self.status,
            "detail": self.detail,
            "action": self.action,
        }


def _mod_attr(module: str, attr: str) -> tuple[bool, str]:
    try:
        __import__(module)
        m = sys.modules[module]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if not hasattr(m, attr):
        return False, f"{module} has no {attr}"
    return True, f"{module}.{attr} present"


def _env(name: str) -> str:
    return str(os.environ.get(name, "") or "").strip()


def _code_findings() -> list[Finding]:
    """Repository-side capability. Presence only -- nothing here needs an operator."""
    specs = [
        ("evaluator.producer", "qwen_train.f2_evaluator", "produce_report",
         "retained JUnit -> deterministic report"),
        ("evaluator.identity", "qwen_train.f2_evaluator", "implementation_digest",
         "five-field identity bound to evaluator bytes"),
        ("evaluator.protocol", "qwen_train.f2_governance", "derive_result",
         "independent re-derivation from retained bytes"),
        ("evidence.chain", "qwen_train.f2_governance", "verify_governed_pair",
         "base FAIL + gold PASS -> STATE_VERIFIED"),
        ("integrity.gate", "qwen_train.f2_integrity", "assess_arm_integrity",
         "fail-closed anti-tampering admission"),
        ("isolation.observe", "qwen_train.f2_isolation", "run_egress_probes",
         "unprivileged egress observation"),
        ("isolation.verify", "qwen_train.f2_isolation", "verify_isolation_attestation",
         "independent verdict re-derivation"),
        ("provenance.chain", "qwen_train.f2_model_provenance", "verify_conversion_chain",
         "base->adapter->corpus->conversion->served"),
        ("q6.gate", "qwen_train.f2_analysis", "evaluate_infrastructure_gate",
         "infrastructure-failure stop/diagnose"),
        ("t_x_c0.derive", "qwen_train.f2_arm_primitives", "derive_x_from_frozen",
         "exact deterministic lesson removal"),
        ("t_x_c0.c0", "qwen_train.f2_arm_primitives", "build_c0_artifact",
         "independent empty control"),
        ("readiness.gate", "qwen_train.f2_readiness", "evaluate_f2_readiness",
         "18-item fail-closed execution gate"),
    ]
    out: list[Finding] = []
    for ident, module, attr, what in specs:
        ok, detail = _mod_attr(module, attr)
        out.append(
            Finding(IMPLEMENTED if ok else EXTERNAL_EVIDENCE, ident,
                    "present" if ok else "MISSING",
                    f"{what} ({detail})" if ok else f"{what} -- {detail}",
                    "" if ok else f"restore {module}.py")
        )
    return out


def _env_findings() -> list[Finding]:
    """Machine-checkable environment prerequisites. Presence only, never values."""
    specs = [
        ("env.distiller_model", "SWARM_DISTILLER_MODEL",
         "distiller model identity", OPERATOR_ACTION),
        ("env.weights_digest", "SWARM_DISTILLER_WEIGHTS_DIGEST",
         "sha256 of the distiller weights", OPERATOR_ACTION),
        ("env.evaluator_id", "SWARM_F2_EVALUATOR_ID",
         "authorized evaluator id", OPERATOR_ACTION),
        ("env.evaluator_version", "SWARM_F2_EVALUATOR_VERSION",
         "authorized evaluator version", OPERATOR_ACTION),
        ("env.evaluator_procedure", "SWARM_F2_EVALUATOR_PROCEDURE",
         "authorized evaluator procedure id", OPERATOR_ACTION),
        ("env.artifact_root", "SWARM_F2_ARTIFACT_ROOT",
         "absolute trusted artifact store root", OPERATOR_ACTION),
        ("env.retention_days", "SWARM_F2_ARTIFACT_RETENTION_DAYS",
         "artifact retention in whole days", OPERATOR_ACTION),
        ("env.emit_bundle", "SWARM_F2_EMIT_BUNDLE",
         "must be enabled or no evidence chain is emitted", OPERATOR_ACTION),
        ("env.outcome_report", "SWARM_F2_TASK_OUTCOME_REPORT",
         "destination the evaluator writes the report to", OPERATOR_ACTION),
    ]
    out: list[Finding] = []
    for ident, var, what, cat in specs:
        val = _env(var)
        out.append(
            Finding(cat if not val else READY, ident,
                    "set" if val else "absent",
                    f"{var} is set" if val else f"{var} is not set ({what})",
                    "" if val else f"set {var}")
        )

    # Evaluator implementation artifact: a real file with a real digest.
    impl = _env("SWARM_F2_EVALUATOR_IMPL")
    if not impl:
        out.append(Finding(OPERATOR_ACTION, "env.evaluator_impl",
                           "absent", "SWARM_F2_EVALUATOR_IMPL is not set",
                           "point it at the evaluator implementation artifact"))
    elif not Path(impl).is_file():
        out.append(Finding(OPERATOR_ACTION, "env.evaluator_impl",
                           "missing", f"{impl} does not exist",
                           "point it at an existing evaluator implementation file"))
    else:
        try:
            digest = hashlib.sha256(Path(impl).read_bytes()).hexdigest()
        except OSError as exc:
            out.append(Finding(OPERATOR_ACTION, "env.evaluator_impl",
                               "unreadable", f"{impl}: {exc}", "supply a readable file"))
        else:
            out.append(Finding(READY, "env.evaluator_impl", "present",
                               f"sha256 {digest}", ""))

    # Receipt key: presence ONLY. The value is never read, logged or returned.
    out.append(
        Finding(READY if _env("SWARM_RECEIPT_KEY") else OPERATOR_ACTION,
                "env.receipt_key",
                "present" if _env("SWARM_RECEIPT_KEY") else "absent",
                "SWARM_RECEIPT_KEY is present (value never read)"
                if _env("SWARM_RECEIPT_KEY")
                else "SWARM_RECEIPT_KEY is absent; receipt authority fails closed "
                     "and no promotion can occur",
                "" if _env("SWARM_RECEIPT_KEY")
                else "provision it for the run only; never commit it")
    )
    return out


def _env_control_findings(
    root: Path,
    adapter_controls: Mapping[str, str] | None = None,
) -> list[Finding]:
    """Effective-value check for the F2 execution controls (``env.controls``).

    Why this is not a presence check
    -------------------------------
    The F2 child environment is built in three steps, and only the last one can
    change a value the adapter has already forced:

    1. ``env = os.environ.copy()``            ``f2_execution_adapter.py:488``
    2. ``env["SWARM_..."] = <required>``      ``f2_execution_adapter.py:490-496``
    3. inside the child,
       ``load_dotenv(override=True)``         ``swarm_os/app/main.py:20``
       then ``settings._load_dotenv()``       ``swarm_os/config/settings.py:20,23``

    Step 3 runs in the child AFTER step 2 and overwrites, so the precedence the
    child actually sees is::

        repository .env  >  adapter-forced value  >  inherited process env  >  ABSENT

    (Step 3 resolves ``.env`` from the current working directory when the host
    process is ``python -c``, which is exactly how the adapter spawns P2 --
    ``f2_execution_adapter.py:522`` with ``cwd=repo_root`` at ``:526``. Both
    loaders assign unconditionally, so a ``.env`` entry always wins.)

    A presence-only check reports PASS for ``SWARM_AUTONOMY=1`` merely because
    the name exists; that is precisely the contradiction this finding blocks.

    Only the seven non-secret controls above are ever read or reported.
    Credential-bearing names that appear in ``.env`` are never selected, never
    echoed and never placed in the finding.
    """
    adapter = dict(F2_ADAPTER_CONTROLS if adapter_controls is None else adapter_controls)
    file_env = _parse_operator_env(root)
    environ = os.environ

    expected = sorted(F2_REQUIRED_CONTROLS)
    matched: list[str] = []
    failures: list[tuple[str, str]] = []  # (kind, human text); kind drives status

    for name in expected:
        required = F2_REQUIRED_CONTROLS[name]
        if name in file_env:
            effective, source = file_env[name], "from .env"
        elif name in adapter:
            effective, source = adapter[name], "from the F2 adapter"
        elif name in environ:
            effective, source = environ[name], "from the inherited process env"
        else:
            failures.append((
                "missing",
                f"{name} is absent from .env, the F2 adapter and the process "
                f"environment (required {required})",
            ))
            continue

        if effective not in _F2_CONTROL_VALUES:
            failures.append((
                "invalid",
                f"{name}={effective} ({source}) is not a recognised 0/1 value "
                f"(required {required})",
            ))
        elif effective != required:
            failures.append((
                "mismatch",
                f"{name}={effective} ({source}) but required {required}",
            ))
        else:
            matched.append(f"{name}={required}")

    total = len(expected)
    if not failures:
        return [Finding(
            READY, "env.controls", "match",
            f"{len(matched)} of {total} controls match the required value: "
            + ", ".join(matched),
            "",
        )]

    kinds = {kind for kind, _ in failures}
    status = "missing" if "missing" in kinds else (
        "invalid" if "invalid" in kinds else "mismatch"
    )
    return [Finding(
        OPERATOR_ACTION, "env.controls", status,
        f"{len(failures)} of {total} controls fail: "
        + "; ".join(text for _, text in failures),
        f"correct the listed control(s) in {root / '.env'} so the F2 child "
        f"receives the required value",
    )]


def _evidence_findings(root: Path) -> list[Finding]:
    """External evidence and authorization gates that no repository change can
    satisfy.

    Q9 is deliberately NOT here. The privileged egress control
    (``host.egress_enforcement``) and its observed evidence
    (``evidence.q9_attestation``) are one requirement seen from two sides, so
    they are emitted together and reconciled from the single supplied artifact
    by :func:`_q9_findings`. Listing ``evidence.q9_attestation`` here as a fixed
    ``not_supplied`` gate produced a report in which the same id appeared twice
    -- once permanently ``not_supplied`` and once ``verified``.
    """
    return [
        Finding(EXTERNAL_EVIDENCE, "evidence.s8_base_gold", "absent",
                "no verified base/gold execution pair is present; S8 requires "
                "real base=FAIL and gold=PASS executions with retained output, "
                "run log and evaluator bytes",
                "run the authorized base and gold executions and retain the "
                "evidence in the trusted store"),
        Finding(EXTERNAL_EVIDENCE, "evidence.active_lesson", "absent",
                "no genuine ACTIVE lesson is established, so T cannot be frozen "
                "and X cannot be derived from it",
                "perform the authorized clean-room learning event (requires Q9 "
                "isolation, the receipt key, and an admitted population)"),
        Finding(EXTERNAL_EVIDENCE, "population.admission", "insufficient",
                "population acquisition IS authorized (F2-IMPL-AUTH-028/029/031); "
                "0 tasks are ADMITTED because S8 base/gold execution evidence is "
                "absent (see evidence.s8_base_gold)",
                "produce S8 base/gold evidence for the metadata-eligible tasks so "
                "they can be admitted"),
        Finding(NOT_EXECUTED, "run.q10_calibration", "not_executed",
                "calibration (Q10) is implemented but was not executed; it is not "
                "authorized in this repository state",
                "authorize Q10 to measure p_X, k and the censoring rates"),
        Finding(NOT_EXECUTED, "run.confirmatory_f2", "not_executed",
                "confirmatory F2 (Q13) was not executed and must not be until "
                "every gate above is satisfied",
                "authorize Q13 only after READY=True with evidence in hand"),
    ]


def _q9_findings(
    attestation: Path | None,
    enforcement_capture: Path | None = None,
) -> list[Finding]:
    """The two Q9 gates, reconciled from ONE artifact and emitted exactly once.

    ``host.egress_enforcement`` (the privileged control) and
    ``evidence.q9_attestation`` (the observed evidence for it) are the same
    requirement: the finding's own remedy is "apply a default-deny egress policy
    for the arm's identity, then run the isolation probes and supply the
    attestation". They therefore share a single source of truth and a single
    acceptance test -- the SAME test the consuming gate uses
    (``qwen_train.f2_readiness._check_no_egress``): the attestation's verdict
    must be satisfied AND its enforcement identity must be bound. A probe records
    THAT a connection failed, never WHICH control failed it, so an attestation
    whose enforcement identity is unbound is not evidence that enforcement is in
    force, and neither finding may clear.

    Fail-closed and single-valued: absent evidence leaves both blocked; a
    malformed artifact is ``invalid``; an unverified or unattributable one is
    ``not_established``; only a satisfied-and-bound artifact yields ``verified``
    / ``enforced``. No unrelated gate is touched.

    ``enforcement_capture`` is the HOST-side capture the consuming gate already
    accepts (``qwen_train.f2_readiness._check_no_egress``, threaded there for
    exactly this reason): a control that silently drops in the vSwitch produces
    a timeout no observer can tell from a blackhole, so without the capture this
    gate would report the S8 architecture's own enforcement as unproven. It is
    verified, never trusted -- the verifier still requires the capture to hash to
    the digest the attestation claims, its effective highest-weight rule to
    derive an explicit ``Deny`` for each literal target, and the negative control
    to be observed ``permitted``. A capture that cannot be read is reported
    loudly and leaves both gates blocked rather than being silently ignored.
    """
    if attestation is None:
        return [
            Finding(PRIVILEGED_HOST, "host.egress_enforcement", "not_enforced",
                    "outbound egress denial is a privileged host control; this "
                    "repository observes and verifies isolation but cannot impose "
                    "it",
                    "apply a default-deny egress policy for the arm's identity, "
                    "then run the isolation probes and supply the attestation"),
            Finding(EXTERNAL_EVIDENCE, "evidence.q9_attestation", "not_supplied",
                    "no verified clean-room attestation was supplied to the "
                    "readiness gate",
                    "run `python -m qwen_train.f2_isolation` under the enforced "
                    "policy, then pass the result with '--attestation PATH' and "
                    "the host capture with '--enforcement-capture PATH'"),
        ]

    try:
        from qwen_train.f2_isolation import (
            assess_enforcement_identity,
            parse_attestation,
            verify_isolation_attestation,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed
        return [
            Finding(PRIVILEGED_HOST, "host.egress_enforcement", "not_enforced",
                    f"isolation verifier unavailable, so enforcement cannot be "
                    f"established: {exc}", "restore qwen_train/f2_isolation.py"),
            Finding(PRIVILEGED_HOST, "evidence.q9_attestation", "invalid",
                    f"attestation could not be verified: {exc}",
                    "restore qwen_train/f2_isolation.py"),
        ]

    # The host capture is an EXPLICIT input. If the operator named one and it
    # cannot be read, that must be reported as its own reason -- silently
    # falling back to the capture-less contract would hand back a verdict whose
    # provenance the operator cannot tell from one that used their input.
    capture_bytes: bytes | None = None
    if enforcement_capture is not None:
        try:
            capture_bytes = Path(enforcement_capture).read_bytes()
        except OSError as exc:  # noqa: BLE001 - fail closed
            return [
                Finding(PRIVILEGED_HOST, "host.egress_enforcement", "not_enforced",
                        f"the supplied enforcement capture could not be read "
                        f"({exc}), so enforcement cannot be established",
                        "pass the host-side capture with "
                        "'--enforcement-capture PATH'"),
                Finding(PRIVILEGED_HOST, "evidence.q9_attestation",
                        "not_established",
                        f"enforcement capture unusable: {exc}",
                        "regenerate the host-side capture for this arm and pass "
                        "its path with '--enforcement-capture PATH'"),
            ]

    try:
        att = parse_attestation(attestation.read_bytes())
        verdict = verify_isolation_attestation(
            att, enforcement_capture=capture_bytes
        )
        identity = assess_enforcement_identity(att)
    except Exception as exc:  # noqa: BLE001 - fail closed
        return [
            Finding(PRIVILEGED_HOST, "host.egress_enforcement", "not_enforced",
                    f"supplied attestation is unusable ({exc}), so enforcement is "
                    "not established",
                    "enforce egress denial and supply a valid attestation"),
            Finding(PRIVILEGED_HOST, "evidence.q9_attestation", "invalid",
                    f"attestation could not be parsed or verified: {exc}",
                    "regenerate it with qwen_train.f2_isolation"),
        ]

    if verdict.satisfied and identity.bound:
        return [
            Finding(READY, "host.egress_enforcement", "enforced",
                    f"egress enforcement observed and independently verified "
                    f"under enforcement scope {identity.scope_model!r}", ""),
            Finding(READY, "evidence.q9_attestation", "verified",
                    f"{verdict.detail}; enforcement identity bound via "
                    f"{identity.scope_model}", ""),
        ]

    if not verdict.satisfied:
        reason = verdict.detail
        remedy = "enforce egress denial, then re-run the attestation probes"
    else:
        reason = (
            "isolation probes satisfied but the ENFORCEMENT IDENTITY is not "
            "bound: " + ", ".join(identity.missing)
        )
        remedy = (
            "re-run f2_isolation with --enforcement-scope, --interpreter-path and "
            "--interpreter-sha256 (and --spawn-image per spawned image)"
        )
    return [
        Finding(PRIVILEGED_HOST, "host.egress_enforcement", "not_enforced",
                f"attestation supplied but enforcement is not established: {reason}",
                remedy),
        Finding(PRIVILEGED_HOST, "evidence.q9_attestation", "not_established",
                reason, remedy),
    ]


def _resolve_conversion_record(
    conversion_record: Path | Mapping[str, Any] | str | None = None,
) -> tuple[Mapping[str, Any] | None, str]:
    """Resolve conversion record from argument or SWARM_F2_CONVERSION_RECORD.

    CLI / programmatic argument takes precedence over environment variable.
    Fails closed if a supplied path cannot be read, parsed, or is not a JSON object.
    """
    if conversion_record is not None:
        if isinstance(conversion_record, Mapping):
            return conversion_record, ""
        p = Path(conversion_record)
        if not p.is_file():
            return None, f"supplied conversion record '{p.name}' does not exist or is not a file"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - fail closed
            return None, f"supplied conversion record '{p.name}' could not be parsed: {exc}"
        if not isinstance(data, Mapping):
            return None, f"supplied conversion record '{p.name}' is not a JSON object"
        return data, ""

    rec_env = _env("SWARM_F2_CONVERSION_RECORD")
    if rec_env:
        p = Path(rec_env)
        if not p.is_file():
            return None, f"SWARM_F2_CONVERSION_RECORD '{p.name}' does not exist or is not a file"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - fail closed
            return None, f"SWARM_F2_CONVERSION_RECORD '{p.name}' could not be parsed: {exc}"
        if not isinstance(data, Mapping):
            return None, f"SWARM_F2_CONVERSION_RECORD '{p.name}' is not a JSON object"
        return data, ""

    return None, ""


#: Authorized F2 distiller GGUF the provenance chain is bound to, plus its
#: SHA-256. Single source of truth so a model-identity change is one visible
#: edit. This is the WITNESSED RECONVERSION (owner-authorized 2026-10-14); it is
#: deliberately NOT byte-identical to the previously served artifact
#: (``65202f37...``, 2_708_803_840 B), which is preserved unchanged. The two are
#: distinct artifacts with distinct digests and must never be conflated.
SERVED_GGUF_PATH = Path(
    r"C:\Users\rober\F2_EVIDENCE\s8\conversion\robs4b_q4km_reconverted.gguf"
)
SERVED_GGUF_SHA256 = (
    "3e5ba031f0b50e98450d1575aa19780bc5b4b13f48bf3eca38f3c915a442a337"
)


def _provenance_findings(
    root: Path,
    conversion_record: Path | Mapping[str, Any] | str | None = None,
) -> list[Finding]:
    """Conversion-chain verdict, computed from what is actually on disk."""
    try:
        from qwen_train import f2_model_provenance as mp
    except Exception as exc:  # noqa: BLE001
        return [Finding(IMPLEMENTED, "provenance.module", "MISSING",
                        f"f2_model_provenance unavailable: {exc}", "")]

    rec, err = _resolve_conversion_record(conversion_record)
    if err:
        try:
            chain = mp.build_conversion_chain(
                model_alias="robs4b",
                training_runs_path=root / "qwen_train" / "training_runs.jsonl",
                adapter_dirs=[root / "qwen_train" / "robs4b_final_adapter"],
                corpus_search_roots=[root.parent / "qwen_train_data"],
                served_gguf=SERVED_GGUF_PATH,
                served_gguf_sha256=SERVED_GGUF_SHA256,
                conversion_record=None,
            )
        except Exception as exc:  # noqa: BLE001
            return [Finding(EXTERNAL_EVIDENCE, "provenance.chain", "error",
                            f"chain could not be built: {exc}", "")]
        out = [
            Finding(IMPLEMENTED if link.state == "PROVEN" else EXTERNAL_EVIDENCE,
                    f"provenance.{link.name}", link.state, link.detail,
                    "" if link.state == "PROVEN" else mp.LINK_REMEDY.get(link.name, ""))
            for link in chain.links
            if link.name != mp.LINK_CONVERSION
        ]
        out.append(
            Finding(EXTERNAL_EVIDENCE, "provenance.conversion", "error",
                    f"conversion record unusable: {err}",
                    "supply a valid conversion record JSON")
        )
        out.append(
            Finding(OPERATOR_ACTION, "provenance.verdict", "incomplete",
                    f"model provenance NOT established: {err}",
                    "supply a valid conversion record JSON")
        )
        return out

    try:
        chain = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=root / "qwen_train" / "training_runs.jsonl",
            adapter_dirs=[root / "qwen_train" / "robs4b_final_adapter"],
            corpus_search_roots=[root.parent / "qwen_train_data"],
            served_gguf=SERVED_GGUF_PATH,
            served_gguf_sha256=SERVED_GGUF_SHA256,
            conversion_record=rec,
        )
    except Exception as exc:  # noqa: BLE001
        return [Finding(EXTERNAL_EVIDENCE, "provenance.chain", "error",
                        f"chain could not be built: {exc}", "")]
    verdict = mp.verify_conversion_chain(chain)
    out = [
        Finding(IMPLEMENTED if link.state == "PROVEN" else EXTERNAL_EVIDENCE,
                f"provenance.{link.name}", link.state, link.detail,
                "" if link.state == "PROVEN" else mp.LINK_REMEDY.get(link.name, ""))
        for link in chain.links
    ]
    out.append(
        Finding(IMPLEMENTED if verdict.satisfied else OPERATOR_ACTION,
                "provenance.verdict",
                "complete" if verdict.satisfied else "incomplete",
                verdict.detail,
                "" if verdict.satisfied else "; ".join(verdict.remedies))
    )
    return out


def run_preflight(
    *,
    root: Path | None = None,
    attestation: Path | None = None,
    enforcement_capture: Path | None = None,
    conversion_record: Path | Mapping[str, Any] | str | None = None,
    env: dict[str, str] | None = None,
    adapter_controls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run every preflight check and return a deterministic report.

    ``enforcement_capture`` is the host-side enforcement capture that belongs to
    ``attestation``. It reaches only the Q9 gate, and only the same way the
    consuming gate takes it (``qwen_train.f2_readiness``); supplying it can
    shrink the unproven set, never grow the satisfied one.

    ``conversion_record`` is an optional conversion record JSON path or mapping.
    CLI/explicit argument takes precedence over SWARM_F2_CONVERSION_RECORD.

    ``adapter_controls`` overrides the adapter-forced control values that
    ``env.controls`` resolves against. Like ``env`` it exists so a test can
    model a hypothetical child environment; omitting it uses the real adapter
    values from ``F2_ADAPTER_CONTROLS``.
    """
    repo = Path(root) if root else Path(__file__).resolve().parents[1]
    if env is not None:
        # Only used by tests, so the report stays hermetic.
        for k, v in env.items():
            os.environ[k] = v

    findings: list[Finding] = []
    findings += _code_findings()
    findings += _env_findings()
    findings += _env_control_findings(repo, adapter_controls=adapter_controls)
    findings += _evidence_findings(repo)
    # Q9: the privileged control and its attestation evidence are one gate,
    # VERIFIED (never trusted) from the single supplied artifact, with the host
    # capture the consuming gate also takes.
    findings += _q9_findings(attestation, enforcement_capture)
    findings += _provenance_findings(repo, conversion_record=conversion_record)

    for f in findings:
        if f.category not in CATEGORIES:
            raise ValueError(f"unknown preflight category {f.category!r}")

    blocking = [f for f in findings if f.category in _BLOCKING]
    by_category = {c: [f for f in findings if f.category == c] for c in CATEGORIES}
    return {
        "schema": "f2_preflight_v1",
        "result": "BLOCKED" if blocking else "READY",
        "blocking_count": len(blocking),
        "counts": {c: len(v) for c, v in by_category.items()},
        "findings": [f.to_dict() for f in findings],
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "=" * 78,
        f"F2 PREFLIGHT: {report['result']}",
        f"blocking findings: {report['blocking_count']}",
        "=" * 78,
    ]
    for cat in CATEGORIES:
        items = [f for f in report["findings"] if f["category"] == cat]
        if not items:
            continue
        lines.append("")
        lines.append(f"--- {cat} ({len(items)}) ---")
        for f in items:
            lines.append(f"  [{f['status']:>14}] {f['id']}")
            lines.append(f"                     {f['detail']}")
            if f["action"]:
                lines.append(f"                     ACTION: {f['action']}")
    lines.append("")
    lines.append("NOTE: READY here means repository machinery can CONSUME the")
    lines.append("required external evidence. It is not a claim that F2 is")
    lines.append("scientifically complete or executable today.")
    return "\n".join(lines)


def _parse_operator_env(root: Path) -> dict[str, str]:
    """Parse ``<root>/.env`` into a ``name -> value`` mapping. Strictly read-only.

    This is the parsing half of :func:`_load_operator_env`, factored out so the
    loader and the ``env.controls`` check cannot drift apart about what a line
    means: comment/blank skipping, whitespace stripping, surrounding quote
    stripping, and first-occurrence-wins for a duplicated key are defined in
    exactly one place.

    It never touches ``os.environ`` and never writes anything. Whether a caller
    is allowed to look at a given key is the caller's decision -- see
    :func:`_env_control_findings`, which selects only the seven non-secret
    control names.
    """
    env_path = root / ".env"
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in out:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def _load_operator_env(root: Path) -> None:
    """Populate ``os.environ`` from the repository's ``.env`` for variables not set.

    Why this exists
    ---------------
    The operator handoff states two facts that were mutually unsatisfiable:

    * the receipt key's provision location is the operator's ``.env``
      (``EXPERIMENT_J_F2_OPERATOR_HANDOFF.md`` section 1.2); and
    * preflight is a standalone command, ``python -m qwen_train.f2_preflight``
      (handoff step K).

    ``f2_readiness`` reads ``os.environ`` only, so a key correctly placed in
    ``.env`` was invisible to the very gate that is supposed to check it. That
    is a mechanism defect, not an operator mistake: the documented check could
    not observe the documented location.

    This follows the repository's own convention -- ``swarm_os/config/settings.py``
    self-loads ``.env`` for the same reason -- and resolves ``.env`` at the
    REPOSITORY root rather than the current working directory, because the
    handoff documents preflight as directly runnable.

    Deliberate safety properties:

    * **An explicit process environment always wins.** A variable already present
      in ``os.environ`` is never overwritten, so an operator who exports a value
      in their shell is never silently replaced by a stale file.
    * **Values are never logged, printed, or returned.** Only presence is observed.
    * **Nothing is written.** This reads ``.env``; it never modifies it.
    * **Fail-closed behaviour is unchanged.** If ``.env`` is absent or unreadable,
      nothing happens and every check proceeds exactly as before. No check is
      weakened, skipped, or bypassed.
    """
    for key, value in _parse_operator_env(root).items():
        if key in os.environ:
            continue
        os.environ[key] = value


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qwen_train.f2_preflight")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--attestation", default=None,
                    help="path to a clean-room attestation to verify")
    ap.add_argument("--enforcement-capture", default=None,
                    help="host-side enforcement capture that belongs to the "
                         "supplied attestation (requires --attestation)")
    ap.add_argument("--conversion-record", default=None,
                    help="path to a conversion record JSON to verify")
    ap.add_argument("--provenance", action="store_true",
                    help="print only the conversion-chain verdict")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.enforcement_capture and not args.attestation:
        print(
            "error: --enforcement-capture requires --attestation: a capture is "
            "binding evidence for one attestation, not a standalone input",
            file=sys.stderr,
        )
        return 2

    root = Path(__file__).resolve().parents[1]
    _load_operator_env(root)
    att = Path(args.attestation) if args.attestation else None
    cap = Path(args.enforcement_capture) if args.enforcement_capture else None
    conv_path = Path(args.conversion_record) if args.conversion_record else None

    if args.provenance:
        for f in _provenance_findings(root, conversion_record=conv_path):
            print(f"[{f.status:>10}] {f.ident}: {f.detail}")
            if f.action:
                print(f"             ACTION: {f.action}")
        return 0

    report = run_preflight(root=root, attestation=att,
                           enforcement_capture=cap,
                           conversion_record=conv_path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0 if report["result"] == "READY" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
