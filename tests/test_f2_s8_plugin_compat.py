"""Session 42 regression coverage: shared install-plan logic + offline classifier.

Covers the proven defect class (a declared pytest plugin the environment never
installs -> collection failure -> every declared node missing) and the packaging
edge cases that decide whether a classification may be trusted at all:

* PEP 735 ``include-group`` (nested, cyclic, missing, malformed)
* PEP 503 name normalisation, PEP 508 specifiers/markers/direct references
* duplicate constraints -- combined when compatible, FAIL CLOSED when not
* optional extras selected vs not selected by the build contract
* ``requirements*.txt`` and ``[build-system].requires`` are NOT installation
* a declared plugin the builder does not install
* builder/classifier plan equivalence
* unknown-vs-compatible: dynamic metadata, absent project table, no local tree
"""
from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from qwen_train.f2_registration import is_pytest_command
from qwen_train.f2_s8_acquisition import (
    AcquisitionError,
    builder_install_plan,
    declared_test_plugins,
    declared_test_plugins_report,
    dependency_inputs_from_pyproject,
    dependency_metadata_status,
    install_context_from_pyproject,
    parse_requirements_names,
    project_install_context,
    resolve_dependency_groups,
    static_dependency_inputs,
)
from qwen_train.f2_s8_pilot_build import EXTRA_DEPS, GIT, PYTEST_PIN, UV_ALL_EXTRAS
from qwen_train.f2_s8_plugin_compat import (
    CLASS_COMPATIBLE,
    CLASS_INCOMPATIBLE,
    CLASS_NOT_APPLICABLE,
    CLASS_UNKNOWN,
    PLUGIN_CHECK_RULE_VERSION,
    GitTreeReader,
    _classify_one,
    _ledger_task_ids,
    _project_root,
    _read_context,
    _write_report,
)

# Captured at import (before `tests/conftest.py`'s autouse global_subprocess_mock
# patches it) so these tests exercise REAL local `git` -- both when the fixture
# repos below are created and when the classifier reads git objects from them.
_REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def _use_real_subprocess(global_subprocess_mock):
    """Restore the genuine Popen: this module drives real local git subprocesses.

    Depends on the conftest's autouse mock explicitly so ordering is guaranteed:
    the global mock is installed first, then this fixture restores the real
    class for the test body (same pattern as
    ``tests/test_f2_arm_workspace_isolation.py``).
    """
    subprocess.Popen = _REAL_POPEN
    yield


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _deps(**over) -> dict:
    base = {"build_requires": [], "groups": {}, "extras": {}, "requirements_names": []}
    base.update(over)
    return base


def _git_repo(root: Path, files: dict[str, str]) -> tuple[Path, str]:
    """A local git repo holding ``files`` -- no network, no checkout elsewhere."""
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    run = lambda *args: subprocess.run([GIT, "-C", str(repo), *args],  # noqa: E731
                                       capture_output=True, text=True, timeout=120)
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-q", "-m", "fixture")
    head = run("rev-parse", "HEAD").stdout.strip()
    assert len(head) == 40, head
    return repo, head


def _classify_repo(files: dict[str, str], tmp_path: Path,
                   *, task_id: str = "demo__demo-1") -> dict:
    trees = tmp_path / "trees"
    repo, head = _git_repo(trees / task_id, files)
    assert repo.is_dir() and (repo / ".git").exists()
    return _classify_one(
        task_id,
        {"source": "swe-bench-live", "test_cmd": "pytest -rA",
         "base_commit": head, "repo": "demo/demo"},
        tree_roots=(trees,),
    )


DEMO_PROJECT = """
[project]
name = "demo"
version = "0.1.0"
dependencies = []

[dependency-groups]
dev = ["pytest-django>=4.9.0"]
"""

