"""Authoritative F2 test-outcome evaluator (independent, deterministic).

This module is the PRODUCER that was missing from the F2 execution path. Before
this module existed the repository contained three *consumers* of a per-node test
status map (``f2_governance._derive_json_test_report_v1``,
``f2_protocol._derive_task_outcome`` and
``f2_arm_worker._task_success_from_report``) and **zero** producers: nothing in
the repository could turn retained execution evidence into that map.

Design commitments
------------------
* **Independent of the F2 analysis code.** This module imports nothing from
  ``f2_governance``, ``f2_protocol``, ``f2_analysis`` or ``f2_statistics``. It
  depends only on the standard library, so a defect in the verifier cannot mask
  itself through a shared code path.
* **Identity is reconstructed, never guessed.** pytest's ``junitxml`` writer
  splits a node id into ``classname=<dotted module + class chain>`` and
  ``name=<final segment, parametrisation included>``. The inverse of that
  transform is implemented exactly in :func:`nodeid_to_junit_identity`. There is
  no ``split("::")`` guesswork and no prefix matching anywhere.
* **Fail closed.** Malformed XML, an unresolvable declared node id, a duplicate
  emitted identity, or an empty declared set all produce a non-passing outcome
  with a machine-readable reason. None of them can yield ``pass``.
* **Deterministic bytes.** The report is canonical JSON with sorted keys, fixed
  separators and ``ensure_ascii``; it carries no timestamp, no hostname, no
  path, and no ordering that depends on dict iteration. The same evidence and
  the same declared contract always produce byte-identical output.

Verified identity convention
----------------------------
Empirically confirmed against pytest's own ``--junitxml`` writer:

===========================  =====================================  ==========================
declared node id             emitted ``classname``                  emitted ``name``
===========================  =====================================  ==========================
``sub/test_things.py::test_plain``              ``sub.test_things``             ``test_plain``
``sub/test_things.py::TestBar::test_meth``      ``sub.test_things.TestBar``     ``test_meth``
``sub/test_things.py::test_param[1]``           ``sub.test_things``             ``test_param[1]``
``sub/test_things.py::test_strparam[c d]``      ``sub.test_things``             ``test_strparam[c d]``
===========================  =====================================  ==========================

A plain-function failure AND a raised ``RuntimeError`` both surface as
``<failure>``; pytest reserves ``<error>`` for other conditions. Both are
treated as *not passed* here, so the distinction can never manufacture a pass.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "EVALUATOR_ID",
    "EVALUATOR_VERSION",
    "PROCEDURE_ID",
    "PROTOCOL_VERSION",
    "RESULT_PROTOCOL_ID",
    "REPORT_SCHEMA",
    "MAX_JUNIT_BYTES",
    "STATUS_PASSED",
    "STATUS_FAILED",
    "STATUS_ERROR",
    "STATUS_SKIPPED",
    "STATUS_MISSING",
    "NOT_PASSED",
    "JUnitIdentity",
    "EvaluationOutcome",
    "EvaluatorError",
    "nodeid_to_junit_identity",
    "parse_junit_xml",
    "evaluate_execution",
    "render_report",
    "parse_report",
    "verdict_from_report_payload",
    "derive_result_protocol",
    "implementation_digest",
    "identity_fields",
]

# --------------------------------------------------------------------------
# Evaluator identity. These five values ARE the authorization record; the
# implementation digest binds them to the exact bytes that ran.
# --------------------------------------------------------------------------
EVALUATOR_ID = "f2_pytest_junit_evaluator"
EVALUATOR_VERSION = "1.0.0"
PROCEDURE_ID = "f2_pytest_junit_identity_v1"
PROTOCOL_VERSION = "f2_experiment_j_v1"
RESULT_PROTOCOL_ID = "f2_evaluator_report_v1"
REPORT_SCHEMA = "f2_evaluator_report_v1"

#: Hard cap on retained JUnit bytes. Bounds XML parse cost and refuses to treat
#: an unbounded document as execution evidence.
MAX_JUNIT_BYTES = 64 * 1024 * 1024

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"
STATUS_MISSING = "missing"

#: Any status in this set means "the test did not pass". SKIPPED and ERROR are
#: explicitly not passes, matching the SWE-bench grading convention.
NOT_PASSED = frozenset({STATUS_FAILED, STATUS_ERROR, STATUS_SKIPPED, STATUS_MISSING})


class EvaluatorError(RuntimeError):
    """Raised when the evaluator cannot establish a result. Never a pass."""


@dataclass(frozen=True, order=True)
class JUnitIdentity:
    """The pair pytest's junitxml writer emits for one test case."""

    classname: str
    name: str

    def as_text(self) -> str:
        return f"{self.classname}::{self.name}"


