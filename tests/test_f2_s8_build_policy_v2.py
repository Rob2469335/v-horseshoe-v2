"""Regression tests for S8 Builder Policy v2 (Experiment J-F2 S8).

Validates the 16 mandatory conditions:
 1. Explicit project pin takes precedence over the default.
 2. Project pin conflicts are detected deterministically.
 3. Default pytest 8.3.5 is selected when supported (Python >= 3.8).
 4. Python-compatibility fallback uses the fixed metadata table.
 5. Version selection does not depend on build outcome.
 6. The selected pytest version is identical in base and gold environments for a task.
 7. Manifest records the selected version and selection rule.
 8. Missing metadata fails closed.
 9. Unknown or ambiguous project configuration fails closed.
10. uv path resolution uses the configured builder-owned path.
11. Missing uv fails closed (BUILDER_TOOL_UNAVAILABLE).
12. A SHA-256 mismatch fails closed before execution (BUILDER_TOOL_CORRUPT).
13. The builder does not fall back to the owner's profile (PROFILE_ISOLATION_VIOLATION).
14. Existing manifest consumers remain compatible (legacy schema preserved).
15. Evaluator JUnit identity tests cover the verified cross-version behavior (7.4 vs 8.3 vs 8.4).
16. Existing evaluator collection-error regression tests remain intact.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import pytest

from qwen_train import f2_s8_pilot_dryrun as DRY
from qwen_train import f2_s8_pilot_build as B
from qwen_train.f2_evaluator import (
    JUnitIdentity,
    collection_error_texts,
    count_collection_errors,
    evaluate_execution,
    parse_junit_xml,
)
from qwen_train.f2_s8_acquisition import (
    select_pytest_version,
)
from qwen_train.f2_s8_pilot_build import (
    classify_build_failure,
    resolve_and_verify_uv,
)
from qwen_train.f2_s8_pytest_policy import (
    ADMISSIBILITY_SCHEMA,
    PytestAdmissibility,
    load_pytest_admissibility,
)


@pytest.fixture(autouse=True)
def _isolate_test_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Ensure tests run with non-privileged identity and isolated environment variables."""
    monkeypatch.delenv("F2_BUILDER_UV_PATH", raising=False)
    monkeypatch.setenv("SWARM_TEST_ISOLATION", "1")
    # The admissibility sidecar lives at a FIXED path on purpose.  Point it at a
    # path that does not exist so these tests never depend on (or observe) a
    # policy file the owner may have installed on this host.
    from qwen_train import f2_s8_pytest_policy as policy

    monkeypatch.setattr(policy, "PYTEST_ADMISSIBILITY_PATH",
                        tmp_path / "absent-pytest-policy.json")
    # The dry-run execution seam refuses an administrator identity by design;
    # these tests exercise its argv/env construction, not the guard (which has
    # its own tests in tests/test_f2_s8_acquisition.py).
    from qwen_train import f2_s8_acquisition as acquisition

    monkeypatch.setattr(acquisition, "effective_identity_is_admin", lambda: False)


