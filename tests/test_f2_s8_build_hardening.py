"""F2-S8 build hardening and collection-only pre-check regression tests.

Two things are proved here, and neither is proved by a green suite elsewhere:

1. **Declared test dependencies are resolved from the project's own metadata
   and recorded with provenance.**  ``pip install -e .`` installs
   ``[project].dependencies`` and no PEP 621 extra, and a PEP 735 dependency
   group is installed only when a tool is told to install it -- so a project
   that declares a test-only import under an extra (QuantEcon declares
   ``pandas`` solely under ``[project.optional-dependencies].testing``) built an
   environment whose collection aborted with ``ModuleNotFoundError`` and was
   recorded as an environment collection failure.

2. **Nothing in the build or collection path can be flattered into a PASS.**
   Missing dependencies, failed installations, an incomplete evidence record,
   a skipped task and a broken collector each have an explicit reason code, and
   none of them is silently downgraded to something friendlier.

The pre-check tests call the REAL ``run_precheck`` with an injected collection
runner, so selection, binding, classification and isolation are exercised
without executing a test body.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train.f2_s8_acquisition import (  # noqa: E402
    AcquisitionError,
    BUILDER_SOURCE,
    TEST_DEPENDENCY_SOURCE_NAMES,
    builder_install_plan,
    declared_test_requirements,
    parse_requirements_lines,
)
from qwen_train.f2_s8_pilot_build import classify_build_failure  # noqa: E402
from qwen_train.f2_s8_precheck import (  # noqa: E402
    PrecheckError,
    SIDE_FAIL,
    SIDE_INCONCLUSIVE,
    SIDE_PASS,
    _classify_side,
    _validate_task_results,
    missing_dependency_modules,
    run_precheck,
)
from qwen_train.f2_s8_factory import AppendOnlyLedger  # noqa: E402
from tests.conftest import _REAL_POPEN  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_real_git(monkeypatch):
    """The suite mocks subprocess.Popen globally; the pre-check must run the
    REAL ``git rev-parse HEAD``.  Restore it for this module only; the injected
    collection runner still prevents any test body from executing."""
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
QUANTECON_PYPROJECT = """
[project]
name = "quantecon.py"
version = "0.8.0"
requires-python = ">=3.11"

[project.optional-dependencies]
docs = ["sphinx"]
testing = ["pandas>=1.5", "pytest"]
"""


def _inputs(project: str) -> dict:
    """``dependency_inputs`` in the exact shape the identity function folds."""
    import tomllib

    data = tomllib.loads(project)
    return {
        "extras": {k: list(v) for k, v in
                   ((data.get("project") or {}).get("optional-dependencies") or {}).items()},
        "groups": dict(data.get("dependency-groups") or {}),
        "requires": list((data.get("build-system") or {}).get("requires") or []),
        "requirements_names": [],
    }


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proc(code: int, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# 1. declared test dependency resolution
# ---------------------------------------------------------------------------
class TestDeclaredTestRequirements:
    def test_quant_econ_style_testing_extra_is_selected(self):
        """The exact defect shape: ``pandas`` declared only under ``testing``."""
        resolved = declared_test_requirements(_inputs(QUANTECON_PYPROJECT))
        assert "pandas>=1.5" in resolved["requirements"]
        assert resolved["extras"] == ["testing"]
        assert resolved["problems"] == []

    def test_non_test_extras_are_not_selected(self):
        """No indiscriminate install: ``docs``/``dev``/``lint`` are left alone."""
        deps = _inputs("""
[project]
name = "x"
[project.optional-dependencies]
docs = ["sphinx"]
dev = ["black"]
lint = ["ruff"]
""")
        resolved = declared_test_requirements(deps)
        assert resolved["requirements"] == []
        assert resolved["extras"] == []
        assert resolved["sources"] == []

    def test_only_the_test_named_member_of_a_mixed_table_is_selected(self):
        deps = _inputs("""
[project.optional-dependencies]
testing = ["pandas"]
dev = ["black", "sphinx"]
""")
        resolved = declared_test_requirements(deps)
        assert resolved["requirements"] == ["pandas"]

    def test_an_explicit_source_that_does_not_exist_is_blocking(self):
        """Naming a group that the project does not declare must NOT be a no-op:
        the builder would otherwise record an adjustment it never applied."""
        deps = _inputs("[project.optional-dependencies]\ndocs = ['sphinx']\n")
        resolved = declared_test_requirements(deps, extras=["testing"])
        blocking = [p for p in resolved["problems"] if p["scope"] == "blocking"]
        assert blocking and blocking[0]["code"] == "declared_test_source_missing"
        assert "testing" in blocking[0]["detail"]

    def test_a_group_name_is_normalised_before_comparison(self):
        deps = _inputs("""
