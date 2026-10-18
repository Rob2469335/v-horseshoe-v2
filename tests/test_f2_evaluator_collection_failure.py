"""Regression tests for the collection-failure classification defect.

DEFECT (proven 2026-10-18 against retained Phase-3 evidence).  pytest reports a
module it could not IMPORT as
``<testcase classname="" name="<module>"><error message="collection failure">``.
``evaluate_execution`` resolved every declared FAIL_TO_PASS node absent from the
JUnit to ``missing``, and ``missing`` is in ``NOT_PASSED``, so the producer
declared ``fail`` on BOTH sides.  ``f2_s8_compose._result_from_bundles`` then
mapped base!=PASS -> ``gold_fail``, which the ledger records as
``outcome_class="scientific"`` -- terminal, never re-run.  A missing dependency
therefore produced the SAME record as a genuine "base fails and gold still
fails", permanently burning the task.

Ten retained Phase-3 gold JUnit files carry that signature; eight are
collection-failure-only (cfn-lint-4236, conan-18832/-18840, fastmcp-1632,
matplotlib-30467/-30475, electrum-10172, dspy-8718).

CORRECTION.  A collection failure explains an ABSENT target node, so the run
establishes no scientific result: it is ``error`` (environment/infrastructure,
retryable), not ``fail``.  The rule is narrow -- collection errors present AND a
declared node missing -- so a module that collected and genuinely failed still
scores ``fail``, and any run with no collection errors is untouched.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from qwen_train import f2_evidence as ev
from qwen_train import f2_s8_compose as compose
from qwen_train.f2_evaluator import (
    RESULT_ERROR,
    STATUS_MISSING,
    EvaluatorError,
    count_collection_errors,
    evaluate_execution,
    verdict_from_report_payload,
)


# --------------------------------------------------------------------------- helpers
def _junit(*cases: str) -> bytes:
    return ("<testsuites><testsuite name=\"pytest\">" + "".join(cases)
            + "</testsuite></testsuites>").encode()


def _collection_error(module: str, exc: str = "ModuleNotFoundError: No module named 'fastapi'") -> str:
    return (f'<testcase classname="" name="{module}" time="0.000">'
            f'<error message="collection failure">ImportError while importing test '
            f'module {module}.\nE   {exc}</error></testcase>')


def _real_fail(classname: str, name: str) -> str:
    return (f'<testcase classname="{classname}" name="{name}" time="0.1">'
            f'<failure message="assert 1 == 2">boom</failure></testcase>')


def _real_pass(classname: str, name: str) -> str:
    return f'<testcase classname="{classname}" name="{name}" time="0.1"/>'


F2P = ["pkg/tests/test_mod.py::test_target"]
CLS = "pkg.tests.test_mod"
NAME = "test_target"


# ------------------------------------------------------------------- the defect
class TestCollectionFailureIsInconclusive:
    def test_collection_error_with_no_target_node_ran_is_error_not_fail(self):
        """The exact Phase-3 signature: only a collection failure is in the XML."""
        xml = _junit(_collection_error("pkg.tests.test_mod"))
        out = evaluate_execution(xml, fail_to_pass=F2P, instance_id="t", execution_state_identity="gold")
        assert out.declared_result == RESULT_ERROR
        assert out.collection_errors == 1
        assert out.fail_to_pass[F2P[0]] == STATUS_MISSING
        assert "INCONCLUSIVE" in out.detail

    def test_plugin_import_failure_that_prevents_collection_is_error(self):
        xml = _junit(_collection_error("pkg.tests.test_mod", "ModuleNotFoundError: No module named 'pytest_django'"))
        out = evaluate_execution(xml, fail_to_pass=F2P)
        assert out.declared_result == RESULT_ERROR

    def test_dependency_failure_on_many_modules_is_error(self):
        xml = _junit(*[_collection_error(f"pkg.tests.test_m{i}") for i in range(5)])
        out = evaluate_execution(xml, fail_to_pass=F2P)
        assert out.declared_result == RESULT_ERROR
        assert out.collection_errors == 5

    def test_the_deriver_reaches_the_same_verdict_as_the_producer(self):
        """Producer and deriver must not drift: they apply the identical rule."""
        payload = {
            "fail_to_pass": {F2P[0]: STATUS_MISSING},
            "pass_to_pass": {},
            "collection_errors": 3,
        }
        assert verdict_from_report_payload(payload)[0] == RESULT_ERROR


# ------------------------------------------------- genuine outcomes are preserved
class TestGenuineOutcomesUnchanged:
    def test_a_real_assertion_failure_is_still_fail(self):
        """Valid collection + a real <failure> => a genuine task outcome, not error."""
        xml = _junit(_real_fail(CLS, NAME))
        out = evaluate_execution(xml, fail_to_pass=F2P)
        assert out.declared_result == "fail"
        assert out.collection_errors == 0

    def test_a_real_pass_is_still_pass(self):
        xml = _junit(_real_pass(CLS, NAME))
        out = evaluate_execution(xml, fail_to_pass=F2P)
        assert out.declared_result == "pass"

    def test_absent_target_node_without_any_collection_error_is_still_fail(self):
        """Absence alone is NOT infrastructure evidence -- must not be reclassified."""
        xml = _junit(_real_pass(CLS, "test_something_else"))
        out = evaluate_execution(xml, fail_to_pass=F2P)
        assert out.declared_result == "fail"
        assert out.collection_errors == 0

    def test_a_collection_error_elsewhere_does_not_excuse_a_real_failure(self):
        """Narrow rule: the declared node RAN and failed, so it is still 'fail'."""
        xml = _junit(_real_fail(CLS, NAME), _collection_error("pkg.tests.test_other"))
        out = evaluate_execution(xml, fail_to_pass=F2P)
        assert out.declared_result == "fail"
        assert out.collection_errors == 1

    def test_a_skipped_target_is_not_reclassified_without_collection_errors(self):
        xml = _junit(f'<testcase classname="{CLS}" name="{NAME}"><skipped message="no numpy"/></testcase>')
        out = evaluate_execution(xml, fail_to_pass=F2P)
        assert out.declared_result == "fail"
        assert out.collection_errors == 0


# ------------------------------------------------------------------ fail closed
class TestFailClosed:
    def test_missing_junit_is_refused(self):
        with pytest.raises(EvaluatorError):
            evaluate_execution(b"", fail_to_pass=F2P)

    def test_truncated_xml_is_refused(self):
        with pytest.raises(EvaluatorError):
            evaluate_execution(b"<testsuites><testsuite>", fail_to_pass=F2P)

    def test_junit_with_no_testcases_is_refused(self):
        with pytest.raises(EvaluatorError):
            evaluate_execution(_junit(), fail_to_pass=F2P)

    def test_count_collection_errors_refuses_unparseable_bytes(self):
        with pytest.raises(EvaluatorError):
            count_collection_errors(b"<not xml")

    def test_count_collection_errors_does_not_count_a_real_error(self):
        """A genuine error carries the test's own identity, not an empty classname."""
        xml = _junit(f'<testcase classname="{CLS}" name="{NAME}"><error message="boom"/></testcase>')
        assert count_collection_errors(xml) == 0


