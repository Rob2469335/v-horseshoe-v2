"""D4 regression tests: the post-batch audit must be able to FAIL.

Every test here drives the production entry point, :func:`audit_batch`, over a
real on-disk report / manifest / JUnit layout.  Several tests are deliberately
shaped so that the pre-repair behaviour fails them:

* the 345-collection-error fixture would have counted 1 under a first-match
  implementation;
* the swapped-hash test would have passed when the declared hash was read but
  never compared;
* the infrastructure-row-with-declared-hash test would have passed when
  infrastructure rows were skipped wholesale.

The suite also proves the audit does not mutate the evidence or any ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train.f2_evaluator import evaluate_execution  # noqa: E402
from qwen_train.f2_s8_post_batch_audit import (  # noqa: E402
    BatchAuditBlocked,
    _group_evidence,
    audit_batch,
    load_audit,
    require_passing,
    write_audit,
)
from qwen_train.f2_s8_evidence_index import (  # noqa: E402
    build_index,
    index_digest,
    load_index,
    write_index,
)

TASK = "acme__repo-1"
NODE = "tests/test_mod.py::test_target"
CLS = "tests.test_mod"
NAME = "test_target"
REG_SHA = "a" * 64
REVISION = "b" * 40
RUN_ID = "c" * 64
PATCH = (
    "diff --git a/pkg/newmod.py b/pkg/newmod.py\n"
    "--- /dev/null\n"
    "+++ b/pkg/newmod.py\n"
    "@@ -0,0 +1 @@\n"
    "+VALUE = 1\n"
)


def _junit(*cases: str) -> bytes:
    return (
        '<testsuites><testsuite name="pytest">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    ).encode()


def _pass() -> str:
    return f'<testcase classname="{CLS}" name="{NAME}"/>'


def _fail() -> str:
    return (
        f'<testcase classname="{CLS}" name="{NAME}">'
        '<failure message="assert 0 == 1">boom</failure></testcase>'
    )


def _collection(module: str, index: int = 0) -> str:
    return (
        f'<testcase classname="" name="mod{index}">'
        '<error message="collection failure">'
        f"ImportError while importing test module tests.test_{index}.\n"
        f"E   ModuleNotFoundError: No module named '{module}'"
        "</error></testcase>"
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _declared(junit: bytes, *, changed_modules: tuple[str, ...]) -> tuple[int, str, str]:
    out = evaluate_execution(
        junit,
        fail_to_pass=[NODE],
        pass_to_pass=[],
        instance_id=TASK,
        execution_state_identity="base",
        changed_modules=changed_modules,
    )
    return int(out.collection_errors), str(out.collection_error_cause), str(
        out.declared_result
    )


SV = pathlib.Path  # kept for readability in fixture paths


def _fixture(
    root: pathlib.Path,
    *,
    base_junit: bytes | None = None,
    gold_junit: bytes | None = None,
    patch: str = PATCH,
    base_row_overrides: dict | None = None,
    gold_row_overrides: dict | None = None,
    row_overrides: dict | None = None,
    manifest_status: str = "built",
    write_base: bool = True,
    write_gold: bool = True,
) -> dict[str, pathlib.Path]:
    """Create a complete, internally consistent batch on disk."""
    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)

    base_junit = base_junit if base_junit is not None else _junit(_fail())
    gold_junit = gold_junit if gold_junit is not None else _junit(_pass())

    base_path = staging / f"{TASK}.base.junit.hostcopy.xml"
    gold_path = staging / f"{TASK}.gold.junit.hostcopy.xml"
    if write_base:
        base_path.write_bytes(base_junit)
    if write_gold:
        gold_path.write_bytes(gold_junit)

    (inputs / f"{TASK}.json").write_text(
        json.dumps({
            "instance_id": TASK,
            "FAIL_TO_PASS": [NODE],
            "PASS_TO_PASS": [],
            "patch": patch,
            "repo": "acme/repo",
            "base_commit": "d" * 40,
        }),
        encoding="utf-8",
    )

    manifest = root / "build_manifest.json"
    manifest.write_text(
        json.dumps({TASK: {"status": manifest_status, "workspace": str(root / "ws")}}),
        encoding="utf-8",
    )
    manifest_digest = _sha(manifest.read_bytes())

    changed = ("pkg.newmod",)
    base_errors, base_cause, base_verdict = _declared(base_junit, changed_modules=changed)
    gold_errors, gold_cause, gold_verdict = _declared(gold_junit, changed_modules=changed)

    row = {
        "task_id": TASK,
        "attempted": True,
        "admitted": bool(write_base and write_gold),
        "outcome": "base_fail_gold_pass" if write_base and write_gold else "infrastructure_failure",
        "outcome_class": "scientific" if write_base and write_gold else "infrastructure",
        "base_verdict": base_verdict,
        "gold_verdict": gold_verdict,
        "base_junit_sha256": _sha(base_junit) if write_base else "",
        "gold_junit_sha256": _sha(gold_junit) if write_gold else "",
        "base_collection_errors": base_errors if write_base else None,
        "gold_collection_errors": gold_errors if write_gold else None,
        "base_collection_error_cause": base_cause if write_base else "",
        "gold_collection_error_cause": gold_cause if write_gold else "",
        "failure_class": "" if write_base and write_gold else "no_artifact_reported",
        "reason_code": "" if write_base and write_gold else "worker_crash",
        "ledger_record": {
            "record_digest": "f" * 64,
            "registration_sha256": REG_SHA,
        },
    }
    row.update(row_overrides or {})
    if base_row_overrides:
        row.update(base_row_overrides)
    if gold_row_overrides:
        row.update(gold_row_overrides)

    report = {
        "tasks": [row],
        "totals": {
            "attempted": 1,
            "admitted": 1 if row["admitted"] else 0,
            "completed": 1 if row["outcome_class"] == "scientific" else 0,
            "failed": 0,
            "infrastructure_invalid": 0 if row["outcome_class"] == "scientific" else 1,
        },
        "registration_id": "reg-1",
        "registration_sha256": REG_SHA,
        "manifest_path": str(manifest.resolve()),
        "manifest_digest": manifest_digest,
        "staging_root": str(staging.resolve()),
        "inputs_dir": str(inputs.resolve()),
        "repository_revision": REVISION,
        "run_id": RUN_ID,
        "completed_at": "2026-10-18T00:00:00Z",
    }
    results = root / "pilot_results.json"
    results.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return {
        "root": root,
        "staging": staging,
        "inputs": inputs,
        "manifest": manifest,
        "results": results,
        "base_path": base_path,
        "gold_path": gold_path,
        "report": report,
    }


def _audit(
    fx: dict[str, pathlib.Path],
    *,
    evidence_index: object = "auto",
    require_evidence_index: bool = True,
    **over,
) -> dict:
    kwargs = dict(
        batch_results_path=fx["results"],
        staging_root=fx["staging"],
        manifest_path=fx["manifest"],
        inputs_dir=fx["inputs"],
        expected_manifest_digest=_sha(fx["manifest"].read_bytes()),
        expected_repository_revision=REVISION,
        expected_run_id=RUN_ID,
        expected_staging_root=fx["staging"],
        require_evidence_index=require_evidence_index,
    )
    if evidence_index == "auto":
        _index, index_path = build_index(
            batch_results_path=fx["results"],
            staging_root=fx["staging"],
            output_dir=fx["root"] / "evidence_index",
        )
        kwargs["evidence_index_path"] = index_path
    elif evidence_index is None:
        kwargs["evidence_index_path"] = None
    else:
        kwargs["evidence_index_path"] = evidence_index
    kwargs.update(over)
    return audit_batch(**kwargs)


def _codes(result: dict) -> set[str]:
    return set(result["reason_codes"])


def _collection_batch(root: pathlib.Path, *, errors: int = 2) -> dict[str, pathlib.Path]:
    """A batch whose base side REALLY has ``errors`` collection failures, then
    a report whose declared count is forged to zero."""
    junit = _junit(*(_collection("pkg.newmod", i) for i in range(errors)))
    fx = _fixture(root, base_junit=junit)
    report = json.loads(fx["results"].read_text(encoding="utf-8"))
    assert report["tasks"][0]["base_collection_errors"] == errors
    report["tasks"][0]["base_collection_errors"] = 0
    fx["results"].write_text(
        json.dumps(report, indent=1, sort_keys=True), encoding="utf-8"
    )
    return fx


# ---------------------------------------------------------------------------
# valid batch / determinism / read-only
# ---------------------------------------------------------------------------
class TestValidBatch:
    def test_a_valid_batch_passes(self, tmp_path):
        fx = _fixture(tmp_path)
        result = _audit(fx)
        assert result["verdict"] == "PASS", result["discrepancies"]
        assert result["reason_codes"] == []
        assert result["counts"]["tasks_passed"] == 1
        assert result["counts"]["tasks_failed"] == 0
        assert result["counts"]["evidence_files_inspected"] == 2

    def test_the_result_is_deterministic_on_replay(self, tmp_path):
        fx = _fixture(tmp_path)
        first = _audit(fx)
        second = _audit(fx)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
        assert first["audit_digest"] == second["audit_digest"]

    def test_a_valid_audit_mutates_nothing(self, tmp_path):
        fx = _fixture(tmp_path)
        ledger = tmp_path / "ledger.jsonl"
        ledger.write_text('{"row": 1}\n', encoding="utf-8")
        watched = [fx["results"], fx["manifest"], fx["base_path"], fx["gold_path"], ledger]
        before = {str(p): _sha(p.read_bytes()) for p in watched}
        _audit(fx)
        after = {str(p): _sha(p.read_bytes()) for p in watched}
        assert before == after

    def test_a_written_artifact_round_trips_and_is_integrity_checked(self, tmp_path):
        fx = _fixture(tmp_path)
        result = _audit(fx)
        path = write_audit(tmp_path / "audit.json", result)
        loaded = load_audit(path)
        assert loaded["audit_digest"] == result["audit_digest"]
        assert require_passing(loaded)["verdict"] == "PASS"
        tampered = dict(loaded)
        tampered["verdict"] = "FAIL"
        with pytest.raises(BatchAuditBlocked):
            require_passing(tampered)


# ---------------------------------------------------------------------------
# forged / wrong values
# ---------------------------------------------------------------------------
class TestForgedDeclaredValues:
    def test_a_forged_collection_error_count_is_detected(self, tmp_path):
        junit = _junit(_collection("pkg.newmod", 0), _collection("pkg.newmod", 1))
        fx = _fixture(tmp_path, base_junit=junit)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        assert report["tasks"][0]["base_collection_errors"] == 2
        report["tasks"][0]["base_collection_errors"] = 0  # forged
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert result["verdict"] == "FAIL"
        assert "COLLECTION_ERRORS_MISMATCH" in _codes(result)

    def test_a_345_error_fixture_is_counted_in_full(self, tmp_path):
        # The pre-repair auditing function returned after the FIRST match (1).
        junit = _junit(*(_collection("pkg.newmod", i) for i in range(345)))
        fx = _fixture(tmp_path, base_junit=junit)
        result = _audit(fx)
        assert result["verdict"] == "PASS", result["discrepancies"]
        base = result["tasks"][0]["sides"]["base"]
        assert base["collection_errors"] == 345
        assert base["collection_error_cause"] == "project"
        assert base["verdict"] == "fail"

    def test_a_wrong_cause_is_detected(self, tmp_path):
        env_junit = _junit(_collection("fastapi", 0))
        fx = _fixture(tmp_path, base_junit=env_junit)
        # The fixture derives the true cause (environment); overwrite it with a lie.
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["tasks"][0]["base_collection_error_cause"] = "project"
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "CAUSE_MISMATCH" in _codes(result)

    def test_a_wrong_verdict_is_detected(self, tmp_path):
        fx = _fixture(tmp_path, base_row_overrides={"base_verdict": "pass"})
        result = _audit(fx)
        assert "VERDICT_MISMATCH" in _codes(result)

    def test_a_missing_declared_count_is_a_failure(self, tmp_path):
        fx = _fixture(tmp_path)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        del report["tasks"][0]["base_collection_errors"]
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "DECLARED_COLLECTION_ERRORS_MISSING" in _codes(result)

    def test_a_missing_declared_cause_is_a_failure(self, tmp_path):
        fx = _fixture(tmp_path)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["tasks"][0]["base_collection_error_cause"] = ""
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "DECLARED_CAUSE_MISSING" in _codes(result)

    def test_a_placeholder_verdict_is_a_failure(self, tmp_path):
        # The historical report wrote "produced" here, not a verdict.
        fx = _fixture(tmp_path, base_row_overrides={"base_verdict": "produced"})
        result = _audit(fx)
        assert "DECLARED_VERDICT_MALFORMED" in _codes(result)

    def test_a_forged_totals_admission_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["totals"]["admitted"] = 5
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "TOTALS_MISMATCH" in _codes(result)


# ---------------------------------------------------------------------------
# evidence identity / missing / malformed
# ---------------------------------------------------------------------------
class TestEvidenceIdentity:
    def test_missing_expected_evidence_is_detected(self, tmp_path):
        fx = _fixture(tmp_path, write_gold=False)
        # The row CLAIMS gold evidence (a declared hash) but the file is absent.
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["tasks"][0]["gold_junit_sha256"] = _sha(_junit(_pass()))
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "JUNIT_MISSING" in _codes(result)
        assert result["verdict"] == "FAIL"

    def test_a_swapped_junit_is_detected_by_hash(self, tmp_path):
        fx = _fixture(tmp_path)
        # Put the GOLD bytes under the BASE name.  The declared base hash still
        # names the original base bytes, so the hash comparison must fail.
        fx["base_path"].write_bytes(fx["gold_path"].read_bytes())
        result = _audit(fx)
        assert "JUNIT_HASH_MISMATCH" in _codes(result)

    def test_malformed_junit_is_detected(self, tmp_path):
        garbage = b"<testsuites><not-closed"
        fx = _fixture(tmp_path)
        fx["base_path"].write_bytes(garbage)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["tasks"][0]["base_junit_sha256"] = _sha(garbage)
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "JUNIT_MALFORMED" in _codes(result)

    def test_evidence_for_another_task_is_unexpected(self, tmp_path):
        fx = _fixture(tmp_path)
        # A stale file from a different batch/task in the same staging root.
        (fx["staging"] / "other__repo-9.base.junit.hostcopy.xml").write_bytes(_junit(_fail()))
        result = _audit(fx)
        assert "UNEXPECTED_EVIDENCE_FILE" in _codes(result)

    def test_stale_evidence_with_the_right_name_is_a_hash_mismatch(self, tmp_path):
        fx = _fixture(tmp_path)
        # An earlier run's bytes under this run's name.
        fx["gold_path"].write_bytes(_junit("<testcase classname='x' name='y'/>"))
        result = _audit(fx)
        assert "JUNIT_HASH_MISMATCH" in _codes(result)

    def test_an_undeclared_evidence_file_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["tasks"][0]["base_junit_sha256"] = ""
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "JUNIT_UNDECLARED" in _codes(result)

    def test_duplicate_evidence_is_not_silently_collapsed(self):
        a = pathlib.Path("acme__repo-1.base.junit.hostcopy.xml")
        b = pathlib.Path("ACME__REPO-1.base.junit.hostcopy.xml")
        recognised, unexplained = _group_evidence([a, b])
        assert len(recognised) == 2
        # The collision is surfaced, not dropped.
        assert unexplained

    def test_case_distinct_task_ids_are_not_duplicates_on_posix(self, monkeypatch):
        """POSIX is case-sensitive; only the HOST's normcase may collapse names."""
        monkeypatch.setattr(os.path, "normcase", lambda p: p)
        a = pathlib.Path("Foo.base.junit.hostcopy.xml")
        b = pathlib.Path("foo.base.junit.hostcopy.xml")
        recognised, unexplained = _group_evidence([a, b])
        assert len(recognised) == 2
        assert unexplained == []

    def test_a_different_staging_root_is_refused(self, tmp_path):
        fx = _fixture(tmp_path)
        other = tmp_path / "other-staging"
        other.mkdir()
        result = _audit(fx, expected_staging_root=other)
        assert "STAGING_ROOT_MISMATCH" in _codes(result)