# --------------------------------------------------------------------------
# PEP 735 group resolution
# --------------------------------------------------------------------------
class TestGroupResolution:
    def test_nested_include_groups_reach_a_plugin_at_any_depth(self):
        deps = _deps(groups={
            "dev": [{"include-group": "testing"}],
            "testing": [{"include-group": "test-core"}],
            "test-core": ["pytest-django>=4.9.0", "mkdocs>=1.6"],
        })
        expanded, problems = resolve_dependency_groups(deps["groups"])
        assert problems == []
        assert expanded["dev"] == ["pytest-django>=4.9.0", "mkdocs>=1.6"]
        assert declared_test_plugins(deps) == ["pytest-django"]

    def test_a_cycle_is_reported_and_the_plan_fails_closed(self):
        deps = _deps(groups={"dev": [{"include-group": "testing"}],
                             "testing": [{"include-group": "dev"}]})
        _, problems = resolve_dependency_groups(deps["groups"])
        assert {p["code"] for p in problems} == {"dependency_group_cycle"}
        with pytest.raises(AcquisitionError, match="dependency_group_cycle"):
            builder_install_plan(deps, pinned=[PYTEST_PIN])

    def test_a_missing_group_is_reported_and_the_plan_fails_closed(self):
        deps = _deps(groups={"dev": [{"include-group": "nope"}]})
        _, problems = resolve_dependency_groups(deps["groups"])
        assert {p["code"] for p in problems} == {"dependency_group_missing"}
        with pytest.raises(AcquisitionError, match="dependency_group_missing"):
            builder_install_plan(deps, pinned=[PYTEST_PIN])

    def test_a_malformed_directive_is_reported_and_the_plan_fails_closed(self):
        deps = _deps(groups={"dev": [{"wrong-key": "test"}]})
        _, problems = resolve_dependency_groups(deps["groups"])
        assert {p["code"] for p in problems} == {"unsupported_group_entry"}
        with pytest.raises(AcquisitionError, match="unsupported_group_entry"):
            builder_install_plan(deps, pinned=[PYTEST_PIN])

    def test_include_group_tables_survive_extraction_as_structured_objects(self):
        data = {"dependency-groups": {"dev": [{"include-group": "test"}],
                                      "test": ["pytest-xdist"]}}
        deps = dependency_inputs_from_pyproject(data)
        assert deps["groups"]["dev"] == [{"include-group": "test"}]  # NOT stringified
        assert declared_test_plugins(deps) == ["pytest-xdist"]

    def test_a_self_including_group_does_not_hang_and_is_untrusted(self):
        deps = _deps(groups={"dev": [{"include-group": "dev"}, "pytest-cov"]})
        _, problems = resolve_dependency_groups(deps["groups"])
        assert any(p["code"] == "dependency_group_cycle" for p in problems)
        with pytest.raises(AcquisitionError):
            builder_install_plan(deps)


# --------------------------------------------------------------------------
# normalisation and PEP 508 parsing
# --------------------------------------------------------------------------
class TestRequirementParsing:
    def test_name_spellings_normalise_to_one_distribution(self):
        deps = _deps(groups={"dev": ["pytest_django>=4.9.0", "PyTest.Django>=4.9.0"]})
        assert declared_test_plugins(deps) == ["pytest-django"]
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert plan["explicit_requirements"] == ["pytest==8.3.5", "pytest-django>=4.9.0"]
        assert plan["packages"] == ["pytest", "pytest-django"]

    def test_a_single_declaration_is_passed_through_verbatim(self):
        deps = _deps(groups={"dev": ["pytest-cov>=5.0.0,<6"]})
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert "pytest-cov>=5.0.0,<6" in plan["explicit_requirements"]

    def test_an_environment_marker_keeps_its_declaration_and_flags_offline(self):
        deps = _deps(groups={"dev": ["pytest-django>=4.9.0; python_version >= '3.9'"]})
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert any(r.startswith("pytest-django") and ";" in r
                   for r in plan["explicit_requirements"])
        flagged = [p for p in plan["problems"] if p["code"] == "conditional_declaration"]
        assert flagged and flagged[0]["scope"] == "offline"

    def test_a_direct_reference_is_parsed_and_flagged_offline(self):
        raw = "pytest-django @ https://example.invalid/pytest_django-4.9-py3-none-any.whl"
        deps = _deps(groups={"dev": [raw]})
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert raw in plan["explicit_requirements"]
        flagged = [p for p in plan["problems"] if p["code"] == "direct_reference"]
        assert flagged and flagged[0]["scope"] == "offline"

    def test_a_pytest_declaration_that_cannot_be_parsed_blocks_the_install(self):
        deps = _deps(groups={"dev": ["pytest-django>=4.9.0; bogus"]})
        with pytest.raises(AcquisitionError, match="unparsable_requirement"):
            builder_install_plan(deps, pinned=[PYTEST_PIN])

    def test_an_unreadable_non_plugin_entry_does_not_break_the_install(self):
        # a uv-style local path: irrelevant to this plan, but still recorded so
        # the classifier reports UNKNOWN instead of "no plugin declared".
        deps = _deps(groups={"dev": ["./libs/local-plugin"]})
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        flagged = [p for p in plan["problems"] if p["code"] == "unparsable_requirement"]
        assert flagged and flagged[0]["scope"] == "offline"
        assert plan["explicit_requirements"] == [PYTEST_PIN]