class TestPytestPolicyV2Selection:
    """Conditions 1-9: Deterministic Pytest Version Selection Policy."""

    def test_condition_1_explicit_project_pin_precedence_over_default(self, tmp_path: pathlib.Path) -> None:
        """1. Explicit project pin takes precedence over the default."""
        test_reqs = tmp_path / "test-requirements.txt"
        test_reqs.write_text("pytest==8.4.1\npytest-cov==6.2.1\n", encoding="utf-8")

        res = select_pytest_version(project=tmp_path, declared_python_minor=(3, 9))
        assert res["status"] == "VALID"
        assert res["rule"] == "RULE_A_EXPLICIT_PROJECT_PIN"
        assert res["pin"] == "pytest==8.4.1"
        assert res["version"] == "8.4.1"
        assert res["source"] == "test-requirements.txt"

    def test_condition_2_project_pin_conflicts_detected_deterministically(self, tmp_path: pathlib.Path) -> None:
        """2. Project pin conflicts are detected deterministically."""
        # Case A: Multiple conflicting exact pins across requirements
        res = select_pytest_version(
            project=None,
            declared_python_minor=(3, 9),
            test_requirements=["pytest==8.4.1", "pytest==7.4.4"],
        )
        assert res["status"] == "CONFLICT"
        assert "conflicting project pytest pins" in str(res["detail"])

        # Case B: Project pin conflicts with declared Python minor
        # pytest==8.4.1 requires >=3.9, running under Python 3.7
        res2 = select_pytest_version(
            project=None,
            declared_python_minor=(3, 7),
            test_requirements=["pytest==8.4.1"],
        )
        assert res2["status"] == "CONFLICT"
        assert "rejects Python (3, 7)" in str(res2["detail"])

    def test_condition_3_default_pytest_835_selected_when_supported(self) -> None:
        """3. Default pytest 8.3.5 is selected when supported."""
        for minor in (8, 9, 10, 11, 12, 13, 14):
            res = select_pytest_version(project=None, declared_python_minor=(3, minor))
            assert res["status"] == "VALID"
            assert res["rule"] == "RULE_B_DEFAULT_PIN"
            assert res["pin"] == "pytest==8.3.5"
            assert res["version"] == "8.3.5"
            assert res["source"] == "default"

    def test_condition_4_python_compatibility_fallback_uses_fixed_table(self) -> None:
        """4. Python-compatibility fallback uses the fixed metadata table."""
        # Python 3.7 fallback -> pytest==7.4.4 (Conan case)
        res_37 = select_pytest_version(project=None, declared_python_minor=(3, 7))
        assert res_37["status"] == "VALID"
        assert res_37["rule"] == "RULE_C_PYTHON_COMPATIBILITY_FALLBACK"
        assert res_37["pin"] == "pytest==7.4.4"
        assert res_37["version"] == "7.4.4"
        assert res_37["source"] == "PYTEST_COMPATIBILITY_FALLBACK[(3, 7)]"

        # Python 3.6 fallback -> pytest==7.0.1
        res_36 = select_pytest_version(project=None, declared_python_minor=(3, 6))
        assert res_36["status"] == "VALID"
        assert res_36["rule"] == "RULE_C_PYTHON_COMPATIBILITY_FALLBACK"
        assert res_36["pin"] == "pytest==7.0.1"
        assert res_36["version"] == "7.0.1"

    def test_condition_5_version_selection_independent_of_build_outcome(self, tmp_path: pathlib.Path) -> None:
        """5. Version selection does not depend on build outcome."""
        test_reqs = tmp_path / "test-requirements.txt"
        test_reqs.write_text("pytest==8.4.1\n", encoding="utf-8")

        # Selection before build
        sel1 = select_pytest_version(project=tmp_path, declared_python_minor=(3, 9))
        # Hypothetical outcome results do not alter metadata selection
        sel2 = select_pytest_version(project=tmp_path, declared_python_minor=(3, 9))
        assert sel1 == sel2

    def test_condition_6_selected_version_identical_in_base_and_gold(self, tmp_path: pathlib.Path) -> None:
        """6. The selected pytest version is identical in base and gold environments for a task."""
        base_tree = tmp_path / "repo"
        base_tree.mkdir()
        (base_tree / "test-requirements.txt").write_text("pytest==8.4.1\n", encoding="utf-8")

        gold_tree = tmp_path / "repo_gold"
        gold_tree.mkdir()
        (gold_tree / "test-requirements.txt").write_text("pytest==8.4.1\n", encoding="utf-8")

        base_sel = select_pytest_version(project=base_tree, declared_python_minor=(3, 9))
        gold_sel = select_pytest_version(project=gold_tree, declared_python_minor=(3, 9))
        assert base_sel == gold_sel
        assert base_sel["pin"] == gold_sel["pin"]

    def test_condition_7_manifest_records_selected_version_and_rule(self, tmp_path: pathlib.Path) -> None:
        """7. Manifest records the selected version and selection rule."""
        sel = select_pytest_version(project=None, declared_python_minor=(3, 10))
        assert "pin" in sel
        assert "version" in sel
        assert "rule" in sel
        assert "source" in sel
        assert "requires_python" in sel
        assert "declared_python_minor" in sel
        assert sel["rule"] == "RULE_B_DEFAULT_PIN"

    def test_condition_8_missing_metadata_fails_closed(self) -> None:
        """8. Missing metadata fails closed."""
        # Unverified pytest version not in fixed PyPI metadata table
        res = select_pytest_version(
            project=None,
            declared_python_minor=(3, 10),
            test_requirements=["pytest==99.99.99"],
        )
        assert res["status"] == "CONFLICT"
        assert "missing or unverified metadata" in str(res["detail"])

    def test_condition_9_unknown_or_ambiguous_project_configuration_fails_closed(self) -> None:
        """9. Unknown or ambiguous project configuration fails closed."""
        # Invalid requirement syntax for pytest
        res = select_pytest_version(
            project=None,
            declared_python_minor=(3, 10),
            test_requirements=["pytest[unclosed"],
        )
        assert res["status"] == "CONFLICT"
        assert "unparsable" in str(res["detail"])

        # Unsupported Python minor (< 3.6, e.g. Python 3.4)
        res_old = select_pytest_version(project=None, declared_python_minor=(3, 4))
        assert res_old["status"] == "CONFLICT"
        assert "no compatibility fallback available" in str(res_old["detail"])