# ---------------------------------------------------------------------------
# infrastructure rows
# ---------------------------------------------------------------------------
class TestInfrastructureRows:
    def test_a_crashed_row_without_evidence_is_validly_audited(self, tmp_path):
        fx = _fixture(tmp_path, write_base=False, write_gold=False)
        # No evidence is expected for a registered infrastructure failure.
        result = _audit(fx)
        assert result["verdict"] == "PASS", result["discrepancies"]
        assert result["tasks"][0]["sides"]["base"]["status"] == "no_evidence_expected"

    def test_a_crashed_row_with_a_declared_hash_but_no_file_fails(self, tmp_path):
        # The pre-repair audit skipped infrastructure rows wholesale.  A declared
        # hash means evidence was claimed, so its absence is a hard failure.
        fx = _fixture(tmp_path, write_base=False, write_gold=False)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["tasks"][0]["base_junit_sha256"] = "d" * 64
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "JUNIT_MISSING" in _codes(result)

    def test_an_infrastructure_row_with_an_unregistered_reason_fails(self, tmp_path):
        fx = _fixture(
            tmp_path, write_base=False, write_gold=False,
            row_overrides={"reason_code": "made_up_reason"},
        )
        result = _audit(fx)
        assert "INFRA_UNCLASSIFIED" in _codes(result)

    def test_a_scientific_row_with_no_evidence_fails(self, tmp_path):
        fx = _fixture(
            tmp_path, write_base=False, write_gold=False,
            row_overrides={"outcome_class": "scientific", "outcome": "gold_fail",
                           "admitted": False, "reason_code": "", "failure_class": ""},
        )
        result = _audit(fx)
        assert "EVIDENCE_MISSING" in _codes(result)

    def test_a_pre_execution_ledger_refusal_is_validly_audited(self, tmp_path):
        fx = _fixture(
            tmp_path, write_base=False, write_gold=False,
            row_overrides={"reason_code": "ledger_refused",
                           "failure_class": "ledger_refused"},
        )
        result = _audit(fx)
        assert result["verdict"] == "PASS", result["discrepancies"]