[dependency-groups]
Test_Deps = ["pytest-xdist"]
""")
        assert declared_test_requirements(deps)["requirements"] == ["pytest-xdist"]

    def test_pep_735_include_group_is_expanded(self):
        deps = _inputs("""
[dependency-groups]
base = ["six"]
test = [{include-group = "base"}, "pytest-xdist"]
""")
        resolved = declared_test_requirements(deps)
        assert resolved["requirements"] == ["six", "pytest-xdist"]

    def test_a_dependency_group_cycle_is_reported_not_followed(self):
        deps = _inputs("""
[dependency-groups]
test = [{include-group = "inner"}]
inner = [{include-group = "test"}]
""")
        resolved = declared_test_requirements(deps)
        assert any(p["scope"] == "blocking" for p in resolved["problems"])

    def test_dependency_inputs_are_never_mutated(self):
        """``dependency_inputs`` is folded into ``build_identity``; mutating it
        would silently invalidate every recorded identity."""
        deps = _inputs(QUANTECON_PYPROJECT)
        before = copy.deepcopy(deps)
        declared_test_requirements(deps, groups=["test"], extras=["testing"],
                                   project=None)
        assert deps == before

    def test_requirements_file_options_are_reported_never_followed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n",
                                                 encoding="utf-8")
            (root / "requirements-test.txt").write_text(
                "# comment\n-r base.txt\n-e .\n--index-url http://example\n"
                "pytest-xdist>=6\n", encoding="utf-8")
            resolved = declared_test_requirements(
                _inputs("[project]\nname='x'\n"), project=root)
        assert resolved["requirements"] == ["pytest-xdist>=6"]
        codes = {p["code"] for p in resolved["problems"]}
        assert "requirements_option_not_followed" in codes
        # The include was reported, not resolved -- no phantom requirement.
        assert not any("base.txt" in r for r in resolved["requirements"])

    def test_source_file_digest_is_recorded_and_verifiable(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pyproject = root / "pyproject.toml"
            pyproject.write_text(QUANTECON_PYPROJECT, encoding="utf-8")
            resolved = declared_test_requirements(
                _inputs(QUANTECON_PYPROJECT), project=root)
            sources = [s for s in resolved["sources"]
                       if s["kind"] == "optional_dependency"]
            assert sources and sources[0]["name"] == "testing"
            assert sources[0]["sha256"] == _sha256(pyproject)
            assert sources[0]["file"] == str(pyproject)

    def test_a_group_and_an_extra_of_the_same_name_stay_separate(self):
        """Collapsing them would lose which one contributed the requirement."""
        deps = _inputs("""