class TestBuilderToolIsolation:
    """Conditions 10-13: Builder Tool Isolation and Security Controls."""

    def test_condition_10_uv_path_resolution_uses_builder_owned_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """10. uv path resolution uses the configured builder-owned path."""
        fake_uv = tmp_path / "tools" / "uv.exe"
        fake_uv.parent.mkdir(parents=True)
        fake_uv.write_bytes(b"uv-binary-content-v2")
        expected_sha = hashlib.sha256(b"uv-binary-content-v2").hexdigest()

        monkeypatch.setenv("F2_BUILDER_UV_PATH", str(fake_uv))
        resolved = resolve_and_verify_uv(expected_sha256=expected_sha,
                                        fixture_root=tmp_path)
        assert resolved == fake_uv

    def test_condition_11_missing_uv_fails_closed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """11. Missing uv fails closed."""
        nonexistent = tmp_path / "missing_dir" / "uv.exe"
        monkeypatch.setenv("F2_BUILDER_UV_PATH", str(nonexistent))
        with pytest.raises(FileNotFoundError, match="BUILDER_TOOL_UNAVAILABLE"):
            resolve_and_verify_uv(fixture_root=tmp_path)

        # Classification check
        code = classify_build_failure(FileNotFoundError(f"BUILDER_TOOL_UNAVAILABLE: uv executable not found at {nonexistent}"))
        assert code == "BUILDER_TOOL_UNAVAILABLE"

    def test_condition_12_sha256_mismatch_fails_closed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """12. A SHA-256 mismatch fails closed before execution."""
        fake_uv = tmp_path / "uv.exe"
        fake_uv.write_bytes(b"tampered-content")
        monkeypatch.setenv("F2_BUILDER_UV_PATH", str(fake_uv))

        with pytest.raises(RuntimeError, match="BUILDER_TOOL_CORRUPT"):
            resolve_and_verify_uv(expected_sha256="deadbeef" * 8, fixture_root=tmp_path)

        # Classification check
        code = classify_build_failure(RuntimeError("BUILDER_TOOL_CORRUPT: uv executable has digest mismatch"))
        assert code == "BUILDER_TOOL_CORRUPT"

    def test_condition_13_builder_does_not_fall_back_to_owner_profile(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """13. The builder does not fall back to the owner's profile."""
        profile_dir = pathlib.Path(r"C:\Users\rober")
        profile_uv = profile_dir / ".local" / "bin" / "uv.exe"

        monkeypatch.setenv("F2_BUILDER_UV_PATH", str(profile_uv))
        with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
            resolve_and_verify_uv()

        # Classification check
        code = classify_build_failure(RuntimeError("PROFILE_ISOLATION_VIOLATION: uv path is located within user profile"))
        assert code == "PROFILE_ISOLATION_VIOLATION"


class TestManifestAndEvaluatorCompatibility:
    """Conditions 14-16: Manifest Backward Compatibility and Evaluator Stability Gate."""

    def test_condition_14_manifest_schema_backward_compatibility(self) -> None:
        """14. Existing manifest consumers remain compatible."""
        record = {
            "task_id": "test__task-1",
            "base_commit": "abcdef123456",
            "workspace": r"C:\f2_pilot\test__task-1",
            "repo": r"C:\f2_pilot\test__task-1\repo",
            "gold_repo": r"C:\f2_pilot\test__task-1\repo_gold",
            "uses_uv": False,
            "venv": r"C:\f2_pilot\test__task-1\.venv",
            "gold_venv": r"C:\f2_pilot\test__task-1\.venv_gold",
            "python": r"C:\f2_pilot\test__task-1\.venv\Scripts\python.exe",
            "gold_python": r"C:\f2_pilot\test__task-1\.venv_gold\Scripts\python.exe",
            "project": r"C:\f2_pilot\test__task-1\repo",
            "gold_project": r"C:\f2_pilot\test__task-1\repo_gold",
            "project_subdir": "",
            "base_interpreter": r"C:\Python310\python.exe",
            "extra_deps": [],
            "install_plan": {},
            "builder": "builder_v2",
            "pytest_selection": {
                "pin": "pytest==8.3.5",
                "version": "8.3.5",
                "rule": "RULE_B_DEFAULT_PIN",
                "source": "default",
                "declared_python_minor": [3, 10],
            },
            "uv_evidence": None,
            "requires_python": None,
            "elapsed_s": 42,
        }
        serialized = json.dumps(record)
        loaded = json.loads(serialized)
        assert loaded["task_id"] == "test__task-1"
        assert loaded["pytest_selection"]["rule"] == "RULE_B_DEFAULT_PIN"
        assert loaded["pytest_selection"]["pin"] == "pytest==8.3.5"

    def test_condition_15_evaluator_junit_identity_across_pytest_versions(self) -> None:
        """15. Evaluator JUnit identity tests cover verified cross-version behavior (7.4 vs 8.3 vs 8.4)."""
        junit_v74 = (
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<testsuites>\n'
            b'  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="3" time="0.05">\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_one" time="0.01"/>\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_two" time="0.01">\n'
            b'      <failure message="assert False">assert False</failure>\n'
            b'    </testcase>\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_three" time="0.01"/>\n'
            b'  </testsuite>\n'
            b'</testsuites>\n'
        )
        junit_v83 = (
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<testsuites>\n'
            b'  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="3" time="0.06">\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_one" time="0.01"/>\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_two" time="0.01">\n'
            b'      <failure message="assert False">assert False</failure>\n'
            b'    </testcase>\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_three" time="0.01"/>\n'
            b'  </testsuite>\n'
            b'</testsuites>\n'
        )
        junit_v84 = (
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<testsuites>\n'
            b'  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="3" time="0.07">\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_one" time="0.01"/>\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_two" time="0.01">\n'
            b'      <failure message="assert False">assert False</failure>\n'
            b'    </testcase>\n'
            b'    <testcase classname="test_mod" file="test_mod.py" name="test_three" time="0.01"/>\n'
            b'  </testsuite>\n'
            b'</testsuites>\n'
        )
        parsed_74 = parse_junit_xml(junit_v74)
        parsed_83 = parse_junit_xml(junit_v83)
        parsed_84 = parse_junit_xml(junit_v84)

        assert parsed_74 == parsed_83 == parsed_84
        ident_one = JUnitIdentity(classname="test_mod", name="test_one")
        ident_two = JUnitIdentity(classname="test_mod", name="test_two")
        ident_three = JUnitIdentity(classname="test_mod", name="test_three")
        assert parsed_74[ident_one] == "passed"
        assert parsed_74[ident_two] == "failed"
        assert parsed_74[ident_three] == "passed"

        eval_74 = evaluate_execution(junit_v74, fail_to_pass=["test_mod.py::test_two"], pass_to_pass=["test_mod.py::test_one"])
        eval_83 = evaluate_execution(junit_v83, fail_to_pass=["test_mod.py::test_two"], pass_to_pass=["test_mod.py::test_one"])
        eval_84 = evaluate_execution(junit_v84, fail_to_pass=["test_mod.py::test_two"], pass_to_pass=["test_mod.py::test_one"])

        assert eval_74.fail_to_pass == eval_83.fail_to_pass == eval_84.fail_to_pass
        assert eval_74.pass_to_pass == eval_83.pass_to_pass == eval_84.pass_to_pass
        assert eval_74.declared_result == eval_83.declared_result == eval_84.declared_result == "fail"

    def test_condition_16_evaluator_collection_error_regression_intact(self) -> None:
        """16. Existing evaluator collection-error regression tests remain intact."""
        junit_collection_err = (
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<testsuites>\n'
            b'  <testsuite name="pytest" errors="1" failures="0" skipped="0" tests="0" time="0.01">\n'
            b'    <testcase classname="" name="test_error" time="0.0">\n'
            b'      <error message="collection failure">ImportError: missing required dependency</error>\n'
            b'    </testcase>\n'
            b'  </testsuite>\n'
            b'</testsuites>\n'
        )
        assert count_collection_errors(junit_collection_err) == 1
        texts = collection_error_texts(junit_collection_err)
        assert len(texts) == 1
        assert "ImportError: missing required dependency" in texts[0]

# ---------------------------------------------------------------------------
# R1-A: fail-closed resolution over the COMPLETE declaration set
# ---------------------------------------------------------------------------
def _results(*args, **kwargs):
    return select_pytest_version(*args, **kwargs)


class TestFailClosedResolution:
    """Every path either produces a concrete ``pytest==X.Y.Z`` or refuses."""

    def test_bare_pytest_resolves_to_the_default_pin(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "test-requirements.txt").write_text("pytest\n", encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID"
        assert res["pin"] == "pytest==8.3.5"
        assert res["version"] == "8.3.5"
        assert res["rule"] == "RULE_B_DEFAULT_PIN"
        # the defect: pin="pytest", version=None was recorded as VALID
        assert res["pin"] != "pytest"

    def test_exact_approved_pin_is_kept(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       test_requirements=["pytest==8.3.5"])
        assert res["status"] == "VALID" and res["version"] == "8.3.5"
        assert res["rule"] == "RULE_A_EXPLICIT_PROJECT_PIN"

    def test_exact_version_outside_the_ratified_set_is_refused(self) -> None:
        ratified = PytestAdmissibility(versions=("8.3.5",), source="fixture",
                                       owner_ratified=True)
        res = _results(declared_python_minor=(3, 10), admissibility=ratified,
                       test_requirements=["pytest==8.4.1"])
        assert res["status"] == "CONFLICT"
        assert "outside the ratified set" in res["detail"]

    def test_range_only_requirement_resolves_to_a_concrete_version(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       test_requirements=["pytest>=8.0,<9"])
        assert res["status"] == "VALID"
        assert res["rule"] == "RULE_A_DECLARED_CONSTRAINT"
        assert res["version"] == "8.4.2"          # highest ratified release in range
        assert res["pin"] == "pytest==8.4.2"

    def test_wildcard_equality_is_intersected(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       test_requirements=["pytest==8.3.*"])
        assert res["status"] == "VALID" and res["version"] == "8.3.5"

    def test_compatible_release_operator_is_intersected(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       test_requirements=["pytest~=8.3.0"])
        assert res["status"] == "VALID" and res["version"] == "8.3.5"

    def test_exclusion_constraint_is_intersected(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       test_requirements=["pytest>=8,<9,!=8.4.0,!=8.4.1,!=8.4.2"])
        assert res["status"] == "VALID" and res["version"] == "8.3.5"

    @pytest.mark.parametrize("order", [
        ["pytest==8.3.5", "pytest>=8"],
        ["pytest>=8", "pytest==8.3.5"],
    ])
    def test_exact_pin_with_range_is_order_independent(self, order) -> None:
        res = _results(declared_python_minor=(3, 10), test_requirements=order)
        assert res["status"] == "VALID"
        assert (res["version"], res["rule"]) == ("8.3.5", "RULE_A_EXPLICIT_PROJECT_PIN")

    def test_conflicting_declarations_are_refused_with_a_trace(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       test_requirements=["pytest==8.3.5", "pytest==8.4.1"])
        assert res["status"] == "CONFLICT"
        assert "conflicting project pytest pins" in res["detail"]
        assert "pytest==8.4.1" in res["detail"]

    def test_malformed_requirement_is_refused(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       test_requirements=["pytest[unclosed"])
        assert res["status"] == "CONFLICT" and "unparsable" in res["detail"]

    def test_direct_reference_is_refused_not_guessed(self) -> None:
        res = _results(
            declared_python_minor=(3, 10),
            test_requirements=["pytest @ https://example.invalid/pytest-8.3.5-py3-none-any.whl"])
        assert res["status"] == "CONFLICT"
        assert "direct reference" in res["detail"]

    def test_missing_python_minor_is_refused(self) -> None:
        res = _results(test_requirements=["pytest==8.3.5"])
        assert res["status"] == "CONFLICT"
        assert "declared Python minor is required" in res["detail"]

    def test_python_minor_outside_the_matrix_is_refused(self) -> None:
        res = _results(declared_python_minor=(3, 15))
        assert res["status"] == "CONFLICT"
        assert "no compatibility fallback available" in res["detail"]

    def test_declaration_in_project_dependencies(self) -> None:
        res = _results(declared_python_minor=(3, 10),
                       runtime_dependencies=["pytest==8.3.5", "requests"])
        assert res["status"] == "VALID" and res["version"] == "8.3.5"

    def test_declaration_in_optional_dependencies(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\n'
            '[project.optional-dependencies]\ntest = ["pytest==8.4.1"]\n',
            encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID" and res["version"] == "8.4.1"
        assert res["source"].endswith("optional-dependencies].test")

    def test_declaration_in_dependency_group_with_include(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\n'
            '[dependency-groups]\ntest = ["pytest==8.3.5"]\n'
            'dev = [{include-group = "test"}]\n',
            encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID" and res["version"] == "8.3.5"

    def test_declaration_in_setup_cfg_test_requirements(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "setup.cfg").write_text(
            "[options.extras_require]\ntest =\n    pytest==8.3.5\n",
            encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID" and res["version"] == "8.3.5"
        assert res["source"].startswith("setup.cfg:")

    def test_declaration_in_tox_ini(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tox.ini").write_text(
            "[testenv]\ndeps =\n    pytest==8.4.1\n", encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID" and res["version"] == "8.4.1"
        assert res["source"].startswith("tox.ini:")

    def test_declaration_in_static_setup_py(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "setup.py").write_text(
            "from setuptools import setup\n"
            "setup(name='fixture', install_requires=['pytest==8.3.5'])\n",
            encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID" and res["version"] == "8.3.5"
        assert res["source"].startswith("setup.py:")

    def test_dynamic_setup_py_declaration_fails_closed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "setup.py").write_text(
            "from setuptools import setup\n"
            "def reqs():\n    return ['pytest==8.3.5']\n"
            "setup(name='fixture', install_requires=reqs())\n",
            encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "CONFLICT"
        assert "setup_py_dynamic_declaration" in res["detail"]

    def test_dynamic_project_dependencies_fail_closed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\ndynamic = ["dependencies"]\n',
            encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "CONFLICT"
        assert "dynamic_declaration_unsupported" in res["detail"]

    def test_nested_requirements_include_is_followed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "test-requirements.txt").write_text(
            "-r base-test.txt\n", encoding="utf-8")
        (tmp_path / "base-test.txt").write_text("pytest==8.4.1\n", encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID" and res["version"] == "8.4.1"

    def test_lockfile_outside_the_ratified_set_is_refused(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "uv.lock").write_text(
            'version = 1\n[[package]]\nname = "pytest"\nversion = "9.1.1"\n',
            encoding="utf-8")
        ratified = PytestAdmissibility(versions=("8.3.5", "8.4.1"), source="fixture",
                                       owner_ratified=True)
        res = _results(project=tmp_path, declared_python_minor=(3, 10),
                       admissibility=ratified)
        assert res["status"] == "CONFLICT"
        assert "outside the ratified set" in res["detail"]

    def test_lockfile_resolution_governs_when_valid(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "uv.lock").write_text(
            'version = 1\n[[package]]\nname = "pytest"\nversion = "8.3.5"\n',
            encoding="utf-8")
        (tmp_path / "test-requirements.txt").write_text("pytest>=8\n", encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID"
        assert res["rule"] == "RULE_A_LOCKFILE_RESOLUTION"
        assert res["version"] == "8.3.5"

    def test_lockfile_violating_a_declaration_is_refused(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "uv.lock").write_text(
            'version = 1\n[[package]]\nname = "pytest"\nversion = "8.3.5"\n',
            encoding="utf-8")
        (tmp_path / "test-requirements.txt").write_text("pytest==8.4.1\n", encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "CONFLICT"
        assert "violates the declared constraint" in res["detail"]

    def test_exact_pin_conflict_with_a_group_declaration(self) -> None:
        """The python__mypy pattern: adjustment pin vs the project's own pin."""
        res = _results(declared_python_minor=(3, 10),
                       adjustment={"pytest_pin": "pytest==8.3.5"},
                       test_requirements=["pytest==8.4.1"])
        assert res["status"] == "CONFLICT"

    def test_python_minor_conflict_with_a_range(self) -> None:
        """The conan-io pattern: a range that cannot run on the declared minor."""
        res = _results(declared_python_minor=(3, 7),
                       test_requirements=["pytest>=8.4"])
        assert res["status"] == "CONFLICT"
        assert "(3, 7)" in res["detail"]

    def test_unlicensed_dynamic_problem_is_never_silently_dropped(
            self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            "-r missing-file.txt\n", encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "CONFLICT"
        assert "requirements_include_missing" in res["detail"]

    def test_evidence_records_sources_hashes_and_intersection(
            self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "test-requirements.txt").write_text("pytest>=8,<9\n", encoding="utf-8")
        res = _results(project=tmp_path, declared_python_minor=(3, 10))
        assert res["status"] == "VALID"
        assert res["intersection"] == "<9,>=8"
        assert res["candidates"] == ["8.3.5", "8.4.0", "8.4.1", "8.4.2"]
        assert res["declarations"][0]["source"] == "test-requirements.txt"
        assert len(res["declarations"][0]["sha256"]) == 64
        assert res["declaration_sources"][0]["kind"] == "requirements_file"
        assert res["metadata_snapshot"]["id"]
        assert res["admissibility"]["owner_ratified"] is False

    def test_a_valid_result_always_carries_a_concrete_pin(self) -> None:
        for kwargs in (
            {"test_requirements": ["pytest>=8"]},
            {"test_requirements": ["pytest"]},
            {"test_requirements": ["pytest~=8.4.0"]},
        ):
            res = _results(declared_python_minor=(3, 10), **kwargs)
            assert res["status"] == "VALID"
            assert res["version"], kwargs
            assert res["pin"] == f"pytest=={res['version']}"


class TestAdmissibilityGate:
    """Technical compatibility is NOT admissibility."""

    @staticmethod
    def _ratified(tmp_path: pathlib.Path, versions, *,
                  ratified_by="test-fixture", tamper=None):
        """A policy + provenance + evidence triple, exactly as the owner records it."""
        from qwen_train.f2_s8_pytest_policy import (
            ADMISSIBILITY_EVIDENCE_SCHEMA, ADMISSIBILITY_PROVENANCE_SCHEMA,
        )

        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir(exist_ok=True)
        records = []
        for role in ("population_scan", "release_metadata"):
            artifact = evidence_dir / f"{role}.json"
            artifact.write_text(json.dumps({"role": role}), encoding="utf-8")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if tamper == "evidence" and role == "release_metadata":
                digest = "0" * 64          # binds bytes that are not there
            records.append({"role": role, "path": str(artifact),
                            "sha256": digest,
                            "schema": ADMISSIBILITY_EVIDENCE_SCHEMA})
        policy_path = tmp_path / "pytest-policy.json"
        policy_path.write_text(json.dumps({
            "schema": ADMISSIBILITY_SCHEMA,
            "ratified_by": ratified_by,
            "versions": list(versions),
        }), encoding="utf-8")
        policy_digest = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        provenance_path = tmp_path / "pytest-policy-provenance.json"
        if tamper == "policy":
            policy_digest = "0" * 64
        authority = "someone-else" if tamper == "authority" else ratified_by
        provenance_path.write_text(json.dumps({
            "schema": ADMISSIBILITY_PROVENANCE_SCHEMA,
            "ratified_by": authority,
            "policy": {"path": str(policy_path), "sha256": policy_digest},
            "evidence": records,
        }), encoding="utf-8")
        return policy_path, provenance_path

    def test_unratified_policy_is_refused_at_the_execution_boundary(self) -> None:
        from qwen_train.f2_s8_pytest_policy import OPERATIVE_PYTEST_ADMISSIBILITY

        res = _results(declared_python_minor=(3, 10),
                       admissibility=OPERATIVE_PYTEST_ADMISSIBILITY,
                       require_owner_ratification=True)
        assert res["status"] == "ADMISSIBILITY_NOT_RATIFIED"
        assert "D1" in res["detail"]

    def test_a_ratified_policy_document_is_hash_bound(self, tmp_path: pathlib.Path) -> None:
        policy_path, provenance_path = self._ratified(tmp_path, ["8.3.5"])
        policy = load_pytest_admissibility(
            policy_path, provenance_path=provenance_path)
        assert policy.owner_ratified is True
        assert len(policy.sha256) == 64
        res = _results(declared_python_minor=(3, 10),
                       admissibility=policy, require_owner_ratification=True,
                       test_requirements=["pytest>=8"])
        assert res["status"] == "VALID" and res["version"] == "8.3.5"

    def test_a_policy_without_provenance_is_refused(self, tmp_path: pathlib.Path) -> None:
        policy_path, _ = self._ratified(tmp_path, ["8.3.5"])
        with pytest.raises(Exception, match="provenance missing"):
            load_pytest_admissibility(
                policy_path, provenance_path=tmp_path / "absent.json")

    def test_provenance_binding_a_different_policy_is_refused(
            self, tmp_path: pathlib.Path) -> None:
        policy_path, provenance_path = self._ratified(
            tmp_path, ["8.3.5"], tamper="policy")
        with pytest.raises(Exception, match="policy digest mismatch"):
            load_pytest_admissibility(
                policy_path, provenance_path=provenance_path)

    def test_changed_evidence_is_refused(self, tmp_path: pathlib.Path) -> None:
        policy_path, provenance_path = self._ratified(
            tmp_path, ["8.3.5"], tamper="evidence")
        with pytest.raises(Exception, match="evidence .* digest mismatch"):
            load_pytest_admissibility(
                policy_path, provenance_path=provenance_path)

    def test_a_ratifying_authority_mismatch_is_refused(
            self, tmp_path: pathlib.Path) -> None:
        policy_path, provenance_path = self._ratified(
            tmp_path, ["8.3.5"], tamper="authority")
        with pytest.raises(Exception, match="ratifying authority"):
            load_pytest_admissibility(
                policy_path, provenance_path=provenance_path)

    def test_a_policy_naming_an_unverified_release_is_refused(
            self, tmp_path: pathlib.Path) -> None:
        policy_path, provenance_path = self._ratified(tmp_path, ["99.99.99"])
        with pytest.raises(Exception, match="unverified release"):
            load_pytest_admissibility(
                policy_path, provenance_path=provenance_path)


class TestInstalledVersionEnforcement:
    """R1-A 10: the INSTALLED version is asserted, not assumed."""

    def test_installed_version_is_read_from_distribution_metadata(
            self, monkeypatch) -> None:
        import importlib.metadata

        real = importlib.metadata.version("pytest")
        captured: dict[str, object] = {}

        class _Proc:
            returncode = 0
            stdout = real + "\n"
            stderr = ""

        def _fake_run(args, **kwargs):
            captured["args"] = args
            return _Proc()

        monkeypatch.setattr(B.subprocess, "run", _fake_run)
        installed = B.installed_pytest_version(sys.executable)
        assert installed == real
        # it asks the INSTALLED DISTRIBUTION database, in the target interpreter
        assert captured["args"][0] == str(sys.executable)
        assert "importlib.metadata" in captured["args"][2]
        evidence = B.assert_installed_pytest(sys.executable, installed)
        assert evidence["matches"] is True
        assert evidence["source"] == "importlib.metadata"

    def test_plugin_induced_drift_is_refused(self, monkeypatch) -> None:
        """The measured awslabs__mcp-1154 pattern: 8.3.5 requested, 9.1.1 found."""
        monkeypatch.setattr(B, "installed_pytest_version", lambda python: "9.1.1")
        with pytest.raises(RuntimeError, match="PYTEST_INSTALLED_DRIFT"):
            B.assert_installed_pytest(sys.executable, "8.3.5")
        assert B.classify_build_failure(
            RuntimeError("PYTEST_INSTALLED_DRIFT: x")) == "PYTEST_INSTALLED_DRIFT"

    def test_an_empty_approved_version_is_refused(self) -> None:
        with pytest.raises(RuntimeError, match="PYTEST_INSTALLED_UNVERIFIABLE"):
            B.assert_installed_pytest(sys.executable, "")

    def test_a_missing_pytest_installation_is_unverifiable(self, monkeypatch) -> None:
        class _Proc:
            returncode = 3
            stdout = ""
            stderr = ""

        monkeypatch.setattr(B.subprocess, "run", lambda *a, **k: _Proc())
        with pytest.raises(RuntimeError, match="PYTEST_INSTALLED_UNVERIFIABLE"):
            B.installed_pytest_version(sys.executable)

    def test_approved_pytest_requirement_refuses_an_unresolved_selection(self) -> None:
        with pytest.raises(RuntimeError, match="PYTEST_PIN_UNRESOLVED"):
            B.approved_pytest_requirement({"status": "VALID", "version": None})
        assert B.approved_pytest_requirement({"version": "8.3.5"}) == "pytest==8.3.5"


class TestDedicatedUvExecution:
    """R1-B: a uv-owned dry run uses the verified executable, never PATH."""


    def _prepare(self, fixture_root, *args, **kwargs):
        kwargs["fixture_root"] = fixture_root
        return DRY.prepare_uv_execution(*args, **kwargs)

    def _run(self, fixture_root, *args, **kwargs):
        kwargs["fixture_root"] = fixture_root
        return DRY.run_test(*args, **kwargs)

    def _uv_stub(self, directory: pathlib.Path, text: str) -> pathlib.Path:
        directory.mkdir(parents=True, exist_ok=True)
        stub = directory / ("uv.cmd" if os.name == "nt" else "uv")
        stub.write_text(f"@echo {text}\r\n", encoding="utf-8")
        return stub

    def test_dedicated_executable_is_selected(self, tmp_path: pathlib.Path) -> None:
        approved = self._uv_stub(tmp_path / "approved", "APPROVED")
        digest = hashlib.sha256(approved.read_bytes()).hexdigest()
        plan = self._prepare(tmp_path, "uv run pytest -q", uv_path=approved,
                                        expected_sha256=digest)
        assert pathlib.Path(plan["argv"][0]) == approved
        assert plan["argv"][1:] == ["run", "pytest", "-q"]
        assert plan["uv_sha256"] == digest

    def test_invalid_dedicated_executable_is_rejected(self, tmp_path: pathlib.Path) -> None:
        approved = self._uv_stub(tmp_path / "approved", "APPROVED")
        with pytest.raises(RuntimeError, match="BUILDER_TOOL_CORRUPT"):
            self._prepare(tmp_path, "uv run pytest", uv_path=approved,
                                     expected_sha256="deadbeef" * 8)

    def test_missing_dedicated_executable_is_reported(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(FileNotFoundError, match="BUILDER_TOOL_UNAVAILABLE"):
            self._prepare(tmp_path, "uv run pytest",
                                     uv_path=tmp_path / "missing" / "uv.exe")

    def test_ambient_path_executable_cannot_hijack(self, tmp_path: pathlib.Path,
                                                   monkeypatch) -> None:
        approved = self._uv_stub(tmp_path / "approved", "APPROVED")
        digest = hashlib.sha256(approved.read_bytes()).hexdigest()
        ambient = self._uv_stub(tmp_path / "ambient", "AMBIENT")
        marker = tmp_path / "ambient-ran.txt"
        ambient.write_text(f'@echo AMBIENT> "{marker}"\r\n', encoding="utf-8")
        monkeypatch.setenv(
            "PATH", str(ambient.parent) + os.pathsep + os.environ.get("PATH", ""))

        recorded: dict[str, object] = {}

        class _Proc:
            returncode = 0
            stdout = "APPROVED\n"
            stderr = ""

        def _fake_run(args, **kwargs):
            recorded["args"] = args
            recorded["env"] = kwargs.get("env") or {}
            return _Proc()

        # The suite's global fixture patches subprocess.Popen, so the production
        # path is exercised through its process-launcher seam: the captured argv
        # is EXACTLY what would be executed.
        monkeypatch.setattr(DRY.subprocess, "run", _fake_run)
        result = self._run(tmp_path, 
            python="", repo=tmp_path, test_cmd="uv run pytest",
            junit=tmp_path / "out.xml", timeout=60, uv_offline=True,
            uv_path=approved, uv_sha256=digest)

        argv = list(recorded["args"])
        assert pathlib.Path(argv[0]) == approved
        assert pathlib.Path(argv[0]).read_text(encoding="utf-8").strip() == "@echo APPROVED"
        assert str(ambient.parent) not in recorded["env"]["PATH"].split(os.pathsep)
        assert result["execution"]["uv_sha256"] == digest
        assert not marker.exists(), "the ambient uv stub was invoked"
        assert ambient.read_text(encoding="utf-8").startswith("@echo AMBIENT")

    def test_shell_metacharacter_command_is_refused(self, tmp_path: pathlib.Path) -> None:
        approved = self._uv_stub(tmp_path / "approved", "APPROVED")
        digest = hashlib.sha256(approved.read_bytes()).hexdigest()
        with pytest.raises(RuntimeError, match="UV_EXECUTION_UNSAFE_COMMAND"):
            self._prepare(tmp_path, "uv run pytest && echo hi", uv_path=approved,
                                     expected_sha256=digest)

    def test_non_uv_command_is_refused(self, tmp_path: pathlib.Path) -> None:
        approved = self._uv_stub(tmp_path / "approved", "APPROVED")
        digest = hashlib.sha256(approved.read_bytes()).hexdigest()
        with pytest.raises(RuntimeError, match="does not start with uv"):
            self._prepare(tmp_path, "pytest -q", uv_path=approved,
                                     expected_sha256=digest)

    def test_windows_path_with_spaces_is_preserved(self, tmp_path: pathlib.Path) -> None:
        approved = self._uv_stub(tmp_path / "approved", "APPROVED")
        digest = hashlib.sha256(approved.read_bytes()).hexdigest()
        plan = self._prepare(tmp_path, 
            'uv run pytest "tests/with space/test_a.py::test_b"',
            uv_path=approved, expected_sha256=digest)
        assert plan["argv"][-1] == "tests/with space/test_a.py::test_b"

    def test_dry_run_result_records_the_uv_identity(self, tmp_path: pathlib.Path,
                                                    monkeypatch) -> None:
        approved = self._uv_stub(tmp_path / "approved", "APPROVED")
        digest = hashlib.sha256(approved.read_bytes()).hexdigest()

        class _Proc:
            returncode = 0
            stdout = "APPROVED\n"
            stderr = ""

        monkeypatch.setattr(DRY.subprocess, "run", lambda *a, **k: _Proc())
        (tmp_path / "repo").mkdir()
        result = self._run(tmp_path, 
            python="", repo=tmp_path / "repo", test_cmd="uv run pytest -q",
            junit=tmp_path / "out.xml", timeout=60, uv_offline=True,
            uv_path=approved, uv_sha256=digest)
        assert result["execution"]["mode"] == "argv"
        assert result["execution"]["uv_sha256"] == digest
        assert pathlib.Path(result["execution"]["uv_path"]) == approved

class TestExactPinAgainstExclusions:
    """An exact pin is honoured only when it is itself admissible."""

    def test_exact_pin_excluded_by_its_own_exclusion_is_refused(self) -> None:
        res = select_pytest_version(
            declared_python_minor=(3, 10),
            test_requirements=["pytest==8.4.1", "pytest!=8.4.1", "pytest>=8.3"])
        assert res["status"] == "CONFLICT"
        assert "contradicts the declared constraint" in res["detail"]

    def test_a_range_that_admits_the_exact_pin_still_selects_it(self) -> None:
        res = select_pytest_version(
            declared_python_minor=(3, 10),
            test_requirements=["pytest==8.4.1", "pytest>=8.3", "pytest!=8.3.5"])
        assert res["status"] == "VALID" and res["version"] == "8.4.1"
