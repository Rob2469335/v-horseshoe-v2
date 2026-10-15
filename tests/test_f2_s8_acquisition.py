"""Tests for the metadata-driven acquisition layer (no network, no execution).

Every function under test is pure metadata handling: it reads the corpus, the
registration order and static project metadata.  Nothing here runs a task command
or installs a package -- a PEP 517 build backend is executed code, so it belongs
to the acquisition boundary under existing authority, not to a test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwen_train.f2_s8_acquisition import (
    BUILDER_SOURCE,
    AcquisitionError,
    build_identity,
    discover_project_root,
    load_corpus_rows,
    load_registration_order,
    repo_url_for,
    requires_python,
    resolve_task,
    select_interpreter,
    select_tasks,
    static_dependency_inputs,
)

GOOD_PATCH = "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
GOOD_TEST_PATCH = "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"


def _row(**over):
    """A SWE-bench-Live-shaped raw row (its command lives in ``test_cmds``)."""
    row = {
        "instance_id": "python-babel__babel-1120",
        "repo": "python-babel/babel",
        "base_commit": "0" * 40,
        "test_cmds": ["pytest -rA", "make test"],
        "FAIL_TO_PASS": ["tests/test_pofile.py::WritePoTestCase::test_wrap"],
        "PASS_TO_PASS": ["tests/test_core.py::test_locale"],
        "patch": GOOD_PATCH,
        "test_patch": GOOD_TEST_PATCH,
        "_source": "swe-bench-live",
        "_source_digest": "a" * 64,
    }
    row.update(over)
    return row


def _rebench_row(**over):
    """A SWE-rebench-V2-shaped raw row: the command lives in ``install_config``."""
    row = {
        "instance_id": "acme__widget-7",
        "repo": "acme/widget",
        "base_commit": "1" * 40,
        "install_config": {"test_cmd": "pytest -rA tests"},
        "FAIL_TO_PASS": ["tests/test_w.py::test_one"],
        "PASS_TO_PASS": ["tests/test_w.py::test_two"],
        "patch": GOOD_PATCH,
        "test_patch": GOOD_TEST_PATCH,
        "_source": "swe-rebench-v2",
        "_source_digest": "b" * 64,
    }
    row.update(over)
    return row


def _write_corpus(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Corpus parsing and source identity
# ---------------------------------------------------------------------------
class TestCorpusLoading:
    def test_both_source_schemas_are_accepted_and_tagged(self, tmp_path):
        live = _write_corpus(tmp_path / "live.jsonl", [_row()])
        rebench = _write_corpus(tmp_path / "rebench.jsonl", [_rebench_row()])
        rows = load_corpus_rows({"swe-bench-live": live, "swe-rebench-v2": rebench})
        assert set(rows) == {"python-babel__babel-1120", "acme__widget-7"}
        assert rows["python-babel__babel-1120"]["_source"] == "swe-bench-live"
        assert rows["acme__widget-7"]["_source"] == "swe-rebench-v2"
        # provenance: each row carries the digest of the file it came from
        assert len(rows["python-babel__babel-1120"]["_source_digest"]) == 64
        assert (rows["python-babel__babel-1120"]["_source_digest"]
                != rows["acme__widget-7"]["_source_digest"])

    def test_the_rebench_command_is_read_from_install_config(self):
        """The sources declare the command in DIFFERENT places; one adapter, one meaning."""
        t = resolve_task(_rebench_row())
        assert t.test_cmd == "pytest -rA tests"
        assert t.source == "swe-rebench-v2"

    def test_a_rebench_row_without_install_config_is_refused(self):
        with pytest.raises(AcquisitionError, match="not driveable"):
            resolve_task(_rebench_row(install_config={}))
        with pytest.raises(AcquisitionError, match="not driveable"):
            resolve_task(_rebench_row(install_config=None))

    def test_a_live_row_reads_its_command_from_test_cmds(self):
        t = resolve_task(_row())
        assert "pytest" in t.test_cmd

    def test_a_record_without_instance_id_is_refused(self, tmp_path):
        bad = _write_corpus(tmp_path / "x.jsonl", [_row(instance_id="")])
        with pytest.raises(AcquisitionError, match="instance_id"):
            load_corpus_rows({"swe-bench-live": bad})

    def test_malformed_jsonl_is_refused_not_skipped(self, tmp_path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(Exception):
            load_corpus_rows({"swe-bench-live": bad})

    def test_the_corpus_digest_is_streamed_not_read_whole(self, tmp_path, monkeypatch):
        """A multi-GB corpus must not be materialised just to hash it."""
        import hashlib as _hashlib

        live = _write_corpus(tmp_path / "live.jsonl", [_row()])
        expected = _hashlib.sha256(live.read_bytes()).hexdigest()

        def boom(self):  # pragma: no cover - only reached on regression
            raise AssertionError("whole-file read on the corpus")

        monkeypatch.setattr(Path, "read_bytes", boom)
        rows = load_corpus_rows({"swe-bench-live": live})
        assert rows["python-babel__babel-1120"]["_source_digest"] == expected

    def test_identical_duplicates_across_sources_are_accepted(self, tmp_path):
        """The same task in both corpora, each in its own schema."""
        from qwen_train.f2_population_build import source_to_screen_row

        base = _row()
        live_cmd = source_to_screen_row("swe-bench-live", base)["test_cmd"]
        a = _write_corpus(tmp_path / "a.jsonl", [base])
        b = _write_corpus(tmp_path / "b.jsonl", [_rebench_row(
            instance_id=base["instance_id"], repo=base["repo"],
            base_commit=base["base_commit"], FAIL_TO_PASS=base["FAIL_TO_PASS"],
            PASS_TO_PASS=base["PASS_TO_PASS"], patch=base["patch"],
            install_config={"test_cmd": live_cmd})])
        rows = load_corpus_rows({"swe-bench-live": a, "swe-rebench-v2": b})
        assert list(rows) == ["python-babel__babel-1120"]

    def test_disagreeing_duplicates_across_sources_are_refused(self, tmp_path):
        a = _write_corpus(tmp_path / "a.jsonl", [_row()])
        b = _write_corpus(tmp_path / "b.jsonl", [_rebench_row(
            instance_id="python-babel__babel-1120", repo="python-babel/babel",
            install_config={"test_cmd": "pytest -rA"})])
        with pytest.raises(AcquisitionError, match="DISAGREEING|DIFFERENT"):
            load_corpus_rows({"swe-bench-live": a, "swe-rebench-v2": b})

    def test_the_conflict_scan_names_the_disagreeing_ids(self, tmp_path):
        from qwen_train.f2_s8_acquisition import scan_cross_source_conflicts

        base = _row()
        b = _write_corpus(tmp_path / "b.jsonl", [_rebench_row(
            instance_id=base["instance_id"], repo=base["repo"],
            base_commit="9" * 40, install_config={"test_cmd": "pytest -rA"})])
        a = _write_corpus(tmp_path / "a.jsonl", [base])
        conflicts = scan_cross_source_conflicts(
            {"swe-bench-live": a, "swe-rebench-v2": b})
        assert list(conflicts) == ["python-babel__babel-1120"]
        assert "disagree" in conflicts["python-babel__babel-1120"]

    def test_the_conflict_scan_is_empty_for_agreeing_duplicates(self, tmp_path):
        from qwen_train.f2_s8_acquisition import scan_cross_source_conflicts
        from qwen_train.f2_population_build import source_to_screen_row

        base = _row()
        live_cmd = source_to_screen_row("swe-bench-live", base)["test_cmd"]
        a = _write_corpus(tmp_path / "a.jsonl", [base])
        b = _write_corpus(tmp_path / "b.jsonl", [_rebench_row(
            instance_id=base["instance_id"], repo=base["repo"],
            base_commit=base["base_commit"], FAIL_TO_PASS=base["FAIL_TO_PASS"],
            PASS_TO_PASS=base["PASS_TO_PASS"], patch=base["patch"],
            install_config={"test_cmd": live_cmd})])
        assert scan_cross_source_conflicts(
            {"swe-bench-live": a, "swe-rebench-v2": b}) == {}

    def test_unknown_source_key_is_refused(self, tmp_path):
        a = _write_corpus(tmp_path / "a.jsonl", [_row()])
        with pytest.raises(AcquisitionError, match="unknown source"):
            load_corpus_rows({"not-a-source": a})

    def test_missing_corpus_file_is_refused(self, tmp_path):
        with pytest.raises(AcquisitionError, match="missing"):
            load_corpus_rows({"swe-bench-live": tmp_path / "absent.jsonl"})


class TestRegistrationOrder:
    def test_order_is_read_and_must_be_unique(self, tmp_path):
        p = tmp_path / "ordered.json"
        p.write_text(json.dumps([{"instance_id": "a"}, {"instance_id": "b"}]),
                     encoding="utf-8")
        assert load_registration_order(p) == ["a", "b"]
        p.write_text(json.dumps([{"instance_id": "a"}, {"instance_id": "a"}]),
                     encoding="utf-8")
        with pytest.raises(AcquisitionError, match="duplicate"):
            load_registration_order(p)


# ---------------------------------------------------------------------------
# Task resolution
# ---------------------------------------------------------------------------
class TestResolveTask:
    def test_a_good_row_resolves_with_patch_digests(self):
        row = _row()
        t = resolve_task(row)
        # the canonical command is the repository adapter's, not a second opinion
        from qwen_train.f2_population_build import source_to_screen_row

        assert t.test_cmd == source_to_screen_row("swe-bench-live", row)["test_cmd"]
        assert t.base_commit == "0" * 40
        assert t.gold_patch_sha256 != t.test_patch_sha256
        assert len(t.identity) == 64

    def test_a_non_immutable_commit_is_refused(self):
        with pytest.raises(AcquisitionError, match="immutable"):
            resolve_task(_row(base_commit="main"))

    def test_ineligible_node_ids_are_refused(self):
        with pytest.raises(AcquisitionError, match="not eligible"):
            resolve_task(_row(FAIL_TO_PASS=["some-log-parser-identifier"]))

    def test_a_missing_patch_is_refused(self):
        with pytest.raises(AcquisitionError, match="no gold patch"):
            resolve_task(_row(patch=""))
        with pytest.raises(AcquisitionError, match="no test patch"):
            resolve_task(_row(test_patch=""))

    def test_an_unknown_registration_is_refused(self):
        with pytest.raises(AcquisitionError, match="unknown registration"):
            resolve_task(_row(), registration_id="something_else_v9")

    def test_a_bad_repository_identity_is_refused(self):
        with pytest.raises(AcquisitionError, match="owner/name"):
            repo_url_for("no-slash-here")
        with pytest.raises(AcquisitionError, match="owner/name"):
            repo_url_for("a/b/c")

    def test_repo_url_is_the_canonical_clone_url(self):
        assert repo_url_for("python-babel/babel") == "https://github.com/python-babel/babel.git"


class TestSelectTasks:
    def _rows(self, *ids):
        return {k: _row(instance_id=k) for k in ids}

    def test_order_comes_from_the_registration_not_the_corpus(self):
        picked = select_tasks(["b", "a"], self._rows("a", "b"), limit=2)
        assert [t.instance_id for t in picked] == ["b", "a"]

    def test_excluded_tasks_are_skipped_and_the_next_are_taken(self):
        picked = select_tasks(["a", "b", "c"], self._rows("a", "b", "c"),
                              limit=2, exclude={"a"})
        assert [t.instance_id for t in picked] == ["b", "c"]

    def test_a_registered_task_absent_from_the_corpus_is_a_hard_error(self):
        with pytest.raises(AcquisitionError, match="absent from the corpus"):
            select_tasks(["a", "missing"], self._rows("a"), limit=2)

    def test_limit_must_be_positive(self):
        with pytest.raises(AcquisitionError, match="positive"):
            select_tasks(["a"], {}, limit=0)


# ---------------------------------------------------------------------------
# Static metadata inspection
# ---------------------------------------------------------------------------
class TestProjectDiscovery:
    def test_root_pyproject_is_found(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        assert discover_project_root(tmp_path) == tmp_path

    def test_a_single_nested_project_is_found(self, tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        assert discover_project_root(tmp_path) == sub

    def test_ambiguity_is_refused(self, tmp_path):
        for name in ("one", "two"):
            d = tmp_path / name
            d.mkdir()
            (d / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        with pytest.raises(AcquisitionError, match="ambiguous"):
            discover_project_root(tmp_path)

    def test_no_metadata_is_refused(self, tmp_path):
        with pytest.raises(AcquisitionError, match="no project metadata"):
            discover_project_root(tmp_path)


class TestInterpreterSelection:
    def _py(self, tmp_path, name, text):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_requires_python_bounds_are_read_statically(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="x"\nrequires-python = ">=3.10,<3.14"\n', encoding="utf-8")
        assert requires_python(tmp_path) == ((3, 10), (3, 14))

    def test_selection_respects_the_declared_upper_bound(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="x"\nrequires-python = ">=3.10,<3.14"\n', encoding="utf-8")
        py314 = self._py(tmp_path, "python314.exe", "")
        py310 = self._py(tmp_path, "python310.exe", "")
        versions = {str(py314): (3, 14), str(py310): (3, 10)}
        assert select_interpreter(tmp_path, [py314, py310],
                                  version_of=lambda p: versions[p]) == str(py310)

    def test_no_satisfying_interpreter_is_refused(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="x"\nrequires-python = ">=3.10,<3.11"\n', encoding="utf-8")
        py314 = self._py(tmp_path, "python314.exe", "")
        with pytest.raises(AcquisitionError, match="no available interpreter"):
            select_interpreter(tmp_path, [py314], version_of=lambda p: (3, 14))

    def test_an_unversioned_project_takes_the_first_existing_candidate(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n', encoding="utf-8")
        py = self._py(tmp_path, "python.exe", "")
        assert select_interpreter(tmp_path, [py]) == str(py)


class TestStaticDependencyInputs:
    def test_declared_inputs_are_read_without_execution(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["hatchling"]\n'
            '[project]\nname="x"\n'
            '[project.optional-dependencies]\ntest = ["pytest", "pytest-xdist"]\n'
            '[dependency-groups]\ndev = ["ruff"]\n',
            encoding="utf-8")
        (tmp_path / "requirements-dev.txt").write_text(
            "# comment\npytest==8.3.5\n-r other.txt\n", encoding="utf-8")
        got = static_dependency_inputs(tmp_path)
        assert got["build_requires"] == ["hatchling"]
        assert got["extras"]["test"] == ["pytest", "pytest-xdist"]
        assert got["groups"]["dev"] == ["ruff"]
        assert got["requirements_names"] == ["pytest"]

    def test_requirements_names_keep_their_pep_508_forms_split_off(self):
        """The ONE shared requirements reader: comments stripped, option lines
        skipped, and the NAME survives specifiers, extras, markers and direct
        references -- semantics unchanged, now shared with the classifier."""
        from qwen_train.f2_s8_acquisition import parse_requirements_names

        text = ("# comment\n"
                "-r other.txt\n"
                "-e ./libs/local\n"
                "pytest-django>=4.9.0\n"
                "pytest @ https://example.invalid/pytest-9-py3-none-any.whl\n"
                "pytest-cov[testing]>=5; python_version<'3.13'\n")
        assert parse_requirements_names(text) == [
            "pytest-django", "pytest", "pytest-cov"]


# ---------------------------------------------------------------------------
# Build identity
# ---------------------------------------------------------------------------
class TestBuildIdentity:
    def _parts(self):
        row = _row()
        row["_source"] = "swe-bench-live"
        row["_source_digest"] = "a" * 64
        return resolve_task(row)

    def test_identity_is_stable_for_identical_inputs(self, tmp_path):
        t = self._parts()
        deps = {"build_requires": ["hatchling"], "extras": {}, "groups": {},
                "requirements_names": []}
        a = build_identity(t, project_root="C:/w/repo", interpreter="py314",
                           dependency_inputs=deps, builder_digest="d" * 64)
        b = build_identity(t, project_root="C:/w/repo", interpreter="py314",
                           dependency_inputs=deps, builder_digest="d" * 64)
        assert a == b

    def test_a_changed_patch_changes_the_identity(self, tmp_path):
        t = self._parts()
        deps = {"build_requires": [], "extras": {}, "groups": {}, "requirements_names": []}
        base = build_identity(t, project_root="p", interpreter="i",
                              dependency_inputs=deps, builder_digest="d" * 64)
        row2 = _row(patch=GOOD_PATCH + "+extra\n")
        row2["_source"], row2["_source_digest"] = "swe-bench-live", "a" * 64
        t2 = resolve_task(row2)
        assert build_identity(t2, project_root="p", interpreter="i",
                              dependency_inputs=deps, builder_digest="d" * 64) != base

    def test_a_changed_interpreter_or_deps_or_builder_changes_the_identity(self):
        t = self._parts()
        deps = {"build_requires": [], "extras": {}, "groups": {}, "requirements_names": []}
        base = build_identity(t, project_root="p", interpreter="i",
                              dependency_inputs=deps, builder_digest="d" * 64)
        assert build_identity(t, project_root="p", interpreter="OTHER",
                              dependency_inputs=deps, builder_digest="d" * 64) != base
        assert build_identity(t, project_root="p", interpreter="i",
                              dependency_inputs={**deps, "extras": {"test": ["pytest"]}},
                              builder_digest="d" * 64) != base
        assert build_identity(t, project_root="p", interpreter="i",
                              dependency_inputs=deps, builder_digest="e" * 64) != base

    def test_the_builder_identity_is_part_of_the_payload(self):
        assert "f2_s8_acquisition" in BUILDER_SOURCE


class TestIntegrationSeam:
    """The pure half of the builder integration: patches on disk + tree identity."""

    def _task(self, **over):
        row = _row(**over)
        row["_source"], row["_source_digest"] = "swe-bench-live", "a" * 64
        return resolve_task(row)

    def test_materialising_writes_both_patches_and_verifies_their_digests(self, tmp_path):
        from qwen_train.f2_s8_acquisition import materialise_task_inputs

        task = self._task()
        info = materialise_task_inputs(task, root=tmp_path / "patches")
        gold = Path(info["gold_patch"])
        test = Path(info["test_patch"])
        assert gold.read_text(encoding="utf-8") == task.gold_patch
        assert test.read_text(encoding="utf-8") == task.test_patch
        import hashlib

        assert hashlib.sha256(gold.read_bytes()).hexdigest() == task.gold_patch_sha256
        assert info["task_identity"] == task.identity
        assert info["repo_url"].startswith("https://github.com/")

    def test_a_materialised_patch_that_is_corrupted_is_refused(self, tmp_path):
        from qwen_train.f2_s8_acquisition import materialise_task_inputs

        task = self._task()
        broken = type(task)(**{**task.__dict__, "gold_patch_sha256": "0" * 64})
        with pytest.raises(AcquisitionError, match="gold patch digest mismatch"):
            materialise_task_inputs(broken, root=tmp_path / "p")

    def test_tree_identity_includes_the_discovered_root_and_deps(self, tmp_path):
        from qwen_train.f2_s8_acquisition import (
            build_identity_for_tree,
            static_dependency_inputs,
        )

        tree = tmp_path / "repo"
        tree.mkdir()
        (tree / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["hatchling"]\n[project]\nname="x"\n'
            '[project.optional-dependencies]\ntest = ["pytest"]\n', encoding="utf-8")
        task = self._task()
        first = build_identity_for_tree(task, tree, interpreter="py314", builder_digest="d" * 64)
        # same inputs -> same identity
        assert first == build_identity_for_tree(task, tree, interpreter="py314",
                                                builder_digest="d" * 64)
        # a changed declared dependency -> a different identity (no stale reuse)
        (tree / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["hatchling"]\n[project]\nname="x"\n'
            '[project.optional-dependencies]\ntest = ["pytest", "pytest-xdist"]\n',
            encoding="utf-8")
        assert build_identity_for_tree(task, tree, interpreter="py314",
                                       builder_digest="d" * 64) != first
        assert static_dependency_inputs(tree)["extras"]["test"] == ["pytest", "pytest-xdist"]


class TestDeclaredTestPlugins:
    """REGRESSION (Session 40b): a project's own declared pytest plugins must be
    installed, or collection fails and every declared node is absent.

    Proven live: django-guardian declares pytest-django in [dependency-groups] dev
    and sets DJANGO_SETTINGS_MODULE in [tool.pytest.ini_options]; the built venv had
    django installed but NOT pytest_django, so collection raised
    ImproperlyConfigured("settings are not configured"), the JUnit held 15/16
    all-<error> entries and zero from the declared module, and the pair was
    unresolved.
    """

    def test_only_the_declared_pytest_plugins_are_selected(self):
        from qwen_train.f2_s8_acquisition import declared_test_plugins

        deps = {
            "build_requires": ["setuptools>=61"],
            "groups": {"dev": ["pytest-django>=4.9.0", "pytest-cov>=5.0.0",
                               "mkdocs>=1.6.1", "ruff>=0.12.0",
                               {"include-group": "dev"}],
                       "ci": ["psycopg2-binary>=2.9.10"]},
            "extras": {"test": ["pytest-xdist"]},
            "requirements_names": ["django"],
        }
        # pip contract (``pip install -e .``): NO optional extra is selected, so
        # a plugin declared only in an extra is not part of this install.  This
        # corrects the Session 41 rule, which swept every extra in by default.
        assert declared_test_plugins(deps) == ["pytest-cov", "pytest-django"]
        # uv contract (``uv sync --all-extras``): extras ARE selected.
        assert declared_test_plugins(deps, include_extras=True) == [
            "pytest-cov", "pytest-django", "pytest-xdist"]
        # Non-plugins declared alongside (mkdocs, ruff, psycopg2-binary) and the
        # build system's own requirements are never collected as plugins.
        selected = declared_test_plugins(deps, include_extras=True)
        assert not {"mkdocs", "ruff", "psycopg2-binary", "setuptools"} & set(selected)

    def test_a_project_without_declared_plugins_selects_nothing(self):
        from qwen_train.f2_s8_acquisition import declared_test_plugins

        deps = {"build_requires": [], "groups": {}, "extras": {},
                "requirements_names": ["pytest"]}
        assert declared_test_plugins(deps) == []

    def test_the_selection_comes_from_the_projects_own_metadata(self, tmp_path):
        from qwen_train.f2_s8_acquisition import (
            declared_test_plugins,
            static_dependency_inputs,
        )

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="x"\n'
            '[dependency-groups]\ndev = ["pytest-django>=4.9", "mkdocs>=1.6"]\n',
            encoding="utf-8")
        assert declared_test_plugins(static_dependency_inputs(tmp_path)) == ["pytest-django"]