def nodeid_to_junit_identity(nodeid: str) -> JUnitIdentity:
    """Exact inverse of pytest's ``junitxml`` classname/name split.

    pytest writes ``classname`` as the module path with ``/`` replaced by ``.``
    and the ``.py`` suffix removed, followed by the class chain joined with
    ``.``. ``name`` is the final ``::`` segment with any parametrisation
    bracket intact.

    Raises :class:`EvaluatorError` for a node id that cannot be decomposed,
    rather than guessing.
    """
    text = str(nodeid or "").strip()
    if not text:
        raise EvaluatorError("declared test node id is empty")
    if "::" not in text:
        raise EvaluatorError(
            f"declared test node id {text!r} is not a pytest node id "
            "(expected '<path>::[<Class>::]<test>')"
        )
    path, *rest = text.split("::")
    if not path:
        raise EvaluatorError(f"declared test node id {text!r} has no file path segment")
    if any(not seg for seg in rest):
        raise EvaluatorError(f"declared test node id {text!r} has an empty '::' segment")
    module = path[:-3].replace("\\", "/").replace("/", ".") if path.endswith(".py") else (
        path.replace("\\", "/").replace("/", ".")
    )
    if not module:
        raise EvaluatorError(f"declared test node id {text!r} yields an empty module name")
    classname = ".".join([module, *rest[:-1]])
    name = rest[-1]
    if not name:
        raise EvaluatorError(f"declared test node id {text!r} yields an empty test name")
    return JUnitIdentity(classname=classname, name=name)


def parse_junit_xml(data: bytes) -> dict[JUnitIdentity, str]:
    """Parse retained JUnit XML into ``{identity: status}``.

    Fails closed on anything that is not well-formed, bounded, doctype-free
    JUnit XML. A duplicate ``(classname, name)`` pair is an error: pytest can
    legitimately emit two cases for one identity under ``--lf``/rerun plugins,
    and silently picking one would make the result order-dependent.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise EvaluatorError(f"junit evidence must be bytes, got {type(data).__name__}")
    if not data:
        raise EvaluatorError("junit evidence is empty")
    if len(data) > MAX_JUNIT_BYTES:
        raise EvaluatorError(
            f"junit evidence is {len(data)} bytes, above the {MAX_JUNIT_BYTES} byte cap"
        )
    head = data[:4096].lstrip()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise EvaluatorError(
            "junit evidence declares a DOCTYPE/ENTITY; refusing to parse an "
            "XML document that can define entities"
        )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EvaluatorError(f"junit evidence is not well-formed XML: {exc}") from exc

    statuses: dict[JUnitIdentity, str] = {}
    cases = 0
    for case in root.iter("testcase"):
        cases += 1
        classname = case.get("classname")
        name = case.get("name")
        if classname is None or name is None:
            raise EvaluatorError(
                "junit evidence contains a <testcase> without classname/name"
            )
        ident = JUnitIdentity(classname=str(classname), name=str(name))
        tags = {child.tag for child in case}
        if "skipped" in tags:
            status = STATUS_SKIPPED
        elif "error" in tags:
            status = STATUS_ERROR
        elif "failure" in tags:
            status = STATUS_FAILED
        else:
            status = STATUS_PASSED
        if ident in statuses:
            raise EvaluatorError(
                f"junit evidence emits duplicate test identity {ident.as_text()!r}; "
                "the retained evidence is ambiguous"
            )
        statuses[ident] = status

    if cases == 0:
        raise EvaluatorError("junit evidence contains no <testcase> elements")
    return statuses


@dataclass(frozen=True)
class EvaluationOutcome:
    """Deterministic verdict over one execution's retained evidence."""

    declared_result: str
    fail_to_pass: dict[str, str]
    pass_to_pass: dict[str, str]
    detail: str
    emitted_identities: int

    @property
    def all_fail_to_pass_passed(self) -> bool:
        return bool(self.fail_to_pass) and all(
            s == STATUS_PASSED for s in self.fail_to_pass.values()
        )

    @property
    def all_pass_to_pass_passed(self) -> bool:
        return bool(self.pass_to_pass) and all(
            s == STATUS_PASSED for s in self.pass_to_pass.values()
        )


