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
    An operator decision that has not been recorded, most notably population
    acquisition, which ``F2-IMPL-AUTH-013`` explicitly does not authorize.

Nothing here fabricates. Every finding is either measured on this host, read from
the repository, or explicitly reported as absent. No secret value is ever read or
printed -- presence only.

Usage
-----
``python -m qwen_train.f2_preflight``            human-readable, exit 0/1
``python -m qwen_train.f2_preflight --json``     JSON only
``python -m qwen_train.f2_preflight --attestation PATH``
                                                   verify a supplied Q9 attestation
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
from typing import Any, Sequence

__all__ = [
    "IMPLEMENTED",
    "READY",
    "OPERATOR_ACTION",
    "PRIVILEGED_HOST",
    "EXTERNAL_EVIDENCE",
    "AUTHORIZATION",
    "NOT_EXECUTED",
    "CATEGORIES",
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


def _evidence_findings(root: Path) -> list[Finding]:
    """External evidence and authorization gates. Nothing here can be satisfied
    by repository work."""
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
        Finding(PRIVILEGED_HOST, "host.egress_enforcement", "not_enforced",
                "outbound egress denial is a privileged host control; this "
                "repository observes and verifies isolation but cannot impose it",
                "apply a default-deny egress policy for the arm's identity, then "
                "run the isolation probes and supply the attestation"),
        Finding(AUTHORIZATION, "authz.population_acquisition", "not_authorized",
                "F2-IMPL-AUTH-013 explicitly does not authorize acquiring the "
                "task population; no task may be admitted without it",
                "record an explicit authorization, or accept that the admitted "
                "population is zero"),
        Finding(EXTERNAL_EVIDENCE, "evidence.q9_attestation", "not_supplied",
                "no verified clean-room attestation was supplied to the "
                "readiness gate",
                "run `python -m qwen_train.f2_isolation` under the enforced "
                "policy and pass the attestation as 'no_egress_attestation'"),
        Finding(NOT_EXECUTED, "run.q10_calibration", "not_executed",
                "calibration (Q10) is implemented but was not executed; it is not "
                "authorized in this repository state",
                "authorize Q10 to measure p_X, k and the censoring rates"),
        Finding(NOT_EXECUTED, "run.confirmatory_f2", "not_executed",
                "confirmatory F2 (Q13) was not executed and must not be until "
                "every gate above is satisfied",
                "authorize Q13 only after READY=True with evidence in hand"),
    ]


def _provenance_findings(root: Path) -> list[Finding]:
    """Conversion-chain verdict, computed from what is actually on disk."""
    try:
        from qwen_train import f2_model_provenance as mp
    except Exception as exc:  # noqa: BLE001
        return [Finding(IMPLEMENTED, "provenance.module", "MISSING",
                        f"f2_model_provenance unavailable: {exc}", "")]
    try:
        chain = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=root / "qwen_train" / "training_runs.jsonl",
            adapter_dirs=[root / "qwen_train" / "robs4b_final_adapter"],
            corpus_search_roots=[root.parent / "qwen_train_data"],
            served_gguf=root / "qwen_train" / "robs4b_q4km.gguf",
            served_gguf_sha256="65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41",
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
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run every preflight check and return a deterministic report."""
    repo = Path(root) if root else Path(__file__).resolve().parents[1]
    if env is not None:
        # Only used by tests, so the report stays hermetic.
        for k, v in env.items():
            os.environ[k] = v

    findings: list[Finding] = []
    findings += _code_findings()
    findings += _env_findings()
    findings += _evidence_findings(repo)

    # Q9 attestation, when supplied, is VERIFIED here rather than trusted.
    if attestation is not None:
        try:
            from qwen_train.f2_isolation import (
                parse_attestation, verify_isolation_attestation,
            )
            v = verify_isolation_attestation(parse_attestation(attestation.read_bytes()))
            findings.append(
                Finding(READY if v.satisfied else PRIVILEGED_HOST,
                        "evidence.q9_attestation",
                        "verified" if v.satisfied else "not_established",
                        v.detail, "" if v.satisfied else "enforce egress denial")
            )
        except Exception as exc:  # noqa: BLE001
            findings.append(
                Finding(PRIVILEGED_HOST, "evidence.q9_attestation", "invalid",
                        f"attestation could not be verified: {exc}",
                        "regenerate it with qwen_train.f2_isolation")
            )

    findings += _provenance_findings(repo)

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


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qwen_train.f2_preflight")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--attestation", default=None,
                    help="path to a clean-room attestation to verify")
    ap.add_argument("--provenance", action="store_true",
                    help="print only the conversion-chain verdict")
    args = ap.parse_args(list(argv) if argv is not None else None)

    root = Path(__file__).resolve().parents[1]
    att = Path(args.attestation) if args.attestation else None

    if args.provenance:
        for f in _provenance_findings(root):
            print(f"[{f.status:>10}] {f.ident}: {f.detail}")
            if f.action:
                print(f"             ACTION: {f.action}")
        return 0

    report = run_preflight(root=root, attestation=att)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0 if report["result"] == "READY" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