# ---------------------------------------------------------------------------
# batch / manifest binding
# ---------------------------------------------------------------------------
class TestBatchBinding:
    def test_a_manifest_digest_mismatch_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        fx["manifest"].write_text(
            json.dumps({TASK: {"status": "built", "workspace": "elsewhere"}}),
            encoding="utf-8",
        )
        result = _audit(fx)
        assert "MANIFEST_DIGEST_MISMATCH" in _codes(result)

    def test_a_missing_run_id_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        del report["run_id"]
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "RUN_ID_MISSING" in _codes(result)

    def test_a_wrong_run_id_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        result = _audit(fx, expected_run_id="e" * 64)
        assert "RUN_ID_MISMATCH" in _codes(result)

    def test_a_task_not_in_the_manifest_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        fx["manifest"].write_text(json.dumps({"different": {"status": "built"}}), encoding="utf-8")
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["manifest_digest"] = _sha(fx["manifest"].read_bytes())
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx, expected_manifest_digest=_sha(fx["manifest"].read_bytes()))
        assert "TASK_NOT_IN_MANIFEST" in _codes(result)

    def test_a_non_built_manifest_entry_is_detected(self, tmp_path):
        fx = _fixture(tmp_path, manifest_status="build_failure")
        result = _audit(fx)
        assert "TASK_NOT_BUILT" in _codes(result)

    def test_a_missing_contract_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        result = _audit(fx, inputs_dir=tmp_path / "absent")
        assert "CONTRACT_MALFORMED" in _codes(result)

    def test_a_wrong_revision_has_its_own_reason_code(self, tmp_path):
        fx = _fixture(tmp_path)
        result = _audit(fx, expected_repository_revision="e" * 40)
        assert "REVISION_MISMATCH" in _codes(result)

    def test_malformed_totals_do_not_crash_the_audit(self, tmp_path):
        fx = _fixture(tmp_path)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["totals"]["admitted"] = "not-an-int"
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert "TOTALS_MISMATCH" in _codes(result)

    def test_an_unreadable_report_returns_a_reviewable_fail(self, tmp_path):
        fx = _fixture(tmp_path)
        # A directory exists but cannot be read as a report file.
        result = audit_batch(
            batch_results_path=fx["staging"],
            staging_root=fx["staging"],
            manifest_path=fx["manifest"],
            inputs_dir=fx["inputs"],
        )
        assert result["verdict"] == "FAIL"
        assert "BATCH_UNREADABLE" in result["reason_codes"]

    def test_a_non_list_tasks_field_falls_back_to_results(self, tmp_path):
        fx = _fixture(tmp_path)
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["results"] = report.pop("tasks")
        report["tasks"] = "not-a-list"
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        result = _audit(fx)
        assert result["verdict"] == "PASS", result["discrepancies"]