class TestDuplicateConstraints:
    def test_compatible_constraints_are_combined_never_dropped(self):
        deps = _deps(groups={"dev": ["pytest-xdist>=6"]})
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN],
                                    extra_deps=["pytest-xdist"])
        assert plan["explicit_requirements"] == ["pytest==8.3.5", "pytest-xdist>=6"]
        assert plan["installed_packages"]["pytest-xdist"] == [
            "dependency_group", "task_extra_deps"]

    def test_the_same_declaration_twice_is_one_argument(self):
        deps = _deps(groups={"dev": ["pytest-cov"]},
                     extras={"test": ["pytest-cov"]})
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN], include_extras=True)
        assert plan["explicit_requirements"] == ["pytest==8.3.5", "pytest-cov"]

    def test_contradictory_constraints_fail_closed(self):
        deps = _deps(groups={"dev": ["pytest-xdist<6"]})
        with pytest.raises(AcquisitionError, match="conflicting declarations"):
            builder_install_plan(deps, pinned=[PYTEST_PIN],
                                 extra_deps=["pytest-xdist>=7"])

    def test_contradictory_equality_pins_fail_closed(self):
        deps = _deps(groups={"dev": ["pytest-cov==5.0.0"]})
        with pytest.raises(AcquisitionError, match="conflicting declarations"):
            builder_install_plan(deps, pinned=[PYTEST_PIN],
                                 extra_deps=["pytest-cov==5.1.0"])


