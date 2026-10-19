"""Owner-authorized ERROR-record contract and the F1/F3/F4/F6/F7 fixes.

The contract (owner decision, 2026-10-18): an environment or collection failure
may produce a PERSISTED, explicitly classified ERROR record. It is excluded from
the graded denominator and can never be an admission. It is never converted to
PASS or FAIL, and malformed / contradictory / missing-provenance evidence stays
fail-closed.

These tests pin the whole seam -- evaluator -> composer -> producer -> factory
outcome vocabulary -- because the defect being fixed was an inconsistency
BETWEEN those layers, not inside any one of them.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from types import SimpleNamespace

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train import f2_evaluator as ev  # noqa: E402
from qwen_train.f2_s8_acquisition import (  # noqa: E402
    AcquisitionError,
    expand_requirements_file,
    parse_requirements_lines,
    resolve_task_project_root,
)
from qwen_train.f2_s8_factory import (  # noqa: E402
    ADMITTED_OUTCOME,
    INFRASTRUCTURE_OUTCOMES,
    SCIENTIFIC_OUTCOMES,
)
from qwen_train.f2_s8_precheck import _classify_side  # noqa: E402
from qwen_train.f2_s8_producer import (  # noqa: E402
    ENVIRONMENT_ERROR_PREFIX,
    compose_execution_result,
)
from qwen_train.f2_s8_session import build_task_spec  # noqa: E402

COLLECT_ERROR_JUNIT = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1">
<testcase classname="" name="tests/test_mod.py" file="tests/test_mod.py">
<error message="collection failure">ModuleNotFoundError: No module named 'pandas'</error>
</testcase></testsuite></testsuites>"""

PATCH_ADDS_NEWMOD = "+++ b/pkg/newmod.py\n@@ -0,0 +1 @@\n+VALUE = 1\n"

TARGET = "tests/test_mod.py::test_target"


def _spec(f2p=(TARGET,), p2p=()):
    return build_task_spec(
        task_id="acme__repo-1", source="swe-bench-live", repo="acme/repo",
        base_commit="a" * 40, test_cmd="pytest -rA",
        fail_to_pass=list(f2p), pass_to_pass=list(p2p),
        registration_id="reg-1", registration_sha256="b" * 64,
        evaluator={"evaluator_id": "e", "version": "1",
                   "implementation_digest": "d" * 64,
                   "procedure_id": "p", "protocol_version": "1"},
    )


def _report(junit=COLLECT_ERROR_JUNIT, *, changed_modules=(), p2p=()):
    """A GENUINE report produced by the real evaluator, as bytes."""
    with tempfile.TemporaryDirectory() as td:
        junit_path = pathlib.Path(td) / "j.xml"
        junit_path.write_bytes(junit)
        out_path = pathlib.Path(td) / "r.json"
        ev.produce_report(
            junit_path=junit_path, fail_to_pass=[TARGET], pass_to_pass=list(p2p),
            out_path=out_path, instance_id="acme__repo-1",
            repository="acme/repo", base_commit="a" * 40,
            execution_state_identity="base", changed_modules=changed_modules,
        )
        return out_path.read_bytes()


def _receipt(junit_sha="f" * 64):
    return SimpleNamespace(payload={"junit": {"name": "x.junit.xml",
                                              "sha256": junit_sha,
                                              "size_bytes": 10}})