[project.optional-dependencies]
testing = ["pandas"]
[dependency-groups]
testing = ["pytest-xdist"]
""")
        sources = declared_test_requirements(deps)["sources"]
        kinds = {s["kind"] for s in sources}
        assert kinds == {"optional_dependency", "dependency_group"}
        # Groups are consumed before extras, so both contributions survive and
        # neither is attributed to the other's source.
        assert declared_test_requirements(deps)["requirements"] == [
            "pytest-xdist", "pandas"]

    def test_an_empty_declaration_is_blocking_not_silently_dropped(self):
        deps = _inputs("[project.optional-dependencies]\ntesting = ['']\n")
        resolved = declared_test_requirements(deps)
        assert any(p["code"] == "empty_requirement" for p in resolved["problems"])

    def test_the_selection_set_is_the_documented_conservative_one(self):
        assert "testing" in TEST_DEPENDENCY_SOURCE_NAMES
        assert "dev" not in TEST_DEPENDENCY_SOURCE_NAMES
        assert "docs" not in TEST_DEPENDENCY_SOURCE_NAMES

    def test_a_marker_that_cannot_be_evaluated_here_is_reported(self):
        deps = _inputs(
            "[project.optional-dependencies]\n"
            "testing = ['pandas; extra == \"testing\"']\n")
        resolved = declared_test_requirements(deps)
        assert any(p["code"] == "test_dependency_marker_out_of_context"
                   for p in resolved["problems"])

    def test_parse_requirements_lines_keeps_declarations_verbatim(self):
        lines, options = parse_requirements_lines(
            "pandas>=1.5  # keep the specifier\n-e .\npytest\n")
        assert lines == ["pandas>=1.5", "pytest"]
        assert options == ["-e ."]


# ---------------------------------------------------------------------------
# 2. the install plan records them under their own mechanism
# ---------------------------------------------------------------------------
class TestBuilderInstallPlanDeclaredTestDependencies:
    def test_test_requirements_are_planned_under_their_own_mechanism(self):
        plan = builder_install_plan(
            _inputs(QUANTECON_PYPROJECT), pinned=["pytest==8.3.5"],
            test_requirements=["pandas>=1.5"])
        assert plan["test_requirements"] == ["pandas>=1.5"]
        assert plan["installed_packages"]["pandas"] == ["declared_test_dependency"]
        # The plugin subset is NOT inflated by a non-plugin dependency.
        assert "pandas" not in plan["plugin_requirements"]

    def test_the_default_plan_is_unchanged(self):
        """A caller that passes nothing gets exactly today's behaviour, so the
        offline classifier's view of the builder cannot drift."""
        plan = builder_install_plan(_inputs("[project]\nname='x'\n"),
                                    pinned=["pytest==8.3.5"])
        assert plan["test_requirements"] == []
        assert plan["explicit_requirements"] == ["pytest==8.3.5"]

    def test_uv_mode_refuses_a_test_set_it_cannot_honour(self):
        with pytest.raises(AcquisitionError, match="uv mode"):
            builder_install_plan(_inputs("[project]\nname='x'\n"),
                                 mode="uv", test_requirements=["pandas"])

    def test_the_builder_identity_marks_the_contract_change(self):
        """A workspace built before this change did NOT install the project's
        declared test set, so reusing it silently would re-run collection
        against an environment the new contract never produced."""
        assert "declared-test-deps v2" in BUILDER_SOURCE


# ---------------------------------------------------------------------------
# 3. build failures get an explicit reason code, never a flattering default
# ---------------------------------------------------------------------------
class TestBuildFailureReasonCodes:
    def test_timeout_is_named(self):
        assert classify_build_failure(
            subprocess.TimeoutExpired(cmd="pytest", timeout=1)) == "BUILD_TIMEOUT"

    def test_a_pip_resolver_complaint_is_a_resolution_failure(self):
        exc = RuntimeError(
            "command failed rc=1: pip install pandas\n"
            "ERROR: Could not find a version that satisfies the requirement pandas")
        assert classify_build_failure(exc) == "DEPENDENCY_RESOLUTION_FAILURE"

    def test_a_wheel_build_failure_is_an_installation_failure(self):
        exc = RuntimeError(
            "command failed rc=1: pip install pyyaml\n"
            "ERROR: Failed building wheel for pyyaml")
        assert classify_build_failure(exc) == "DEPENDENCY_INSTALLATION_FAILURE"

    def test_a_git_clone_failure_is_source_acquisition(self):
        exc = RuntimeError(
            "command failed rc=128: git clone --filter=blob:none https://x y\n"
            "fatal: could not read Username")
        assert classify_build_failure(exc) == "SOURCE_ACQUISITION_FAILURE"

    def test_an_unrecognised_failure_is_NOT_relabelled_infrastructure(self):
        """The asymmetric failure: calling a real dependency defect
        "infrastructure" would raise the yield by removing it from the
        denominator.  An unknown failure stays unknown."""
        code = classify_build_failure(RuntimeError("something odd happened"))
        assert code == "BUILD_FAILURE_UNCLASSIFIED"
        assert "INFRASTRUCTURE" not in code

    def test_the_declared_source_message_wins_over_a_pip_signature(self):
        exc = RuntimeError(
            "declared test dependencies cannot be resolved: "
            "declared_test_source_missing:optional_dependency:testing\n"
            "ERROR: Could not find a version that satisfies the requirement pandas")
        assert classify_build_failure(exc) == "DECLARED_TEST_SOURCE_UNRESOLVABLE"

    def test_an_unsatisfiable_requires_python_is_named(self):
        exc = RuntimeError(
            "no interpreter satisfies requires-python=['>=3.11']: []")
        assert classify_build_failure(exc) == "INTERPRETER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 4. a missing dependency is named, and can never be a PASS
