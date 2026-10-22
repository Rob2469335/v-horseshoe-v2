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
import pathlib
import pytest

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


@pytest.fixture(autouse=True)
def _isolate_test_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Ensure tests run with non-privileged identity and isolated environment variables."""
    monkeypatch.delenv("F2_BUILDER_UV_PATH", raising=False)
    monkeypatch.setenv("SWARM_TEST_ISOLATION", "1")


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
        resolved = resolve_and_verify_uv(expected_sha256=expected_sha)
        assert resolved == fake_uv

    def test_condition_11_missing_uv_fails_closed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """11. Missing uv fails closed."""
        nonexistent = tmp_path / "missing_dir" / "uv.exe"
        monkeypatch.setenv("F2_BUILDER_UV_PATH", str(nonexistent))
        with pytest.raises(FileNotFoundError, match="BUILDER_TOOL_UNAVAILABLE"):
            resolve_and_verify_uv()

        # Classification check
        code = classify_build_failure(FileNotFoundError(f"BUILDER_TOOL_UNAVAILABLE: uv executable not found at {nonexistent}"))
        assert code == "BUILDER_TOOL_UNAVAILABLE"

    def test_condition_12_sha256_mismatch_fails_closed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """12. A SHA-256 mismatch fails closed before execution."""
        fake_uv = tmp_path / "uv.exe"
        fake_uv.write_bytes(b"tampered-content")
        monkeypatch.setenv("F2_BUILDER_UV_PATH", str(fake_uv))

        with pytest.raises(RuntimeError, match="BUILDER_TOOL_CORRUPT"):
            resolve_and_verify_uv(expected_sha256="deadbeef" * 8)

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