# ---------------------------------------------------------------------------
# F2 — the ERROR record is representable and explicit
# ---------------------------------------------------------------------------
class TestErrorIsRepresentable:
    def test_an_environment_failure_produces_a_real_report(self):
        """Before the contract this raised: 'invalid result error'."""
        report = ev.parse_report(_report())
        assert report["declared_result"] == "error"

    def test_the_error_report_names_its_cause(self):
        # With the host-supplied modules the cause is derivable; without them it
        # is "unknown" (fail-closed), which the next test pins.
        report = ev.parse_report(_report(
            changed_modules=ev.changed_module_paths(PATCH_ADDS_NEWMOD)))
        assert report["collection_error_cause"] == "environment"
        assert report["collection_errors"] >= 1

    def test_error_is_never_downgraded_to_fail_or_upgraded_to_pass(self):
        report = ev.parse_report(_report())
        assert report["declared_result"] not in ("pass", "fail")

    def test_an_unknown_declared_result_is_still_refused(self):
        payload = json.loads(_report().decode("ascii"))
        payload["declared_result"] = "probably-fine"
        payload[ev.REPORT_DIGEST_FIELD] = ev.compute_report_digest(payload)
        with pytest.raises(ev.EvaluatorError, match="invalid result"):
            ev.parse_report(json.dumps(payload).encode("ascii"))

    def test_a_tampered_error_report_is_still_refused(self):
        """Widening the accepted result must not weaken the self-integrity check."""
        payload = json.loads(_report().decode("ascii"))
        payload["collection_errors"] = 0  # contradicts the digest
        with pytest.raises(ev.EvaluatorError):
            ev.parse_report(json.dumps(payload).encode("ascii"))

    def test_empty_and_non_json_evidence_stay_fail_closed(self):
        with pytest.raises(ev.EvaluatorError):
            ev.parse_report(b"")
        with pytest.raises(ev.EvaluatorError):
            ev.parse_report(b"not json")


# ---------------------------------------------------------------------------
# F2 — the outcome vocabulary makes ERROR infrastructure-class
# ---------------------------------------------------------------------------
class TestErrorOutcomeVocabulary:
    def test_the_error_uses_the_established_infrastructure_outcome(self):
        """One canonical outcome: a second synonymous name would fragment the
        factory's closed, length-checked vocabulary."""
        assert "infrastructure_failure" in INFRASTRUCTURE_OUTCOMES
        assert "infrastructure_failure" not in SCIENTIFIC_OUTCOMES
        assert "infrastructure_failure" != ADMITTED_OUTCOME

    def test_the_outcome_vocabulary_is_unchanged(self):
        from qwen_train.f2_s8_factory import ALL_OUTCOMES
        assert len(ALL_OUTCOMES) == 7

    def test_an_error_side_yields_the_error_outcome_and_is_not_admitted(self):
        spec = _spec()
        base = ev.parse_report(_report())
        gold = ev.parse_report(_report())
        result = compose_execution_result(
            spec=spec, base_receipt=_receipt(), gold_receipt=_receipt(),
            base_report=base, gold_report=gold,
        )
        assert result.outcome == "infrastructure_failure"
        assert result.outcome_class == "infrastructure"
        assert result.reason.startswith(ENVIRONMENT_ERROR_PREFIX)
        assert result.outcome != ADMITTED_OUTCOME

    def test_an_error_is_never_recorded_as_a_scientific_outcome(self):
        """The defect: an unusable run used to land in 'test_not_run' (scientific)."""
        spec = _spec()
        result = compose_execution_result(
            spec=spec, base_receipt=_receipt(), gold_receipt=_receipt(),
            base_report=ev.parse_report(_report()),
            gold_report=ev.parse_report(_report()),
        )
        assert result.outcome not in SCIENTIFIC_OUTCOMES

    def test_the_error_reason_names_the_side_and_cause(self):
        spec = _spec()
        result = compose_execution_result(
            spec=spec, base_receipt=_receipt(), gold_receipt=_receipt(),
            base_report=ev.parse_report(_report()),
            gold_report=ev.parse_report(_report()),
        )
        assert "base established no scientific result" in result.reason
        assert "collection_error_cause=unknown" in result.reason

    def test_an_error_on_gold_alone_still_yields_the_error_outcome(self):
        spec = _spec()
        passing = {"declared_result": "pass", "collection_errors": 0,
                   "collection_error_cause": "unknown", "detail": "ok"}
        result = compose_execution_result(
            spec=spec, base_receipt=_receipt(), gold_receipt=_receipt(),
            base_report=passing, gold_report=ev.parse_report(_report()),
        )
        assert result.outcome == "infrastructure_failure"
        assert result.outcome_class == "infrastructure"
        assert result.reason.startswith(ENVIRONMENT_ERROR_PREFIX)

    def test_a_contract_failure_without_error_is_unchanged(self):
        """No over-capture: a genuine base_pass stays a SCIENTIFIC outcome."""
        spec = _spec()
        base = {"declared_result": "pass", "collection_errors": 0,
                "collection_error_cause": "unknown", "detail": "ok"}
        gold = {"declared_result": "pass", "collection_errors": 0,
                "collection_error_cause": "unknown", "detail": "ok"}
        result = compose_execution_result(
            spec=spec, base_receipt=_receipt(), gold_receipt=_receipt(),
            base_report=base, gold_report=gold,
        )
        assert result.outcome == "base_pass"
        assert result.outcome_class == "scientific"

    def test_a_missing_receipt_stays_infrastructure_failure(self):
        spec = _spec()
        result = compose_execution_result(
            spec=spec, base_receipt=None, gold_receipt=_receipt(),
            base_report=None, gold_report=None,
        )
        assert result.outcome == "infrastructure_failure"
        assert result.outcome_class == "infrastructure"