# --------------------------------------------------------------------------
# what is and is not an installation mechanism
# --------------------------------------------------------------------------
class TestInstallationMechanisms:
    def test_the_known_defect_declared_pytest_django_in_dev_is_installed(self):
        deps = _deps(groups={"dev": ["django-environ>=0.12.0", "pytest>=8.3.4",
                                     "pytest-django>=4.9.0"]})
        assert declared_test_plugins(deps) == ["pytest-django"]
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert "pytest-django>=4.9.0" in plan["explicit_requirements"]
        assert plan["installed_packages"]["pytest-django"] == ["dependency_group"]

    def test_extras_are_excluded_unless_the_contract_selects_them(self):
        deps = _deps(extras={"test": ["pytest-xdist>=6"]})
        pip_plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        uv_plan = builder_install_plan(deps, pinned=[PYTEST_PIN], mode="uv")
        assert "pytest-xdist" not in pip_plan["packages"]
        assert pip_plan["include_extras"] is False
        assert "pytest-xdist" in uv_plan["packages"]
        assert uv_plan["include_extras"] is True       # uv sync --all-extras
        assert uv_plan["explicit_requirements"] == []  # uv owns that environment
        # The REPORT is contract-scoped by the same rule: with no contract
        # selecting extras, a plugin declared only in an extra is not in scope
        # (the Session-41 correction recorded in test_f2_s8_acquisition.py).
        assert declared_test_plugins(deps) == []
        assert declared_test_plugins(deps, include_extras=True) == ["pytest-xdist"]

    def test_a_plugin_declared_only_in_an_unselected_extra_is_not_installed(self):
        deps = _deps(extras={"test": ["pytest-xdist>=6"]})
        declared = set(declared_test_plugins(deps, include_extras=True))
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert declared - set(plan["packages"]) == {"pytest-xdist"}

    def test_requirements_txt_is_not_installed_by_the_builder(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="x"\nversion="0.1"\ndependencies=[]\n'
            "[dependency-groups]\ndev=[]\n", encoding="utf-8")
        (tmp_path / "requirements.txt").write_text(
            "# a comment\n-r base.txt\npytest-xdist>=6\n", encoding="utf-8")
        deps = static_dependency_inputs(tmp_path)
        assert deps["requirements_names"] == ["pytest-xdist"]   # parsed as before
        # ...DECLARED (rule v2: requirements entries are declared-but-never-
        # installed), so a task cannot be certified compatible while its own
        # requirements file asks for a plugin the environment provably lacks...
        assert "pytest-xdist" in declared_test_plugins(deps)
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert "pytest-xdist" not in plan["packages"]           # ...but NOT installed

    def test_an_unreadable_requirements_entry_never_blocks_the_install(self, tmp_path):
        # requirements entries take part in NO install this plan performs, so
        # an unreadable one may change a classification but must never break
        # a build (unlike a group entry, which is installed and can block).
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="x"\nversion="0.1"\ndependencies=[]\n', encoding="utf-8")
        (tmp_path / "requirements.txt").write_text(
            "git+https://example.invalid/x.git\n", encoding="utf-8")
        deps = static_dependency_inputs(tmp_path)
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        flagged = [p for p in plan["problems"] if p["code"] == "unparsable_requirement"]
        assert flagged and flagged[0]["scope"] == "offline"    # never blocking
        assert plan["explicit_requirements"] == [PYTEST_PIN]

    def test_build_system_requirements_are_neither_plugins_nor_installed(self):
        deps = _deps(build_requires=["setuptools>=61", "pytest-build-helper"])
        assert declared_test_plugins(deps) == []
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN])
        assert "pytest-build-helper" not in plan["packages"]

    def test_runtime_dependencies_are_installed_by_the_project_install(self):
        deps = _deps()
        plan = builder_install_plan(deps, pinned=[PYTEST_PIN],
                                    runtime_dependencies=["pytest-django>=4.9.0"])
        assert plan["packages"] == ["pytest", "pytest-django"]
        assert plan["installed_packages"]["pytest-django"] == ["runtime_dependency"]
        # ...but they are not shelled out: the editable install brings them.
        assert plan["explicit_requirements"] == [PYTEST_PIN]
        assert "pytest-django" in declared_test_plugins(
            deps, runtime_dependencies=["pytest-django>=4.9.0"])

    def test_pinned_and_task_extra_requirements_stay_distinct_mechanisms(self):
        plan = builder_install_plan(_deps(), pinned=[PYTEST_PIN],
                                    extra_deps=list(EXTRA_DEPS["python__mypy-19767"]))
        assert plan["explicit_requirements"][0] == PYTEST_PIN
        assert plan["installed_packages"]["pytest-xdist"] == ["task_extra_deps"]

    def test_uv_default_groups_control_what_uv_installs(self):
        deps = _deps(groups={"dev": ["pytest-cov"], "docs": ["mkdocs"]})
        plan = builder_install_plan(deps, mode="uv", uv_default_groups=["dev"])
        assert "pytest-cov" in plan["packages"] and "mkdocs" not in plan["packages"]
        plan_all = builder_install_plan(deps, mode="uv", uv_default_groups=["all"])
        assert "mkdocs" in plan_all["packages"]

    def test_an_unknown_install_mode_is_refused(self):
        with pytest.raises(AcquisitionError, match="unknown install mode"):
            builder_install_plan(_deps(), mode="conda")


class TestMetadataCompleteness:
    def test_dynamic_project_dependencies_are_reported(self):
        assert dependency_metadata_status(
            {"project": {"name": "x", "dynamic": ["dependencies"]}}
        ) == ["dynamic_project_dependencies"]
        assert dependency_metadata_status(
            {"project": {"name": "x", "dynamic": ["optional-dependencies"]}}
        ) == ["dynamic_project_optional_dependencies"]

    def test_an_absent_project_table_is_reported(self):
        assert dependency_metadata_status({"tool": {"pytest": {}}}) == [
            "project_table_absent"]

    def test_setuptools_dynamic_dependency_generation_is_reported(self):
        assert dependency_metadata_status(
            {"project": {"name": "x"},
             "tool": {"setuptools": {"dynamic": {"dependencies": {"file": ["reqs.txt"]}}}}}
        ) == ["dynamic_setuptools_dependencies"]

    def test_complete_static_metadata_reports_nothing(self):
        assert dependency_metadata_status(
            {"project": {"name": "x", "dependencies": ["click"]}}) == []