# ---------------------------------------------------------------------------
class TestMissingDependencyClassification:
    def test_only_module_not_found_is_reported(self):
        assert missing_dependency_modules(
            "E   ModuleNotFoundError: No module named 'pandas'") == ["pandas"]

    def test_a_dotted_name_contributes_its_root(self):
        assert missing_dependency_modules(
            "E   No module named 'requests.adapters'") == ["requests"]

    def test_a_non_module_error_reports_nothing(self):
        text = "E   ValueError: boom\nE   SyntaxError: bad file"
        assert missing_dependency_modules(text) == []

    @staticmethod
    def _environment_error(stdout: str, stderr: str) -> dict:
        return _classify_side(
            side="base", returncode=1, stdout=stdout, stderr=stderr,
            targets=["tests/test_mod.py::test_target"],
            changed_modules=["pkg.newmod"],
        )

    def test_a_missing_module_is_tagged_and_is_never_a_pass(self):
        result = self._environment_error(
            "", "E   ModuleNotFoundError: No module named 'pandas'\n"
               "ERROR tests/test_mod.py::test_target")
        assert result["status"] == SIDE_FAIL
        assert "MISSING_DECLARED_DEPENDENCY" in result["reason_codes"]
        assert result["missing_modules"] == ["pandas"]

    def test_an_environment_failure_without_a_module_keeps_its_own_code(self):
        result = self._environment_error(
            "", "E   ImportError: cannot import name 'missing_attr' from "
                "'numpy'\nERROR tests/test_mod.py::test_target")
        assert result["status"] == SIDE_FAIL
        assert "ENVIRONMENT_COLLECTION_ERROR" in result["reason_codes"]
        assert "MISSING_DECLARED_DEPENDENCY" not in result["reason_codes"]

    def test_collection_errors_never_hide_behind_a_collected_target(self):
        """Partial collection: nodes WERE produced, but so were errors.  The
        errors decide, so a task can never pass by collecting something."""
        result = _classify_side(
            side="gold", returncode=1,
            stdout="tests/test_mod.py::test_target\n",
            stderr="ERROR tests/test_other.py::test_x\nE   ValueError: boom\n",
            targets=["tests/test_mod.py::test_target"],
            changed_modules=["pkg.newmod"],
        )
        assert result["status"] != SIDE_PASS
        assert result["collection_errors"] >= 1


# ---------------------------------------------------------------------------
# 5. the pre-check cannot be flattered
# ---------------------------------------------------------------------------
TASK = "acme__repo-1"
NODE = "tests/test_mod.py::test_target"
PATCH = (
    "diff --git a/pkg/newmod.py b/pkg/newmod.py\n"
    "--- /dev/null\n"
    "+++ b/pkg/newmod.py\n"
    "@@ -0,0 +1 @@\n"
    "+VALUE = 1\n"
)


def _workspace(root: pathlib.Path, task: str) -> pathlib.Path:
    ws = root / "ws" / task
    for tree, venv in (("repo", ".venv"), ("repo_gold", ".venv")):
        py = ws / tree / venv / "Scripts" / "python.exe"
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"")
    return ws


def _base_factory(root: pathlib.Path, *, tasks: list[str] | None = None):
    tasks = tasks or [TASK]
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        (inputs / f"{task}.json").write_text(
            json.dumps({
                "instance_id": task, "FAIL_TO_PASS": [NODE],
                "PASS_TO_PASS": [], "test_cmds": ["pytest -rA"], "patch": PATCH,
            }),
            encoding="utf-8",
        )
    manifest = root / "build_manifest.json"
    manifest.write_text(
        json.dumps({t: {"status": "built", "workspace": str(_workspace(root, t))}
                    for t in tasks}),
        encoding="utf-8",
    )
    registration = root / "registered_tasks.json"
    registration.write_text(json.dumps(tasks), encoding="utf-8")
    ledger = root / "state" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    AppendOnlyLedger(ledger)
    return {"manifest": manifest, "registration": registration,
            "ledger": ledger, "inputs": inputs, "out": root / "out"}


def _run(fx, *, runner=None, **over):
    kwargs = dict(
        repo_root=REPO, build_manifest_path=fx["manifest"],
        registration_path=fx["registration"], registration_id="reg-1",
        registration_sha256="a" * 64, ledger_paths=[fx["ledger"]],
        out_dir=fx["out"], inputs_dir=fx["inputs"],
        runner=runner or (lambda *_a, **_k: _proc(0, NODE + "\n")),
    )
    kwargs.update(over)
    return run_precheck(**kwargs)