def _normalise_declaration(values: Iterable[Any] | None, label: str) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise EvaluatorError(f"{label} must be a sequence of node ids, not a bare string")
    out: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if text:
            out.append(text)
    # Preserve declared order for the report, but reject an ambiguous declaration.
    if len(set(out)) != len(out):
        seen: set[str] = set()
        dupes = sorted({v for v in out if v in seen or seen.add(v)})  # type: ignore[func-returns-value]
        raise EvaluatorError(f"{label} declares duplicate node ids: {dupes}")
    return out


def evaluate_execution(
    junit_bytes: bytes,
    *,
    fail_to_pass: Sequence[str] | None,
    pass_to_pass: Sequence[str] | None = None,
    instance_id: str = "",
    execution_state_identity: str = "",
) -> EvaluationOutcome:
    """Evaluate one execution's retained JUnit evidence against the declared contract.

    Returns an outcome whose ``declared_result`` is ``"pass"`` only when every
    declared FAIL_TO_PASS node is present in the evidence with status
    ``passed``. Any missing, failed, errored or skipped FAIL_TO_PASS node makes
    the result ``fail``.

    PASS_TO_PASS is evaluated and reported but never changes ``declared_result``:
    a PASS_TO_PASS regression is a separate, pre-declared exclusion reason
    handled by admission, not a substitute for the primary FAIL_TO_PASS verdict.
    """
    declared_f2p = _normalise_declaration(fail_to_pass, "FAIL_TO_PASS")
    declared_p2p = _normalise_declaration(pass_to_pass, "PASS_TO_PASS")
    if not declared_f2p:
        raise EvaluatorError(
            "FAIL_TO_PASS is empty; the evaluator cannot establish a result from an "
            "empty declared contract"
        )

    statuses = parse_junit_xml(junit_bytes)

    resolved: dict[str, str] = {}
    for nodeid in declared_f2p:
        ident = nodeid_to_junit_identity(nodeid)
        resolved[nodeid] = statuses.get(ident, STATUS_MISSING)

    resolved_p2p: dict[str, str] = {}
    for nodeid in declared_p2p:
        ident = nodeid_to_junit_identity(nodeid)
        resolved_p2p[nodeid] = statuses.get(ident, STATUS_MISSING)

    not_passed = {k: v for k, v in sorted(resolved.items()) if v != STATUS_PASSED}
    if not not_passed:
        detail = f"all {len(resolved)} declared FAIL_TO_PASS nodes passed"
        result = "pass"
    else:
        result = "fail"
        summary = ", ".join(f"{k}={v}" for k, v in list(not_passed.items())[:8])
        more = "" if len(not_passed) <= 8 else f" (+{len(not_passed) - 8} more)"
        detail = f"{len(not_passed)} of {len(resolved)} FAIL_TO_PASS nodes not passed: {summary}{more}"

    outcome = EvaluationOutcome(
        declared_result=result,
        fail_to_pass=resolved,
        pass_to_pass=resolved_p2p,
        detail=detail,
        emitted_identities=len(statuses),
    )
    if instance_id or execution_state_identity:
        # Retained for the caller's provenance record; never affects the verdict.
        pass
    return outcome


#: Marker appended to an augmented pytest command line.
JUNIT_FLAG = "--junitxml"


def augment_test_command(test_cmd: str, junit_path: str) -> str:
    """Return ``test_cmd`` with a ``--junitxml=<path>`` capture appended.

    This is the seam that lets the *existing* declared test command produce
    machine-readable evidence without the task contract being rewritten. It is
    idempotent: a command that already carries a ``--junitxml`` is rejected
    rather than silently given a second, conflicting destination, because two
    destinations would make the retained evidence ambiguous.
    """
    cmd = str(test_cmd or "").strip()
    if not cmd:
        raise EvaluatorError("declared test command is empty")
    if JUNIT_FLAG in cmd:
        raise EvaluatorError(
            "declared test command already specifies --junitxml; refusing to add a "
            "second, conflicting JUnit destination"
        )
    return f"{cmd} {JUNIT_FLAG}={junit_path}"