# ---------------------------------------------------------------------------
# evidence sidecar index (owner decision E1)
# ---------------------------------------------------------------------------
class TestEvidenceIndex:
    def test_the_index_is_content_addressed(self, tmp_path):
        fx = _fixture(tmp_path)
        index, path = build_index(
            batch_results_path=fx["results"],
            staging_root=fx["staging"],
            output_dir=tmp_path / "idx",
        )
        assert path.stem == index_digest(index)
        assert load_index(path)["index_digest"] == path.stem

    def test_a_batch_without_an_index_fails(self, tmp_path):
        fx = _fixture(tmp_path)
        result = _audit(fx, evidence_index=None)
        assert "EVIDENCE_INDEX_MISSING" in _codes(result)

    def test_a_tampered_index_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        _index, path = build_index(
            batch_results_path=fx["results"],
            staging_root=fx["staging"],
            output_dir=tmp_path / "idx",
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"][0]["sides"]["base"]["junit_sha256"] = "0" * 64
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = _audit(fx, evidence_index=path)
        assert "EVIDENCE_INDEX_MALFORMED" in _codes(result)

    def test_a_substituted_index_is_detected(self, tmp_path):
        fx_a = _fixture(tmp_path / "a")
        fx_b = _fixture(tmp_path / "b")
        _index, foreign = build_index(
            batch_results_path=fx_b["results"],
            staging_root=fx_b["staging"],
            output_dir=tmp_path / "idx_b",
        )
        result = _audit(fx_a, evidence_index=foreign)
        assert "EVIDENCE_INDEX_BINDING_MISMATCH" in _codes(result)

    def test_an_index_hash_mismatch_is_detected(self, tmp_path):
        fx = _fixture(tmp_path)
        index, _path = build_index(
            batch_results_path=fx["results"],
            staging_root=fx["staging"],
            output_dir=tmp_path / "idx",
        )
        index["entries"][0]["sides"]["base"]["junit_sha256"] = "0" * 64
        replacement = write_index(tmp_path / "idx2", index)
        result = _audit(fx, evidence_index=replacement)
        assert "EVIDENCE_INDEX_HASH_MISMATCH" in _codes(result)

    def test_an_infrastructure_row_is_indexed_with_null_sides(self, tmp_path):
        fx = _fixture(tmp_path, write_base=False, write_gold=False)
        index, _path = build_index(
            batch_results_path=fx["results"],
            staging_root=fx["staging"],
            output_dir=tmp_path / "idx",
        )
        entry = index["entries"][0]
        assert entry["outcome_class"] == "infrastructure"
        assert entry["sides"]["base"]["junit_sha256"] is None
        assert entry["sides"]["gold"]["junit_sha256"] is None

    def test_a_pre_execution_refusal_is_indexed_unbound(self, tmp_path):
        fx = _fixture(
            tmp_path, write_base=False, write_gold=False,
            row_overrides={"outcome": "ledger_refused",
                           "reason_code": "ledger_refused",
                           "failure_class": "ledger_refused",
                           "admitted": False},
        )
        report = json.loads(fx["results"].read_text(encoding="utf-8"))
        report["tasks"][0]["ledger_record"] = {}
        fx["results"].write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        index, _path = build_index(
            batch_results_path=fx["results"],
            staging_root=fx["staging"],
            output_dir=tmp_path / "idx",
        )
        entry = index["entries"][0]
        assert entry["chain_bound"] is False
        assert entry["record_digest"] is None
        assert entry["outcome"] == "ledger_refused"


# ---------------------------------------------------------------------------
# hard stop
# ---------------------------------------------------------------------------
class TestHardStop:
    def test_a_failing_audit_blocks_progression(self, tmp_path):
        fx = _collection_batch(tmp_path)
        result = _audit(fx)
        assert result["verdict"] == "FAIL"
        with pytest.raises(BatchAuditBlocked):
            require_passing(result)
        path = write_audit(tmp_path / "audit.json", result)
        with pytest.raises(BatchAuditBlocked):
            require_passing(path)

    def test_preflight_blocks_credit_and_the_next_batch(self, tmp_path):
        from qwen_train import f2_preflight as pf

        fx = _collection_batch(tmp_path)
        failed = _audit(fx)
        path = write_audit(tmp_path / "failed_audit.json", failed)
        report = pf.run_preflight(batch_audit=path)
        by = {f["id"]: f for f in report["findings"]}
        assert by["population.batch_audit"]["status"] == "blocked"
        assert by["population.admission"]["status"] == "credit_blocked"
        assert report["result"] == "BLOCKED"

    def test_preflight_accepts_a_verified_pass(self, tmp_path):
        from qwen_train import f2_preflight as pf

        fx = _fixture(tmp_path)
        passed = _audit(fx)
        path = write_audit(tmp_path / "passed_audit.json", passed)
        report = pf.run_preflight(batch_audit=path)
        by = {f["id"]: f for f in report["findings"]}
        assert by["population.batch_audit"]["status"] == "verified"
        assert by["population.admission"]["status"] != "credit_blocked"

    def test_preflight_without_an_audit_is_blocked(self):
        from qwen_train import f2_preflight as pf

        by = {f["id"]: f for f in pf.run_preflight()["findings"]}
        assert by["population.batch_audit"]["status"] == "absent"

    def test_a_mapping_without_a_digest_is_blocked(self, tmp_path):
        fx = _fixture(tmp_path)
        result = _audit(fx)
        no_digest = {k: v for k, v in result.items() if k != "audit_digest"}
        with pytest.raises(BatchAuditBlocked):
            require_passing(no_digest)

    def test_a_tampered_mapping_is_blocked(self, tmp_path):
        failed = _audit(_collection_batch(tmp_path))
        tampered = dict(failed)
        tampered["verdict"] = "PASS"  # digest still covers the FAIL content
        with pytest.raises(BatchAuditBlocked):
            require_passing(tampered)

    def test_preflight_rejects_a_tampered_mapping(self, tmp_path):
        from qwen_train import f2_preflight as pf

        failed = _audit(_collection_batch(tmp_path))
        tampered = dict(failed)
        tampered["verdict"] = "PASS"
        report = pf.run_preflight(batch_audit=tampered)
        by = {f["id"]: f for f in report["findings"]}
        assert by["population.batch_audit"]["status"] == "unverifiable"
        assert by["population.admission"]["status"] != "credit_blocked"