# -------------------------------------------------- the composer cannot burn a task
class TestComposerMapping:
    def _bundle(self, declared_result: str):
        return SimpleNamespace(declared_result=declared_result)

    def _spec(self):
        return SimpleNamespace(task_id="t__t-1")

    def test_an_inconclusive_side_becomes_infrastructure_not_gold_fail(self):
        """THE regression: a collection failure must never be terminal scientific."""
        res = compose._result_from_bundles(
            self._spec(), self._bundle(ev.RESULT_ERROR), self._bundle(ev.RESULT_ERROR))
        assert res.outcome == "infrastructure_failure"
        assert res.outcome == "infrastructure_failure"
        from qwen_train.f2_s8_factory import INFRASTRUCTURE_OUTCOMES, SCIENTIFIC_OUTCOMES
        assert res.outcome in INFRASTRUCTURE_OUTCOMES
        assert res.outcome not in SCIENTIFIC_OUTCOMES

    def test_an_inconclusive_base_side_alone_is_also_infrastructure(self):
        res = compose._result_from_bundles(
            self._spec(), self._bundle(ev.RESULT_ERROR), self._bundle(ev.RESULT_FAIL))
        assert res.outcome == "infrastructure_failure"

    def test_a_genuine_base_fail_and_gold_fail_stays_gold_fail(self):
        res = compose._result_from_bundles(
            self._spec(), self._bundle(ev.RESULT_FAIL), self._bundle(ev.RESULT_FAIL))
        assert res.outcome == "gold_fail"

    def test_a_genuine_base_pass_stays_base_pass(self):
        res = compose._result_from_bundles(
            self._spec(), self._bundle(ev.RESULT_PASS), self._bundle(ev.RESULT_PASS))
        assert res.outcome == "base_pass"


# ------------------------------------------------------------------- vocabulary
class TestVocabularyInStep:
    def test_result_error_matches_f2_evidence(self):
        assert RESULT_ERROR == ev.RESULT_ERROR

    def test_result_error_is_a_valid_execution_result(self):
        assert RESULT_ERROR in ev.VALID_RESULTS


# ------------------------------------------------------- real retained artifacts
@pytest.mark.skipif(
    not (__import__("pathlib").Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_staging_host_phase3")).is_dir(),
    reason="retained Phase-3 evidence not present on this host",
)
class TestReportedPhase3Artifacts:
    """Re-derive the disputed outcomes from the ACTUAL retained JUnit bytes."""

    STG = __import__("pathlib").Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_staging_host_phase3")
    INP = __import__("pathlib").Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs_phase2")

    COLLECTION_FAILURE_TASKS = (
        "aws-cloudformation__cfn-lint-4236", "conan-io__conan-18832",
        "conan-io__conan-18840", "jlowin__fastmcp-1632",
        "matplotlib__matplotlib-30467", "matplotlib__matplotlib-30475",
        "spesmilo__electrum-10172", "stanfordnlp__dspy-8718",
    )

    def _eval(self, task: str, side: str):
        import json
        rec = json.loads((self.INP / f"{task}.json").read_text(encoding="utf-8"))
        f2p = rec.get("FAIL_TO_PASS") or rec.get("fail_to_pass") or []
        p2p = rec.get("PASS_TO_PASS") or rec.get("pass_to_pass") or []
        xml = (self.STG / f"{task}.{side}.junit.hostcopy.xml").read_bytes()
        return evaluate_execution(xml, fail_to_pass=f2p, pass_to_pass=p2p,
                                  instance_id=task, execution_state_identity=side)

    @pytest.mark.parametrize("task", COLLECTION_FAILURE_TASKS)
    def test_recorded_as_inconclusive_not_a_task_outcome(self, task):
        for side in ("base", "gold"):
            out = self._eval(task, side)
            assert out.collection_errors > 0, (task, side)
            assert out.declared_result == RESULT_ERROR, (task, side, out.declared_result)

    def test_the_two_admitted_tasks_keep_their_scientific_verdict(self):
        """No false positives: genuine admissions must be untouched."""
        for task in ("networkx__networkx-8211", "openai__openai-agents-python-1601"):
            assert self._eval(task, "base").declared_result == "fail"
            assert self._eval(task, "gold").declared_result == "pass"