# --------------------------------------------------------------------------
# builder <-> classifier equivalence
# --------------------------------------------------------------------------
class TestPlanEquivalence:
    def test_both_readers_of_one_pyproject_produce_identical_plans(self, tmp_path):
        text = ('[project]\nname="demo"\nversion="0.1"\n'
                'dependencies=["click>=8"]\n\n'
                '[dependency-groups]\ndev=["pytest-django>=4.9.0"]\n')
        req_text = "# test deps\n-r other.txt\ndjango>=5\npytest-timeout>=6\n"
        (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
        (tmp_path / "requirements.txt").write_text(req_text, encoding="utf-8")
        # builder side: reads the checked-out tree (incl. requirements*.txt)
        builder_context = project_install_context(tmp_path)
        # classifier side: reads the same bytes as a git object / parsed text,
        # through the SAME shared requirements parser
        classifier_context = install_context_from_pyproject(
            tomllib.loads(text),
            requirements_names=parse_requirements_names(req_text))
        assert (builder_context["dependency_inputs"]["requirements_names"]
                == classifier_context["dependency_inputs"]["requirements_names"]
                == ["django", "pytest-timeout"])
        common = dict(pinned=[PYTEST_PIN], extra_deps=[], include_extras=False)
        assert (builder_install_plan(builder_context["dependency_inputs"],
                                     runtime_dependencies=builder_context["runtime_dependencies"],
                                     **common)
                == builder_install_plan(
                    classifier_context["dependency_inputs"],
                    runtime_dependencies=classifier_context["runtime_dependencies"],
                    **common))

    def test_the_builder_takes_its_install_arguments_from_the_shared_plan(self):
        from qwen_train.f2_s8_pilot_build import build  # noqa: F401  (import only)

        source = Path(build.__code__.co_filename).read_text(encoding="utf-8")
        assert "builder_install_plan(" in source
        assert 'plan["plugin_requirements"]' in source
        # no second, hand-maintained package list may creep back in
        assert "declared_test_plugins(" not in source


# --------------------------------------------------------------------------
# classifier behaviour (real local git object, no network, no checkout)
# --------------------------------------------------------------------------
class TestClassifier:
    def test_a_declared_and_installed_plugin_is_compatible(self, tmp_path):
        result = _classify_repo({"pyproject.toml": DEMO_PROJECT}, tmp_path)
        assert result["classification"] == CLASS_COMPATIBLE
        assert result["declared_plugins"] == ["pytest-django"]
        assert result["missing_plugins"] == []
        assert result["reasons"] == []

    def test_a_plugin_the_builder_does_not_install_is_incompatible(self, tmp_path):
        result = _classify_repo({"pyproject.toml":
                                 '[project]\nname="demo"\nversion="0.1"\n'
                                 'dependencies=[]\n'
                                 '[project.optional-dependencies]\n'
                                 'test=["pytest-xdist>=6"]\n'}, tmp_path)
        assert result["classification"] == CLASS_INCOMPATIBLE
        assert result["missing_plugins"] == ["pytest-xdist"]
        assert result["mismatches"][0]["plugin"] == "pytest-xdist"
        assert "optional extra" in result["mismatches"][0]["note"]

    def test_dynamic_dependency_metadata_is_unknown_not_compatible(self, tmp_path):
        result = _classify_repo({"pyproject.toml":
                                 '[project]\nname="demo"\nversion="0.1"\n'
                                 'dynamic=["dependencies"]\n'}, tmp_path)
        assert result["classification"] == CLASS_UNKNOWN
        assert result["reasons"] == ["dynamic_project_dependencies"]

    def test_a_requirements_declared_plugin_is_incompatible_not_silent(self, tmp_path):
        # rule v2: requirements*.txt is a declared-but-never-installed source.
        # A plugin named only there must surface as OBSERVED_INCOMPATIBLE --
        # v1 let such a task be certified compatible.
        result = _classify_repo(
            {"pyproject.toml":
             '[project]\nname="demo"\nversion="0.1"\ndependencies=[]\n'
             '[dependency-groups]\ndev = ["pytest>=8"]\n',
             "requirements.txt": "# test deps\npytest-xdist>=6\n"},
            tmp_path)
        assert result["classification"] == CLASS_INCOMPATIBLE
        assert result["declared_plugins"] == ["pytest-xdist"]
        assert result["missing_plugins"] == ["pytest-xdist"]
        assert "requirements*.txt" in result["mismatches"][0]["note"]
        assert result["evidence"]["requirements_names"] == ["pytest-xdist"]

    def test_requirements_missing_under_delegated_metadata_is_unknown(self, tmp_path):
        # With dynamic dependency metadata, setup.py may read requirements.txt
        # into install_requires, so "absent from the static plan" cannot prove
        # the environment lacks the plugin: UNKNOWN, never INCOMPATIBLE.
        result = _classify_repo(
            {"pyproject.toml":
             '[project]\nname="demo"\nversion="0.1"\ndynamic=["dependencies"]\n',
             "requirements.txt": "pytest-xdist>=6\n"},
            tmp_path)
        assert result["classification"] == CLASS_UNKNOWN
        assert "dynamic_project_dependencies" in result["reasons"]
        assert "requirements_plugin_under_delegated_metadata" in result["reasons"]
        assert result["declared_plugins"] == ["pytest-xdist"]
        assert result["missing_plugins"] == ["pytest-xdist"]
        assert result["evidence"]["unverifiable_missing"] == ["pytest-xdist"]

    def test_unresolvable_dependency_structure_is_unknown_not_compatible(self, tmp_path):
        # A cyclic PEP 735 include-group graph means the install plan itself
        # fails closed (AcquisitionError); the classifier must surface that as
        # UNKNOWN with the structure named -- never as "compatible".
        text = ('[project]\nname="demo"\nversion="0.1"\ndependencies=[]\n'
                '[dependency-groups]\n'
                'dev = [{include-group = "testing"}]\n'
                'testing = [{include-group = "dev"}]\n')
        report = declared_test_plugins_report(
            dependency_inputs_from_pyproject(tomllib.loads(text)))
        assert any(p["code"] == "dependency_group_cycle"
                   and p["scope"] == "blocking" for p in report["problems"])
        result = _classify_repo({"pyproject.toml": text}, tmp_path)
        assert result["classification"] == CLASS_UNKNOWN
        assert result["reasons"] == ["dependency_structure_untrusted"]
        assert "dependency_group_cycle" in result["evidence"]["untrusted_detail"]

    def test_a_pyproject_without_a_project_table_is_unknown(self, tmp_path):
        result = _classify_repo({"pyproject.toml":
                                 '[tool.pytest.ini_options]\naddopts="-q"\n'}, tmp_path)
        assert result["classification"] == CLASS_UNKNOWN
        assert result["reasons"] == ["project_table_absent"]

    def test_setup_py_only_projects_are_unknown(self, tmp_path):
        result = _classify_repo({"setup.py": "from setuptools import setup\nsetup()\n"},
                                tmp_path)
        assert result["classification"] == CLASS_UNKNOWN
        assert result["reasons"] == ["pyproject_absent"]

    def test_no_local_tree_is_unknown_with_a_reason(self, tmp_path):
        result = _classify_one("nothing__nothing-1",
                               {"source": "swe-bench-live", "test_cmd": "pytest -rA",
                                "base_commit": "0" * 40, "repo": "nothing/nothing"},
                               tree_roots=(tmp_path,))
        assert result["classification"] == CLASS_UNKNOWN
        assert result["reasons"] == ["project_tree_unavailable"]

    def test_an_absent_commit_object_is_unknown(self, tmp_path):
        trees = tmp_path / "trees"
        _git_repo(trees / "demo__demo-1", {"pyproject.toml": DEMO_PROJECT})
        result = _classify_one("demo__demo-1",
                               {"source": "swe-bench-live", "test_cmd": "pytest -rA",
                                "base_commit": "f" * 40, "repo": "demo/demo"},
                               tree_roots=(trees,))
        assert result["classification"] == CLASS_UNKNOWN
        assert result["reasons"] == ["base_commit_object_unavailable"]

    def test_a_non_pytest_command_is_not_applicable(self, tmp_path):
        result = _classify_one("demo__demo-1",
                               {"source": "swe-rebench-v2",
                                "test_cmd": "npm run test:unit",
                                "base_commit": "0" * 40, "repo": "demo/demo"},
                               tree_roots=(tmp_path,))
        assert result["classification"] == CLASS_NOT_APPLICABLE
        assert result["reasons"] == ["declared_command_not_pytest"]
        assert not is_pytest_command("npm run test:unit")

    def test_classification_is_deterministic(self, tmp_path):
        trees = tmp_path / "trees"
        repo, head = _git_repo(trees / "demo__demo-1", {"pyproject.toml": DEMO_PROJECT})
        assert repo.is_dir()
        meta = {"source": "swe-bench-live", "test_cmd": "pytest -rA",
                "base_commit": head, "repo": "demo/demo"}
        first = _classify_one("demo__demo-1", meta, tree_roots=(trees,))
        second = _classify_one("demo__demo-1", meta, tree_roots=(trees,))
        assert first == second
        # rule v2 (Session 42): requirements*.txt joins the declared set;
        # any rule change must bump the version, never silently compare.
        assert PLUGIN_CHECK_RULE_VERSION == "f2-plugin-compat-v2"

    def test_project_root_resolution_mirrors_the_builder(self):
        assert _project_root(["pyproject.toml", "src/x.py"]) == ("", None)
        assert _project_root(["hypothesis-python/pyproject.toml"]) == (
            "hypothesis-python/", None)
        assert _project_root(["a/pyproject.toml", "b/pyproject.toml"]) == (
            None, "project_root_ambiguous")
        assert _project_root(["src/x.py"]) == (None, "project_root_not_found")

    def test_read_context_returns_a_reason_not_a_guess(self, tmp_path):
        repo, head = _git_repo(tmp_path / "r", {"pyproject.toml": DEMO_PROJECT})
        context, reason = _read_context(GitTreeReader(repo, head))
        assert reason is None and context is not None
        assert context["metadata_status"] == []
        assert context["uv_default_groups"] == ["dev"]
        assert context["dependency_inputs"]["requirements_names"] == []


# --------------------------------------------------------------------------
# ledgers and report output (READ ONLY / tmp only)
# --------------------------------------------------------------------------
class TestSourcePriorityPopulation:
    """The SOURCE_PRIORITY-resolved cross-source ids ARE classified.

    They are legitimate registered rows, so removing them from the candidate
    population subtracted resolved tasks (a double subtraction against the
    registration's own accounting).  Inclusion is candidate selection only --
    never admission, buildability, or scientific analyzability.
    """

    def test_the_operative_row_is_the_highest_priority_source(self, tmp_path, monkeypatch):
        from qwen_train import f2_s8_plugin_compat as pc

        common = {
            "instance_id": "o__a-1", "repo": "o/a", "base_commit": "a" * 40,
            "FAIL_TO_PASS": ["tests/t.py::x"], "PASS_TO_PASS": ["tests/t.py::y"],
            "patch": "diff",
        }
        live = tmp_path / "live.jsonl"
        reb = tmp_path / "reb.jsonl"
        live.write_text(
            json.dumps({**common, "test_cmds": ["pytest -q"]}) + "\n",
            encoding="utf-8")
        reb.write_text(
            json.dumps({**common, "install_config": {"test_cmd": "npm test"}}) + "\n",
            encoding="utf-8")
        monkeypatch.setattr(pc, "CORPUS",
                            {"swe-bench-live": live, "swe-rebench-v2": reb})

        rows, _ = pc._slim_rows({"o__a-1"})
        # The union keeps the highest-priority source; classification must read
        # the SAME row, not whichever corpus happened to be iterated last.
        assert rows["o__a-1"]["source"] == "swe-bench-live"
        assert rows["o__a-1"]["test_cmd"] == "pytest -q"

    def _population(self, monkeypatch, tmp_path, *, ordered, conflicts, recorded):
        from qwen_train import f2_s8_plugin_compat as pc

        order_file = tmp_path / "order.json"
        order_file.write_text(
            json.dumps([{"instance_id": t} for t in ordered]), encoding="utf-8")
        monkeypatch.setattr(pc, "ORDER", order_file)
        monkeypatch.setattr(pc, "CORPUS", {})
        monkeypatch.setattr(pc, "scan_cross_source_conflicts",
                            lambda corpus: dict(conflicts))
        monkeypatch.setattr(pc, "_ledger_task_ids",
                            lambda path: (list(recorded), len(recorded)))
        return pc.classify_population()

    def test_a_resolved_conflict_is_included_not_subtracted(self, tmp_path, monkeypatch):
        r = self._population(monkeypatch, tmp_path, ordered=["a", "b", "c"],
                             conflicts={"b": "canonical records disagree"},
                             recorded=["c"])
        assert r["population"]["selectable"] == 2
        assert r["population"]["conflicts_included_in_population"] == 1
        assert r["resolved_by_source_priority"] == ["b"]
        ids = {x["task_id"] for x in r["results"]}
        assert ids == {"a", "b"}  # the conflict IS classified
        assert "c" not in ids     # the recorded task is still excluded

    def test_a_conflict_that_is_already_recorded_is_excluded_once(self, tmp_path, monkeypatch):
        r = self._population(monkeypatch, tmp_path, ordered=["a", "b"],
                             conflicts={"b": "canonical records disagree"},
                             recorded=["b"])
        assert r["population"]["conflicts_already_recorded_overlap"] == 1
        assert r["population"]["selectable"] == 1
        assert {x["task_id"] for x in r["results"]} == {"a"}

    def test_inclusion_is_candidate_selection_only(self, tmp_path, monkeypatch):
        r = self._population(monkeypatch, tmp_path, ordered=["a", "b"],
                             conflicts={"b": "canonical records disagree"},
                             recorded=[])
        # No corpus row -> UNKNOWN, never compatible; and nothing claims more.
        assert all(x["classification"] == CLASS_UNKNOWN for x in r["results"])
        assert any("NOT admission" in item for item in r["limitations"])


class TestLedgerAndReport:
    def test_ledger_rows_and_distinct_task_ids_are_counted_separately(self, tmp_path):
        ledger = tmp_path / "ledger.jsonl"
        ledger.write_text(
            "\n".join([json.dumps({"task_id": "a"}),
                       json.dumps({"task_id": "b"}),
                       json.dumps({"task_id": "b"})]) + "\n", encoding="utf-8")
        ids, rows = _ledger_task_ids(ledger)
        assert ids == ["a", "b"] and rows == 3

    def test_a_missing_ledger_is_empty_not_an_error(self, tmp_path):
        assert _ledger_task_ids(tmp_path / "absent.jsonl") == ([], 0)

    def test_the_report_is_written_with_a_verifiable_digest(self, tmp_path):
        report = {
            "rule_version": PLUGIN_CHECK_RULE_VERSION,
            "generated_at": "2026-10-15T00:00:00Z",
            "elapsed_s": 0.1,
            "population": {"registered": 3828, "conflicts_total_both_sources": 196,
                           "conflicts_in_registration": 185,
                           "conflicts_already_recorded_overlap": 0,
                           "conflict_resolution_policy": "SOURCE_PRIORITY: included",
                           "conflicts_included_in_population": 185,
                           "ledger_rows": 9, "ledger_distinct_task_ids": 7,
                           "selectable": 3821, "classified": 2},
            "counts": {CLASS_COMPATIBLE: 1, CLASS_INCOMPATIBLE: 1, CLASS_UNKNOWN: 0,
                       CLASS_NOT_APPLICABLE: 0},
            "percentages": {CLASS_COMPATIBLE: 50.0, CLASS_INCOMPATIBLE: 50.0,
                            CLASS_UNKNOWN: 0.0, CLASS_NOT_APPLICABLE: 0.0},
            "unknown_reasons": {},
            "mismatches_by_plugin": {"pytest-xdist": 1},
            "by_source": {"swe-bench-live": {CLASS_COMPATIBLE: 1,
                                             CLASS_INCOMPATIBLE: 1,
                                             CLASS_UNKNOWN: 0,
                                             CLASS_NOT_APPLICABLE: 0}},
            "incompatible_tasks": [], "unknown_tasks": [], "not_applicable_tasks": [],
            "results": [],
            "provenance": {"registration_file": "reg", "registration_sha256": "0" * 64,
                           "corpus_sha256": {"swe-bench-live": "1" * 64},
                           "source_manifest_sha256": "2" * 64, "ledger_file": "led",
                           "ledger_sha256": "3" * 64, "tree_roots_consulted": [],
                           "builder_constants": {}},
            "limitations": ["plugin-install coverage only"],
        }
        paths = _write_report(report, tmp_path)
        payload = Path(paths["json"]).read_bytes()
        import hashlib

        assert hashlib.sha256(payload).hexdigest() == paths["json_sha256"]
        md = Path(paths["md"]).read_text(encoding="utf-8")
        assert paths["json_sha256"] in md
        assert "OBSERVED_COMPATIBLE | 1" in md

    def test_uv_all_extras_contract_is_read_from_the_builder(self):
        # the classifier must not re-declare which tasks are uv-managed
        assert set(UV_ALL_EXTRAS) <= {"openai__openai-agents-python-1633",
                                      "openai__openai-agents-python-1636"}
        assert "python__mypy-19767" in EXTRA_DEPS