def produce_report(
    *,
    junit_path: str | Path,
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str] | None = None,
    out_path: str | Path,
    instance_id: str = "",
    repository: str = "",
    base_commit: str = "",
    execution_state_identity: str = "",
) -> dict[str, Any]:
    """Evaluate retained JUnit evidence and WRITE the canonical report file.

    This is the producer the F2 execution path was missing. It writes exactly the
    deterministic bytes :func:`render_report` defines, at the path the worker
    reads via ``SWARM_F2_TASK_OUTCOME_REPORT``, and returns the parsed report.

    The write is atomic (temp file + ``os.replace``) so a crashed producer can
    never leave a truncated report that a later reader would treat as evidence.
    """
    import os
    import pathlib as _pathlib

    src = _pathlib.Path(junit_path)
    if not src.is_file():
        raise EvaluatorError(f"junit evidence file does not exist: {src}")
    data = src.read_bytes()

    outcome = evaluate_execution(
        data,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        instance_id=instance_id,
        execution_state_identity=execution_state_identity,
    )
    blob = render_report(
        outcome,
        instance_id=instance_id,
        repository=repository,
        base_commit=base_commit,
        execution_state_identity=execution_state_identity,
    )

    dst = _pathlib.Path(out_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, dst)
    return parse_report(blob)


def identity_fields() -> dict[str, str]:
    """The five-field evaluator authorization record."""
    return {
        "evaluator_id": EVALUATOR_ID,
        "version": EVALUATOR_VERSION,
        "implementation_digest": implementation_digest(),
        "procedure_id": PROCEDURE_ID,
        "protocol_version": PROTOCOL_VERSION,
    }


def implementation_digest() -> str:
    """Lowercase SHA-256 over this module's own source bytes.

    The digest binds the authorized evaluator identity to the exact
    implementation that ran, so a declared identity cannot be separated from the
    bytes that produced the verdict.
    """
    import pathlib

    here = pathlib.Path(__file__).resolve()
    return hashlib.sha256(here.read_bytes()).hexdigest()


#: Self-integrity digest over a report's own content. Without it, anyone able to
#: write the report file can rewrite ``declared_result`` and still pass the
#: internal consistency check, because that check re-derives from the same
#: attacker-controlled map. The digest binds every other field, so any edit
#: breaks verification.
REPORT_DIGEST_FIELD = "report_digest"

#: Fields excluded from the digest (the digest itself, obviously).
_DIGEST_EXCLUDED = frozenset({REPORT_DIGEST_FIELD})