# ---------------------------------------------------------------------------
# F2 — denominator accounting and admission exclusion
# ---------------------------------------------------------------------------
class TestDenominatorAndAdmission:
    def test_error_outcomes_are_counted_as_infrastructure_not_scientific(self):
        from qwen_train.f2_s8_pilot import pilot_totals
        from qwen_train.f2_s8_pilot import PilotTaskResult

        results = [
            PilotTaskResult(task_id="a", admitted=False,
                            outcome="infrastructure_failure",
                            outcome_class="infrastructure"),
            PilotTaskResult(task_id="b", admitted=True,
                            outcome=ADMITTED_OUTCOME, outcome_class="scientific"),
        ]
        totals = pilot_totals(results)
        # ``infrastructure_invalid`` is the ERROR bucket; ``completed`` is the
        # graded (scientific) denominator. An ERROR run must appear in the first
        # and never in the second.
        assert totals["infrastructure_invalid"] == 1
        assert totals["admitted"] == 1
        assert totals["completed"] == 1
        assert totals["failed"] == 0

    def test_an_error_can_never_be_admitted(self):
        spec = _spec()
        result = compose_execution_result(
            spec=spec, base_receipt=_receipt(), gold_receipt=_receipt(),
            base_report=ev.parse_report(_report()),
            gold_report=ev.parse_report(_report()),
        )
        assert result.outcome != ADMITTED_OUTCOME


# ---------------------------------------------------------------------------
# F1 — host-side changed_modules, without leaking the patch to the guest
# ---------------------------------------------------------------------------
class TestChangedModulesPropagation:
    def test_the_spec_payload_never_carries_the_gold_patch(self):
        assert "patch" not in _spec().payload

    def test_without_changed_modules_the_cause_is_unknown(self):
        report = ev.parse_report(_report(changed_modules=()))
        assert report["collection_error_cause"] == "unknown"

    def test_host_supplied_changed_modules_classify_environment(self):
        report = ev.parse_report(
            _report(changed_modules=ev.changed_module_paths(PATCH_ADDS_NEWMOD)))
        assert report["collection_error_cause"] == "environment"

    def test_the_composer_accepts_host_supplied_modules(self):
        from qwen_train.f2_s8_compose import guest_evidence_from_junit

        spec = _spec()
        with tempfile.TemporaryDirectory() as td:
            evidence = guest_evidence_from_junit(
                spec=spec, side="base", junit_bytes=COLLECT_ERROR_JUNIT,
                run_log=b"log", work_dir=pathlib.Path(td),
                changed_modules=ev.changed_module_paths(PATCH_ADDS_NEWMOD),
            )
        payload = ev.parse_report(evidence.report)
        assert payload["declared_result"] == "error"
        assert payload["collection_error_cause"] == "environment"

    def test_audit_and_live_agree_when_modules_are_threaded(self):
        """The CAUSE_MISMATCH defect: both sides must derive the same cause."""
        modules = ev.changed_module_paths(PATCH_ADDS_NEWMOD)
        live = ev.parse_report(_report(changed_modules=modules))
        audit = ev.evaluate_execution(
            COLLECT_ERROR_JUNIT, fail_to_pass=[TARGET], pass_to_pass=[],
            changed_modules=list(modules),
        )
        assert live["collection_error_cause"] == audit.collection_error_cause