class TestPrecheckCannotBeFlattered:
    def test_a_broken_collector_is_infrastructure_and_not_a_pass(self, tmp_path):
        def crash(*_a, **_k):
            raise OSError("spawn failed: no such venv interpreter")

        result = _run(_base_factory(tmp_path), runner=crash)
        assert result["verdict"] == SIDE_INCONCLUSIVE
        assert result["counts"]["passed"] == 0
        codes = result["tasks"][0]["sides"]["base"]["reason_codes"]
        assert "COLLECT_INFRASTRUCTURE_FAILURE" in codes
        assert result["tasks"][0]["status"] == SIDE_INCONCLUSIVE

    def test_a_collection_timeout_is_not_a_pass(self, tmp_path):
        def slow(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

        result = _run(_base_factory(tmp_path), runner=slow)
        assert result["verdict"] == SIDE_INCONCLUSIVE
        assert result["counts"]["passed"] == 0
        assert "COLLECT_TIMEOUT" in result["tasks"][0]["sides"]["base"]["reason_codes"]

    def test_a_repository_collection_failure_is_not_called_infrastructure(
            self, tmp_path):
        """D1 honesty: a genuine collection error keeps its own code."""
        def broken(*_a, **_k):
            return _proc(1, "", "ERROR tests/test_mod.py::test_target\n"
                                "E   ImportError: cannot import name 'foo' "
                                "from 'requests'\n")

        result = _run(_base_factory(tmp_path), runner=broken)
        assert result["verdict"] != SIDE_PASS
        codes = result["tasks"][0]["reason_codes"]
        assert "COLLECT_INFRASTRUCTURE_FAILURE" not in codes
        assert "MISSING_DECLARED_DEPENDENCY" not in codes

    def test_a_missing_declared_dependency_fails_the_task(self, tmp_path):
        def missing_pandas(*_a, **_k):
            return _proc(1, "", "E   ModuleNotFoundError: No module named "
                                "'pandas'\nERROR tests/test_mod.py::test_target\n")

        result = _run(_base_factory(tmp_path), runner=missing_pandas)
        assert result["verdict"] == SIDE_FAIL
        assert result["counts"]["passed"] == 0
        task = result["tasks"][0]
        assert task["status"] == SIDE_FAIL
        assert "MISSING_DECLARED_DEPENDENCY" in task["reason_codes"]
        assert task["parked"] is True

    def test_a_task_that_passes_on_one_side_only_is_not_a_pass(self, tmp_path):
        calls = {"n": 0}

        def one_side(*_a, **_k):
            calls["n"] += 1
            if calls["n"] % 2:
                return _proc(0, NODE + "\n")
            return _proc(1, "", "E   ImportError: cannot import name x\n"
                                "ERROR tests/test_mod.py::test_target\n")

        result = _run(_base_factory(tmp_path), runner=one_side)
        assert result["counts"]["passed"] == 0
        assert result["verdict"] != SIDE_PASS


# ---------------------------------------------------------------------------
# 6. fail-closed record integrity
# ---------------------------------------------------------------------------
class TestResultIntegrity:
    @staticmethod
    def _record(status: str = SIDE_PASS, *, sides: dict | None = None):
        if sides is None:
            sides = {
                s: {"side": s, "status": status, "command": ["python"],
                    "venv_python": "C:\\ws\\.venv\\Scripts\\python.exe"}
                for s in ("base", "gold")
            }
        return {"task_id": TASK, "status": status, "sides": sides,
                "reason_codes": [], "parked": status != SIDE_PASS}

    def test_a_selected_task_without_a_result_is_refused(self):
        with pytest.raises(PrecheckError, match="UNEXPLAINED_SKIP"):
            _validate_task_results([self._record()], [TASK, "acme__repo-2"])

    def test_a_pass_without_command_evidence_is_downgraded(self):
        sides = {
            "base": {"side": "base", "status": SIDE_PASS, "command": ["python"],
                     "venv_python": "x"},
            "gold": {"side": "gold", "status": SIDE_PASS, "command": None,
                     "venv_python": None},
        }
        records = _validate_task_results(
            [self._record(sides=sides)], [TASK])
        assert records[0]["status"] == SIDE_INCONCLUSIVE
        assert records[0]["parked"] is True
        assert "INVALID_EVIDENCE" in records[0]["reason_codes"]
        assert records[0]["invalid_evidence_sides"] == ["gold"]

    def test_an_evidenced_pass_is_left_alone(self):
        records = _validate_task_results([self._record()], [TASK])
        assert records[0]["status"] == SIDE_PASS
        assert records[0]["reason_codes"] == []

    def test_a_missing_side_is_evidence_invalid_not_a_pass(self):
        sides = {"base": {"side": "base", "status": SIDE_PASS,
                          "command": ["python"], "venv_python": "x"}}
        records = _validate_task_results(
            [self._record(sides=sides)], [TASK])
        assert records[0]["status"] == SIDE_INCONCLUSIVE


# ---------------------------------------------------------------------------
# 7. scope: build and pre-check cannot touch grants, credit or ledgers
# ---------------------------------------------------------------------------
#: Modules whose presence in the build or collection path would put a grant,
#: a scored evaluation or a ledger write on the critical path.
BANNED_IMPORTS = {
    "f2_authz_grant", "f2_s8_grant_verifier", "f2_s8_grant",
    "f2_s8_job", "f2_s8_producer", "f2_s8_runner", "f2_s8_pilot_live",
    "f2_s8_compose", "f2_s8_session", "f2_s8_pilot",
}

#: Symbols that would sign, consume, revoke or record anything protected.
BANNED_SYMBOLS = {
    "sign_grant", "consume_grant", "revoke_grant", "execute_grant",
    "append_scored", "write_outcome", "record_admission", "admit",
}


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[-1] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[-1])
    return found