def compute_report_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical report content, excluding the digest field."""
    body = {k: v for k, v in payload.items() if k not in _DIGEST_EXCLUDED}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()


def render_report(
    outcome: EvaluationOutcome,
    *,
    instance_id: str,
    repository: str = "",
    base_commit: str = "",
    execution_state_identity: str = "",
    implementation_digest_hex: str | None = None,
) -> bytes:
    """Render the canonical, deterministic evaluator report bytes.

    Byte-for-byte reproducible: sorted keys, fixed separators, ASCII-escaped,
    no timestamps, no hostnames, no absolute paths.
    """
    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "evaluator_id": EVALUATOR_ID,
        "evaluator_version": EVALUATOR_VERSION,
        "procedure_id": PROCEDURE_ID,
        "protocol_version": PROTOCOL_VERSION,
        "implementation_digest": implementation_digest_hex or implementation_digest(),
        "instance_id": str(instance_id),
        "repository": str(repository),
        "base_commit": str(base_commit),
        "execution_state_identity": str(execution_state_identity),
        "declared_result": outcome.declared_result,
        "detail": outcome.detail,
        "emitted_identities": outcome.emitted_identities,
        "fail_to_pass": dict(sorted(outcome.fail_to_pass.items())),
        "pass_to_pass": dict(sorted(outcome.pass_to_pass.items())),
    }
    payload[REPORT_DIGEST_FIELD] = compute_report_digest(payload)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def parse_report(data: bytes) -> dict[str, Any]:
    """Parse retained evaluator report bytes, failing closed on anything else."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise EvaluatorError("evaluator report is empty")
    try:
        payload = json.loads(bytes(data).decode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise EvaluatorError(f"evaluator report is not ASCII JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise EvaluatorError("evaluator report is not a JSON object")
    if payload.get("schema") != REPORT_SCHEMA:
        raise EvaluatorError(
            f"evaluator report schema {payload.get('schema')!r} != {REPORT_SCHEMA!r}"
        )
    if payload.get("evaluator_id") != EVALUATOR_ID:
        raise EvaluatorError(
            f"evaluator report was produced by {payload.get('evaluator_id')!r}, "
            f"not {EVALUATOR_ID!r}"
        )
    result = payload.get("declared_result")
    if result not in ("pass", "fail"):
        raise EvaluatorError(f"evaluator report declares an invalid result {result!r}")

    # Self-integrity: any edit to any field (including declared_result) breaks
    # this digest. Without it a hand-written report could be internally
    # consistent and believed.
    declared_digest = payload.get(REPORT_DIGEST_FIELD)
    if not declared_digest:
        raise EvaluatorError(
            "evaluator report carries no report_digest; it cannot be shown to be "
            "unmodified since the evaluator produced it"
        )
    actual = compute_report_digest(payload)
    if declared_digest != actual:
        raise EvaluatorError(
            f"evaluator report report_digest {declared_digest!r} does not match its "
            f"own content ({actual!r}); the report has been altered after the "
            "evaluator produced it"
        )
    return dict(payload)


def verdict_from_report_payload(
    payload: Mapping[str, Any],
) -> tuple[str | None, str]:
    """THE single authority for "what does this report say?".

    Returns ``("pass"|"fail"|None, detail)``. ``None`` means the payload cannot
    establish a result, which every caller must treat as fail-closed.

    This function exists to eliminate producer/verifier drift. Three other F2
    modules previously re-implemented "are all FAIL_TO_PASS nodes passed?" with
    their own hard-coded status sets, and those sets had already diverged: none
    of them accepted ``missing``, which this evaluator emits for a declared test
    absent from the evidence. A legitimately produced report could therefore be
    rejected downstream as an *unrecognised status* -- a protocol error -- rather
    than scored as the ``fail`` it actually is. All consumers now call this.
    """
    if not isinstance(payload, Mapping):
        return None, "report is not a JSON object"
    f2p = payload.get("fail_to_pass")
    if not isinstance(f2p, Mapping) or not f2p:
        return None, "report carries no non-empty 'fail_to_pass' map"
    statuses = {str(k): str(v) for k, v in f2p.items()}
    unknown = sorted({v for v in statuses.values() if v != STATUS_PASSED} - NOT_PASSED)
    if unknown:
        return None, f"report carries unknown test statuses: {unknown}"
    not_passed = {k: v for k, v in sorted(statuses.items()) if v in NOT_PASSED}
    if not not_passed:
        return "pass", f"all {len(statuses)} declared FAIL_TO_PASS nodes passed"
    return "fail", (
        f"{len(not_passed)} of {len(statuses)} FAIL_TO_PASS nodes not passed: "
        + ", ".join(f"{k}={v}" for k, v in list(not_passed.items())[:8])
    )


def derive_result_protocol(
    data: bytes, context: Mapping[str, Any] | None = None
) -> tuple[str | None, str]:
    """Result-derivation adapter registered with ``f2_governance``.

    Re-derives the verdict from the RETAINED evaluator report rather than
    trusting any producer declaration. Returns ``(None, reason)`` whenever the
    artifact cannot establish the result, which the caller treats as fail-closed.
    """
    try:
        payload = parse_report(data)
    except EvaluatorError as exc:
        return None, str(exc)

    declared_digest = payload.get("implementation_digest")
    if declared_digest != implementation_digest():
        return None, (
            "evaluator report implementation_digest "
            f"{declared_digest!r} does not match this evaluator's digest "
            f"{implementation_digest()!r}; the bytes that produced the verdict "
            "are not provable"
        )

    if context:
        want_state = str(context.get("execution_state_identity") or "")
        got_state = str(payload.get("execution_state_identity") or "")
        if want_state and got_state and want_state != got_state:
            return None, (
                f"evaluator report execution_state_identity {got_state!r} does not "
                f"match the bundle's {want_state!r}"
            )
        want_inst = str(context.get("instance_id") or "")
        got_inst = str(payload.get("instance_id") or "")
        if want_inst and got_inst and want_inst != got_inst:
            return None, (
                f"evaluator report instance_id {got_inst!r} does not match the "
                f"bundle's {want_inst!r}"
            )

    f2p = payload.get("fail_to_pass")
    if not isinstance(f2p, Mapping) or not f2p:
        return None, "evaluator report carries no non-empty FAIL_TO_PASS map"

    statuses = {str(k): str(v) for k, v in f2p.items()}
    unknown = sorted({v for v in statuses.values() if v != STATUS_PASSED} - NOT_PASSED)
    if unknown:
        return None, f"evaluator report carries unknown statuses: {unknown}"
    not_passed = {k: v for k, v in sorted(statuses.items()) if v in NOT_PASSED}
    if not not_passed:
        return "pass", f"all {len(statuses)} declared FAIL_TO_PASS nodes passed"
    return "fail", (
        f"{len(not_passed)} of {len(statuses)} FAIL_TO_PASS nodes not passed: "
        + ", ".join(f"{k}={v}" for k, v in list(not_passed.items())[:8])
    )


# --------------------------------------------------------------------------
# CLI. The harness invokes this as an ordinary subprocess step so the producer
# is an external, independently invokable program rather than an in-process
# helper that could share state with the code it is supposed to check.
# --------------------------------------------------------------------------
def _load_declared(contract_path: str | None, inline: Sequence[str] | None) -> list[str]:
    """Resolve a declared node-id list from a contract JSON file or flags."""
    import json as _json
    import pathlib as _pathlib

    if inline:
        return [str(v).strip() for v in inline if str(v).strip()]
    if not contract_path:
        raise EvaluatorError("no FAIL_TO_PASS contract supplied (--fail-to-pass or --contract)")
    p = _pathlib.Path(contract_path)
    if not p.is_file():
        raise EvaluatorError(f"contract file does not exist: {p}")
    try:
        payload = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise EvaluatorError(f"contract file is not valid JSON: {exc}") from exc
    if isinstance(payload, list):
        return [str(v).strip() for v in payload if str(v).strip()]
    if isinstance(payload, Mapping):
        for key in ("fail_to_pass", "FAIL_TO_PASS"):
            val = payload.get(key)
            if isinstance(val, list):
                return [str(v).strip() for v in val if str(v).strip()]
    raise EvaluatorError(
        "contract file must be a list of node ids or an object carrying "
        "'fail_to_pass'/'FAIL_TO_PASS'"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit code 0 on 'pass', 3 on 'fail', 2 on evaluator error."""
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        prog="python -m qwen_train.f2_evaluator",
        description="Deterministic F2 pytest/JUnit task-outcome evaluator.",
    )
    ap.add_argument("--junit", required=True, help="path to retained junit.xml")
    ap.add_argument("--contract", help="JSON file carrying the declared node ids")
    ap.add_argument("--fail-to-pass", nargs="*", default=None, help="declared FAIL_TO_PASS node ids")
    ap.add_argument("--pass-to-pass", nargs="*", default=None, help="declared PASS_TO_PASS node ids")
    ap.add_argument("--out", required=True, help="path to write the canonical report")
    ap.add_argument("--instance-id", default="")
    ap.add_argument("--repository", default="")
    ap.add_argument("--base-commit", default="")
    ap.add_argument("--execution-state", default="", choices=["", "base", "gold"])
    args = ap.parse_args(list(argv) if argv is not None else None)

    try:
        f2p = _load_declared(args.contract, args.fail_to_pass)
        p2p = [str(v).strip() for v in (args.pass_to_pass or []) if str(v).strip()]
        report = produce_report(
            junit_path=args.junit,
            fail_to_pass=f2p,
            pass_to_pass=p2p or None,
            out_path=args.out,
            instance_id=args.instance_id,
            repository=args.repository,
            base_commit=args.base_commit,
            execution_state_identity=args.execution_state,
        )
    except EvaluatorError as exc:
        print(f"f2_evaluator: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"f2_evaluator: unexpected evaluator error: {type(exc).__name__}: {exc}")
        return 2

    print(_json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["declared_result"] == "pass" else 3


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