# ---------------------------------------------------------------------------
# F3 — pytest exit-code classification
# ---------------------------------------------------------------------------
class TestPytestExitCodes:
    @staticmethod
    def _side(returncode, stderr=""):
        return _classify_side(
            side="base", returncode=returncode, stdout="", stderr=stderr,
            targets=[TARGET], changed_modules=["pkg.newmod"],
        )

    def test_usage_error_is_named_and_fails(self):
        result = self._side(4, "ERROR: unknown config option: foo\n")
        assert result["status"] == "FAIL"
        assert result["reason_codes"] == ["TEST_CONFIG_USAGE_ERROR"]

    def test_internal_error_is_inconclusive_not_a_task_failure(self):
        result = self._side(3, "INTERNALERROR> boom\n")
        assert result["status"] == "INCONCLUSIVE"
        assert result["reason_codes"] == ["COLLECT_INTERNAL_ERROR"]

    def test_warnings_limit_is_inconclusive(self):
        result = self._side(6, "warnings exceeded\n")
        assert result["reason_codes"] == ["WARNINGS_LIMIT_EXCEEDED"]

    def test_exit_two_with_collection_errors_is_a_collection_outcome(self):
        """pytest uses 2 for 'Interrupted: N errors during collection' too."""
        result = self._side(2, "ERROR tests/test_mod.py\n"
                                "E   ModuleNotFoundError: No module named 'pandas'\n")
        assert "COLLECT_INTERRUPTED" not in result["reason_codes"]
        assert result["status"] == "FAIL"

    def test_exit_two_without_errors_is_a_real_interrupt(self):
        result = self._side(2, "")
        assert result["reason_codes"] == ["COLLECT_INTERRUPTED"]
        assert result["status"] == "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# F4 — pip requirements-file format