def _called_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


class TestBuildAndPrecheckStayInScope:
    @pytest.mark.parametrize("module", ["f2_s8_pilot_build.py", "f2_s8_precheck.py"])
    def test_the_module_imports_no_grant_scored_or_ledger_writer(self, module):
        imported = _imported_modules(REPO / "qwen_train" / module)
        assert not (imported & BANNED_IMPORTS), imported & BANNED_IMPORTS

    @pytest.mark.parametrize("module", ["f2_s8_pilot_build.py", "f2_s8_precheck.py"])
    def test_the_module_calls_no_protected_operation(self, module):
        called = _called_names(REPO / "qwen_train" / module)
        assert not (called & BANNED_SYMBOLS), called & BANNED_SYMBOLS

    def test_a_run_leaves_the_ledger_byte_identical(self, tmp_path):
        """Behavioural proof, stronger than a source scan: whatever the
        pre-check does with the ledger, the file's bytes are unchanged."""
        fx = _base_factory(tmp_path)
        AppendOnlyLedger(fx["ledger"]).append({"seq": 1, "kind": "control"})
        before = fx["ledger"].read_bytes()
        _run(fx)
        assert fx["ledger"].read_bytes() == before

    def test_the_output_directory_may_not_overlap_ledger_state(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError, match="protected state"):
            _run(fx, out_dir=fx["ledger"].parent / "nested")

    def test_the_output_directory_may_not_be_a_scored_results_directory(
            self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError, match="scored-results"):
            _run(fx, out_dir=tmp_path / "pilot_results_new")


# ---------------------------------------------------------------------------
# 8. build success alone schedules nothing
# ---------------------------------------------------------------------------
class TestBuildSuccessSchedulesNothing:
    def test_the_result_awards_nothing_and_says_so(self, tmp_path):
        result = _run(_base_factory(tmp_path))
        assert result["counts"]["passed"] == 1
        # A collection PASS must carry its own disclaimer, and must not create
        # any admission- or credit-shaped field.
        assert any("no credit was awarded" in n for n in result["notes"])
        task = result["tasks"][0]
        for forbidden in ("admitted", "credit", "scored", "outcome"):
            assert forbidden not in task
        assert task["parked"] is False

    def test_only_built_and_registered_tasks_are_selectable(self, tmp_path):
        fx = _base_factory(tmp_path, tasks=[TASK, "acme__repo-2"])
        # A workspace recorded as a build failure must never become a candidate.
        manifest = json.loads(fx["manifest"].read_text(encoding="utf-8"))
        manifest["acme__repo-2"] = {"status": "build_failure",
                                    "reason_code": "DEPENDENCY_RESOLUTION_FAILURE",
                                    "detail": "no matching distribution"}
        fx["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
        result = _run(fx, tasks=[TASK, "acme__repo-2"])
        assert [t["task_id"] for t in result["tasks"]] == [TASK]
        excluded = {e["task_id"]: e["reason"] for e in result["excluded"]}
        assert excluded.get("acme__repo-2") == "WORKSPACE_NOT_BUILT"
        assert result["verdict"] == SIDE_PASS
        assert result["counts"]["passed"] == 1