# ---------------------------------------------------------------------------
class TestRequirementsFormat:
    def test_continuations_are_joined_before_comments_are_stripped(self):
        lines, _ = parse_requirements_lines("pandas>=\\\n1.5  # pinned\n")
        assert lines == ["pandas>=1.5"]

    def test_relative_includes_are_followed(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "base-requirements.txt").write_text("six\n", encoding="utf-8")
            (root / "test-requirements.txt").write_text(
                "-r base-requirements.txt\npytest-xdist\n", encoding="utf-8")
            expanded = expand_requirements_file(
                root / "test-requirements.txt", root=root)
        assert expanded["requirements"] == ["six", "pytest-xdist"]
        assert expanded["problems"] == []

    def test_a_missing_include_is_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "test-requirements.txt").write_text(
                "-r nowhere.txt\n", encoding="utf-8")
            expanded = expand_requirements_file(
                root / "test-requirements.txt", root=root)
        assert any(p["scope"] == "blocking" and p["code"] == "requirements_include_missing"
                   for p in expanded["problems"])

    def test_an_include_escaping_the_project_is_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "proj"
            root.mkdir()
            (root.parent / "outside.txt").write_text("evil\n", encoding="utf-8")
            (root / "test-requirements.txt").write_text(
                "-r ../outside.txt\n", encoding="utf-8")
            expanded = expand_requirements_file(
                root / "test-requirements.txt", root=root)
        assert all("evil" not in r for r in expanded["requirements"])
        assert any(p["code"] == "requirements_include_escapes_project"
                   for p in expanded["problems"])

    def test_a_cycle_terminates_and_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "test-requirements.txt").write_text("-r a.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("-r test-requirements.txt\n", encoding="utf-8")
            expanded = expand_requirements_file(
                root / "test-requirements.txt", root=root)
        assert any(p["code"] == "requirements_include_cycle"
                   for p in expanded["problems"])

    def test_constraints_are_collected_as_paths_not_installs(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "constraints.txt").write_text("urllib3<2\n", encoding="utf-8")
            (root / "test-requirements.txt").write_text(
                "-c constraints.txt\npytest\n", encoding="utf-8")
            expanded = expand_requirements_file(
                root / "test-requirements.txt", root=root)
        assert expanded["requirements"] == ["pytest"]
        assert len(expanded["constraints"]) == 1
        assert expanded["constraints"][0].endswith("constraints.txt")


# ---------------------------------------------------------------------------
# F6 — deterministic project-root discovery
# ---------------------------------------------------------------------------
class TestProjectRootDiscovery:
    def test_a_plain_repo_resolves_to_itself(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / "pyproject.toml").write_text("[project]\nname='x'\n",
                                                 encoding="utf-8")
            assert resolve_task_project_root(repo) == repo.resolve()

    def test_a_cd_clause_wins(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            sub = repo / "src" / "pkg"
            sub.mkdir(parents=True)
            (sub / "pyproject.toml").write_text("[project]\nname='x'\n",
                                                encoding="utf-8")
            resolved = resolve_task_project_root(
                repo, test_cmd="cd src/pkg && uv run pytest")
            assert resolved == sub.resolve()

    def test_a_metapackage_root_resolves_to_the_shared_subproject(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / "setup.py").write_text("from setuptools import setup\n",
                                           encoding="utf-8")
            core = repo / "cirq-core"
            core.mkdir()
            (core / "setup.py").write_text("from setuptools import setup\n",
                                           encoding="utf-8")
            resolved = resolve_task_project_root(
                repo,
                patch_text="+++ b/cirq-core/cirq/ops/x.py\n",
                fail_to_pass=["cirq-core/cirq/ops/x_test.py::test_a"],
            )
            assert resolved == core.resolve()

    def test_nothing_resolvable_raises_rather_than_silently_using_the_root(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / "README").write_text("x", encoding="utf-8")
            with pytest.raises(AcquisitionError):
                resolve_task_project_root(repo)

    def test_the_override_is_honoured(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            sub = repo / "hypothesis-python"
            sub.mkdir()
            (sub / "setup.py").write_text("from setuptools import setup\n",
                                          encoding="utf-8")
            resolved = resolve_task_project_root(repo, override="hypothesis-python")
            assert resolved == sub.resolve()


# ---------------------------------------------------------------------------
# F7 — F2P / P2P skip semantics (verified, pinned, not changed)
# ---------------------------------------------------------------------------
class TestSkipSemantics:
    def test_a_skipped_fail_to_pass_is_not_a_resolution(self):
        outcome = ev.EvaluationOutcome(
            declared_result="fail",
            fail_to_pass={TARGET: ev.STATUS_SKIPPED},
            pass_to_pass={"tests/test_mod.py::test_ok": ev.STATUS_PASSED},
            detail="skipped f2p", emitted_identities=1,
        )
        assert outcome.fail_to_pass[TARGET] != ev.STATUS_PASSED

    def test_a_skipped_pass_to_pass_does_not_satisfy_maintenance(self):
        """Stricter than SWE-bench (which accepts a skipped P2P). Pinned here so
        the difference is a recorded decision, not an accident."""
        outcome = ev.EvaluationOutcome(
            declared_result="pass",
            fail_to_pass={TARGET: ev.STATUS_PASSED},
            pass_to_pass={"tests/test_mod.py::test_ok": ev.STATUS_SKIPPED},
            detail="skipped p2p", emitted_identities=1,
        )
        assert outcome.all_pass_to_pass_passed is False

    def test_an_error_never_satisfies_pass_to_pass(self):
        outcome = ev.EvaluationOutcome(
            declared_result=ev.RESULT_ERROR,
            fail_to_pass={TARGET: ev.STATUS_PASSED},
            pass_to_pass={"tests/test_mod.py::test_ok": ev.STATUS_PASSED},
            detail="error", emitted_identities=1,
        )
        assert outcome.is_error is True
        assert outcome.all_pass_to_pass_passed is False



# ---------------------------------------------------------------------------
# Gap C — project_subdir validation (shared by precheck and live runner)
# ---------------------------------------------------------------------------
class TestProjectSubdir:
    def test_absent_or_dot_preserves_the_root_project(self):
        from qwen_train.f2_s8_acquisition import resolve_project_subdir
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            assert resolve_project_subdir("", base) == base.resolve()
            assert resolve_project_subdir(None, base) == base.resolve()
            assert resolve_project_subdir(".", base) == base.resolve()

    def test_a_nested_directory_resolves(self):
        from qwen_train.f2_s8_acquisition import resolve_project_subdir
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            (base / "src" / "pkg").mkdir(parents=True)
            assert resolve_project_subdir("src/pkg", base) == (base / "src" / "pkg").resolve()

    def test_an_absolute_path_is_refused(self):
        from qwen_train.f2_s8_acquisition import resolve_project_subdir
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(AcquisitionError, match="relative"):
                resolve_project_subdir("C:/Windows", pathlib.Path(td))

    def test_traversal_is_refused(self):
        from qwen_train.f2_s8_acquisition import resolve_project_subdir
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(AcquisitionError, match="traverse"):
                resolve_project_subdir("../escape", pathlib.Path(td))

    def test_a_missing_directory_is_refused_not_silently_replaced(self):
        from qwen_train.f2_s8_acquisition import resolve_project_subdir
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(AcquisitionError, match="does not exist"):
                resolve_project_subdir("nope", pathlib.Path(td))


# ---------------------------------------------------------------------------
# Gap D — one shared interpretation of the declared test command
# ---------------------------------------------------------------------------
class TestCommandSemantics:
    def test_a_single_pytest_command(self):
        inv = ev.analyze_test_invocation("pytest -rA")
        assert inv.ok and inv.kind == "single_pytest"
        assert inv.argv == ("pytest", "-rA")
        assert inv.uses_uv is False and inv.workdir_rel == "" and inv.env == ()

    def test_a_uv_invocation_is_detected(self):
        inv = ev.analyze_test_invocation("uv run pytest -rA")
        assert inv.ok and inv.uses_uv is True

    def test_a_cd_clause_becomes_the_workdir(self):
        inv = ev.analyze_test_invocation("cd src/pkg && pytest -rA")
        assert inv.ok
        assert inv.workdir_rel == "src/pkg"
        assert inv.argv == ("pytest", "-rA")

    def test_inline_assignments_move_into_the_environment(self):
        inv = ev.analyze_test_invocation("PYTHONPATH=. pytest -rA")
        assert inv.ok
        assert dict(inv.env) == {"PYTHONPATH": "."}
        assert inv.argv == ("pytest", "-rA")

    def test_export_clauses_move_into_the_environment(self):
        inv = ev.analyze_test_invocation("export A=1 B=2 && pytest")
        assert inv.ok and dict(inv.env) == {"A": "1", "B": "2"}

    def test_activation_is_a_no_op(self):
        inv = ev.analyze_test_invocation("source .venv/bin/activate && pytest -rA")
        assert inv.ok and inv.argv == ("pytest", "-rA")

    def test_two_distinct_pytest_commands_are_refused(self):
        """Never pick one silently: the authoritative command is unprovable."""
        inv = ev.analyze_test_invocation(["pytest -rA", "uv run pytest -rA"])
        assert not inv.ok and inv.kind == "multiple_distinct"

    def test_a_single_declared_command_with_real_sequencing_is_refused(self):
        inv = ev.analyze_test_invocation("pytest -rA && pytest -rA tests/unit")
        assert not inv.ok and inv.kind == "unsupported"

    def test_shell_operators_are_refused(self):
        for cmd in ("pytest || true", "pytest; rm -rf /", "pytest | tee x", "pytest > out"):
            assert not ev.analyze_test_invocation(cmd).ok, cmd

    def test_an_unsafe_cd_target_is_refused(self):
        assert not ev.analyze_test_invocation("cd ../outside && pytest").ok

    def test_a_non_pytest_command_is_refused(self):
        inv = ev.analyze_test_invocation("make test")
        assert not inv.ok and inv.kind == "unsupported"

    def test_the_builder_and_precheck_agree_on_uv(self):
        from qwen_train.f2_s8_pilot_build import uses_uv_environment
        for cmd, expected in (("uv run pytest -rA", True),
                              ("pytest -rA", False),
                              ("cd sub && uv run pytest", True)):
            assert uses_uv_environment(cmd) is expected, cmd

# ---------------------------------------------------------------------------
# Re-audit fixes: LF-stable digests, monorepo venv, env-prefixed commands,
# and one shared ERROR reason marker.
# ---------------------------------------------------------------------------
class TestReauditFixes:
    def test_hashed_sources_are_lf_only(self):
        """implementation_digest() hashes the module's own bytes. A CRLF makes
        the digest differ from the LF blob Git stores, so a clean checkout would
        produce a different evaluator identity."""
        base = pathlib.Path(__file__).resolve().parents[1]
        for name in ("qwen_train/f2_evaluator.py",
                     "qwen_train/f2_s8_guest.py",
                     "qwen_train/f2_s8_pilot_live.py"):
            assert b"\r" not in (base / name).read_bytes(), name

    def test_a_monorepo_subproject_venv_is_found(self):
        from qwen_train.f2_s8_precheck import _venv_python
        with tempfile.TemporaryDirectory() as td:
            ws = pathlib.Path(td) / "ws"
            py = ws / "repo" / "src" / "pkg" / ".venv" / "Scripts" / "python.exe"
            py.parent.mkdir(parents=True)
            py.write_bytes(b"")
            project = ws / "repo" / "src" / "pkg"
            assert _venv_python(ws, "base", project) == py

    def test_a_root_project_venv_is_unchanged(self):
        from qwen_train.f2_s8_precheck import _venv_python
        with tempfile.TemporaryDirectory() as td:
            ws = pathlib.Path(td) / "ws"
            py = ws / "repo" / ".venv" / "Scripts" / "python.exe"
            py.parent.mkdir(parents=True)
            py.write_bytes(b"")
            assert _venv_python(ws, "base", ws / "repo") == py

    def test_a_venv_prefixed_pytest_entry_is_recognised(self):
        """_is_pytest_argv ignored leading KEY=VALUE, so a list entry that runs
        the suite under declared variables was not counted as invocable."""
        inv = ev.analyze_test_invocation(
            ["PYTHONPATH=. pytest -rA", "uv run pytest -rA"])
        assert not inv.ok and inv.kind == "multiple_distinct"

    def test_a_single_env_prefixed_command_is_usable(self):
        inv = ev.analyze_test_invocation("PYTHONPATH=. DJANGO_SETTINGS_MODULE=x pytest -rA")
        assert inv.ok
        assert dict(inv.env) == {"PYTHONPATH": ".", "DJANGO_SETTINGS_MODULE": "x"}
        assert inv.argv == ("pytest", "-rA")

    def test_one_shared_error_marker(self):
        from qwen_train import f2_evidence as evidence
        from qwen_train import f2_s8_compose as compose
        from qwen_train import f2_s8_producer as producer
        import inspect

        assert producer.ENVIRONMENT_ERROR_PREFIX == evidence.ENVIRONMENT_ERROR_PREFIX
        # Both writers of an ERROR record must carry the same marker.
        assert evidence.ENVIRONMENT_ERROR_PREFIX in inspect.getsource(
            compose._result_from_bundles)
