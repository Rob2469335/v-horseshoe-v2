"""Deterministic Python interpreter selection for the F2-S8 builder.

The policy is "first INSTALLED interpreter, in documented PREFERENCE order, that
satisfies the project's declared ``requires-python``". Preference order matters
because wheel coverage collapses at the newest releases, and the declared bound
must never be violated.

Measured motivation: ``sdv-dev__SDV-2656`` declares
``requires-python = '>=3.8,<3.14'`` with SINGLE quotes. The builder's old regex
matched only double quotes, treated the project as unconstrained, chose CPython
3.14, and pip rejected it mid-install -- burning two tasks.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train import f2_s8_acquisition as acquisition  # noqa: E402
from qwen_train.f2_s8_acquisition import (  # noqa: E402
    AcquisitionError,
    requires_python,
    select_interpreter,
)


def _project(tmp_path: pathlib.Path, spec: str | None) -> pathlib.Path:
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    body = "[project]\nname = 'x'\n"
    if spec is not None:
        body += f"requires-python = {spec}\n"
    (proj / "pyproject.toml").write_text(body, encoding="utf-8")
    return proj


def _exe(tmp_path: pathlib.Path, name: str) -> str:
    """A real (empty) file so ``select_interpreter`` sees it as installed."""
    path = tmp_path / name / "python.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return str(path)


def _choose(proj, cands, versions):
    return select_interpreter(proj, cands, version_of=lambda p: versions[p])


class TestRequiresPythonParsing:
    def test_single_quoted_specification_is_read(self, tmp_path):
        """The exact SDV shape: valid TOML, single quotes."""
        proj = _project(tmp_path, "'>=3.8,<3.14'")
        assert requires_python(proj) == ((3, 8), (3, 14))

    def test_double_quoted_specification_is_read(self, tmp_path):
        proj = _project(tmp_path, '">=3.11"')
        assert requires_python(proj) == ((3, 11), (99, 0))

    def test_an_exact_pin_restricts_to_one_minor_series(self, tmp_path):
        proj = _project(tmp_path, "'==3.12.*'")
        assert requires_python(proj) == ((3, 12), (3, 13))

    def test_compatible_release_allows_the_major_series(self, tmp_path):
        proj = _project(tmp_path, "'~=3.11'")
        assert requires_python(proj) == ((3, 11), (4, 0))

    def test_an_unsatisfiable_specification_is_refused(self, tmp_path):
        proj = _project(tmp_path, "'>=3.14,<3.11'")
        with pytest.raises(AcquisitionError, match="unsatisfiable"):
            requires_python(proj)

    def test_an_uninterpretable_specification_is_refused_not_ignored(self, tmp_path):
        proj = _project(tmp_path, "'not-a-version'")
        with pytest.raises(AcquisitionError, match="cannot be interpreted"):
            requires_python(proj)

    def test_no_declaration_means_unconstrained(self, tmp_path):
        assert requires_python(_project(tmp_path, None)) is None


class TestSetupPyDeclaration:
    """``setup.py``-only projects must not look unconstrained.

    Measured motivation: ``django-json-api`` declares ``python_requires=">=3.9"``
    in ``setup.py`` and nowhere else, and the parser returned ``None`` -- the same
    "declared but invisible" defect that cost the SDV tasks, one source lower.
    """

    @staticmethod
    def _setup(tmp_path: pathlib.Path, body: str, *, name: str = "proj") -> pathlib.Path:
        proj = tmp_path / name
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "setup.py").write_text(body, encoding="utf-8")
        return proj

    def test_a_literal_declaration_is_read(self, tmp_path):
        proj = self._setup(tmp_path, "setup(name='x', python_requires='>=3.9')\n")
        assert requires_python(proj) == ((3, 9), (99, 0))

    def test_a_double_quoted_declaration_is_read(self, tmp_path):
        proj = self._setup(tmp_path, 'setup(name="x", python_requires=">=3.11")\n')
        assert requires_python(proj) == ((3, 11), (99, 0))

    def test_a_module_constant_formatted_into_the_specifier_is_resolved(self, tmp_path):
        """The electrum shape: python_requires='>={}'.format(CONSTANT)."""
        proj = self._setup(
            tmp_path,
            'MIN_PYTHON_VERSION = "3.10.0"\n'
            "setup(name='x', python_requires='>={}'.format(MIN_PYTHON_VERSION))\n",
        )
        assert requires_python(proj) == ((3, 10), (99, 0))

    def test_an_f_string_declaration_is_resolved(self, tmp_path):
        proj = self._setup(
            tmp_path,
            'MIN = "3.9"\nsetup(name="x", python_requires=f">={MIN}")\n',
        )
        assert requires_python(proj) == ((3, 9), (99, 0))

    def test_setup_py_code_is_never_executed(self, tmp_path):
        """A declaration that would kill the process if imported is still read."""
        proj = self._setup(
            tmp_path,
            "raise SystemExit('must not run')\n"
            "setup(name='x', python_requires='>=3.12')\n",
        )
        assert requires_python(proj) == ((3, 12), (99, 0))

    def test_a_requirement_newer_than_every_candidate_excludes_them_all(self, tmp_path):
        proj = self._setup(tmp_path, "setup(name='x', python_requires='>=3.13')\n")
        py310 = _exe(tmp_path, "py310")
        versions = {py310: (3, 10)}
        with pytest.raises(AcquisitionError, match="no available interpreter"):
            _choose(proj, [py310], versions)

    def test_no_python_requires_keyword_means_unconstrained(self, tmp_path):
        proj = self._setup(tmp_path, "setup(name='x', version='1')\n")
        assert requires_python(proj) is None

    def test_an_unparseable_setup_py_is_not_a_declaration(self, tmp_path):
        proj = self._setup(tmp_path, "def broken(:\n")
        assert requires_python(proj) is None

    def test_a_non_static_declaration_fails_closed(self, tmp_path):
        """The file says there is a constraint we cannot read: refuse, do not guess."""
        proj = self._setup(
            tmp_path,
            "import os\nsetup(name='x', python_requires=os.environ['NOPE'])\n",
        )
        with pytest.raises(AcquisitionError, match="cannot be resolved statically"):
            requires_python(proj)

    def test_the_parser_never_executes_or_imports_project_code(self):
        """Static proof that the setup.py path is AST-only, not import-based."""
        source = pathlib.Path(acquisition.__file__).read_text(
            encoding="utf-8", errors="replace")
        start = source.index("def _setup_py_python_requires")
        end = source.index("def _bounds_from_specifier")
        body = source[start:end]
        for forbidden in ("exec(", "eval(", "importlib", "runpy", "subprocess",
                          "compile("):
            assert forbidden not in body, (
                f"_setup_py_python_requires must not use {forbidden!r}")

    def test_a_malformed_declaration_is_refused_not_ignored(self, tmp_path):
        proj = self._setup(tmp_path, "setup(name='x', python_requires='not-a-version')\n")
        with pytest.raises(AcquisitionError, match="cannot be interpreted"):
            requires_python(proj)

    def test_conflicting_declarations_are_refused(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "pyproject.toml").write_text(
            "[project]\nname = 'x'\nrequires-python = '>=3.9'\n", encoding="utf-8")
        (proj / "setup.py").write_text(
            "setup(name='x', python_requires='>=3.11')\n", encoding="utf-8")
        with pytest.raises(AcquisitionError, match="conflicting requires-python"):
            requires_python(proj)

    def test_agreeing_declarations_collapse_to_one_bound(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "pyproject.toml").write_text(
            "[project]\nname = 'x'\nrequires-python = '>=3.10'\n", encoding="utf-8")
        (proj / "setup.py").write_text(
            "setup(name='x', python_requires='>=3.10')\n", encoding="utf-8")
        assert requires_python(proj) == ((3, 10), (99, 0))

    def test_setup_cfg_and_setup_py_conflict_is_refused(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "setup.cfg").write_text(
            "[options]\npython_requires = >=3.9\n", encoding="utf-8")
        (proj / "setup.py").write_text(
            "setup(name='x', python_requires='>=3.12')\n", encoding="utf-8")
        with pytest.raises(AcquisitionError, match="conflicting requires-python"):
            requires_python(proj)

    def test_two_conflicting_setup_calls_are_refused(self, tmp_path):
        proj = self._setup(
            tmp_path,
            "setup(name='a', python_requires='>=3.9')\n"
            "setup(name='b', python_requires='>=3.11')\n",
        )
        with pytest.raises(AcquisitionError, match="conflicting python_requires"):
            requires_python(proj)


class TestSelection:
    def test_prefers_the_documented_order_not_simply_the_newest(self, tmp_path):
        proj = _project(tmp_path, "'>=3.10'")
        c311 = _exe(tmp_path, "py311")
        c312 = _exe(tmp_path, "py312")
        c314 = _exe(tmp_path, "py314")
        versions = {c311: (3, 11), c312: (3, 12), c314: (3, 14)}
        # Preference order puts 3.12 ahead of 3.11 and 3.14.
        assert _choose(proj, [c312, c311, c314], versions) == c312
        assert _choose(proj, [c314, c311, c312], versions) == c314

    def test_an_upper_bound_excludes_newer_interpreters(self, tmp_path):
        proj = _project(tmp_path, "'>=3.11,<3.14'")
        c312 = _exe(tmp_path, "py312")
        c314 = _exe(tmp_path, "py314")
        versions = {c312: (3, 12), c314: (3, 14)}
        assert _choose(proj, [c314, c312], versions) == c312

    def test_a_lower_bound_excludes_older_interpreters(self, tmp_path):
        proj = _project(tmp_path, "'>=3.13'")
        c311 = _exe(tmp_path, "py311")
        c312 = _exe(tmp_path, "py312")
        versions = {c311: (3, 11), c312: (3, 12)}
        with pytest.raises(AcquisitionError, match="no available interpreter"):
            _choose(proj, [c311, c312], versions)

    def test_an_exact_constraint_selects_only_that_series(self, tmp_path):
        proj = _project(tmp_path, "'==3.12.*'")
        c312 = _exe(tmp_path, "py312")
        c311 = _exe(tmp_path, "py311")
        versions = {c312: (3, 12), c311: (3, 11)}
        assert _choose(proj, [c311, c312], versions) == c312

    def test_a_missing_executable_is_skipped(self, tmp_path):
        proj = _project(tmp_path, "'>=3.10'")
        gone = str(tmp_path / "uninstalled" / "python.exe")
        c311 = _exe(tmp_path, "py311")
        versions = {gone: (3, 11), c311: (3, 11)}
        assert _choose(proj, [gone, c311], versions) == c311

    def test_no_compatible_interpreter_fails_with_an_actionable_message(self, tmp_path):
        proj = _project(tmp_path, "'>=3.20'")
        c312 = _exe(tmp_path, "py312")
        versions = {c312: (3, 12)}
        with pytest.raises(AcquisitionError) as exc:
            _choose(proj, [c312], versions)
        assert "requires-python" in str(exc.value)

    def test_selection_is_deterministic_across_repeated_runs(self, tmp_path):
        proj = _project(tmp_path, "'>=3.10'")
        c311 = _exe(tmp_path, "py311")
        c312 = _exe(tmp_path, "py312")
        versions = {c311: (3, 11), c312: (3, 12)}
        # Determinism is about repeated runs agreeing, not about which candidate
        # wins: the PREFERENCE policy lives in the INTERPRETERS ordering, while
        # select_interpreter honours the order it is given.
        results = {_choose(proj, [c311, c312], versions) for _ in range(5)}
        assert len(results) == 1
        assert results.pop() == c311

    def test_the_selected_interpreter_is_never_outside_the_declared_range(self, tmp_path):
        """The invariant that matters: no constraint violation, ever."""
        proj = _project(tmp_path, "'>=3.11,<3.13'")
        versions = {}
        for major, minor in ((3, 10), (3, 11), (3, 12), (3, 14)):
            versions[_exe(tmp_path, f"py{major}{minor}")] = (major, minor)
        chosen = _choose(proj, list(versions), versions)
        lo, hi = requires_python(proj)
        assert lo <= versions[chosen] < hi

class TestInterpreterDiscovery:
    """Discovery must be verified, deterministic and machine-independent."""

    @staticmethod
    def _tree(tmp_path, layout):
        """Build a fake search root from {dirname: (major, minor)}."""
        root = tmp_path / "root"
        versions = {}
        for name, ver in layout.items():
            exe = root / name / "python.exe"
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"")
            versions[str(exe.resolve())] = ver
        return root, versions

    def test_orders_by_policy_not_by_directory_name(self, tmp_path):
        from qwen_train.f2_s8_acquisition import discover_interpreters
        root, versions = self._tree(tmp_path, {
            "Python314": (3, 14), "Python310": (3, 10),
            "cpython-3.13.1-windows-x86_64-none": (3, 13),
            "cpython-3.12.8-windows-x86_64-none": (3, 12),
        })
        got = discover_interpreters(version_of=lambda p: versions[p], roots=[root])
        assert [versions[p] for p in got] == [(3, 12), (3, 13), (3, 14), (3, 10)]

    def test_an_unlisted_version_is_excluded_not_appended(self, tmp_path):
        from qwen_train.f2_s8_acquisition import discover_interpreters
        root, versions = self._tree(tmp_path, {"Python315": (3, 15), "Python312": (3, 12)})
        got = discover_interpreters(version_of=lambda p: versions[p], roots=[root])
        assert [versions[p] for p in got] == [(3, 12)]

    def test_an_unrunnable_executable_is_skipped(self, tmp_path):
        from qwen_train.f2_s8_acquisition import discover_interpreters
        root, versions = self._tree(tmp_path, {"Python312": (3, 12), "Python313": (3, 13)})
        bad = next(p for p, v in versions.items() if v == (3, 13))

        def version_of(path):
            if path == bad:
                raise OSError("not executable")
            return versions[path]

        got = discover_interpreters(version_of=version_of, roots=[root])
        assert [versions[p] for p in got] == [(3, 12)]

    def test_a_reported_version_wins_over_the_directory_name(self, tmp_path):
        """A directory named Python312 that reports 3.11 is ordered as 3.11."""
        from qwen_train.f2_s8_acquisition import discover_interpreters
        root, _ = self._tree(tmp_path, {"Python312": (3, 12), "Python310": (3, 10)})
        lying = str((root / "Python312" / "python.exe").resolve())
        real = str((root / "Python310" / "python.exe").resolve())
        got = discover_interpreters(
            version_of=lambda p: (3, 11) if p == lying else (3, 10), roots=[root])
        # 3.11 ranks before 3.10 regardless of the directory it lives in.
        assert got == [lying, real]

    def test_discovery_is_deterministic_and_deduplicated(self, tmp_path):
        from qwen_train.f2_s8_acquisition import discover_interpreters
        root, versions = self._tree(tmp_path, {"Python312": (3, 12)})
        first = discover_interpreters(version_of=lambda p: versions[p],
                                      roots=[root, root])
        second = discover_interpreters(version_of=lambda p: versions[p], roots=[root])
        assert first == second and len(first) == 1

    def test_version_of_is_required(self, tmp_path):
        from qwen_train.f2_s8_acquisition import discover_interpreters
        with pytest.raises(AcquisitionError, match="version_of is required"):
            discover_interpreters(roots=[tmp_path])

    def test_an_empty_root_yields_no_candidates(self, tmp_path):
        from qwen_train.f2_s8_acquisition import discover_interpreters
        assert discover_interpreters(version_of=lambda p: (3, 12),
                                     roots=[tmp_path / "nope"]) == []


class TestDeclaredMinorReachability:
    """A declared minor the search roots cannot see is a WIRING defect.

    The F2 isolated runtimes are installed with ``TargetDir`` and are on no PATH,
    so before the S8 root was a search root, minors 3.7 / 3.8 / 3.9 discovered
    NOTHING and ten Phase-1 candidates failed closed at interpreter selection.
    """

    def test_the_isolated_s8_root_is_searched(self):
        assert (acquisition.F2_SCRATCH_INTERPRETER_ROOT
                in acquisition.INTERPRETER_SEARCH_ROOTS)

    def test_the_legacy_minors_are_selectable(self):
        for minor in ((3, 7), (3, 8), (3, 9)):
            assert minor in acquisition.PREFERRED_PYTHON_MINORS, minor

    def test_appending_the_legacy_minors_does_not_reorder_the_leaders(self):
        """Order only affects the legacy ``prefer=None`` path, so the modern
        minors must keep their leading positions unchanged."""
        leaders = acquisition.PREFERRED_PYTHON_MINORS[:5]
        assert leaders == ((3, 12), (3, 13), (3, 11), (3, 14), (3, 10))

    def test_an_absent_minor_names_itself_and_the_roots(self, tmp_path, monkeypatch):
        import qwen_train.f2_s8_pilot_build as pb
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname='x'\nrequires-python='>=3.8'\n", encoding="utf-8")
        monkeypatch.setattr(pb, "available_interpreters", lambda prefer=None: [])
        with pytest.raises(pb.DeclaredInterpreterError) as exc:
            pb.pick_interpreter(tmp_path, prefer=((3, 8),))
        message = str(exc.value)
        assert "3.8" in message
        assert "no available interpreter" in message
        assert "INTERPRETER_SEARCH_ROOTS" in message

    def test_a_discovered_minor_the_bound_rejects_is_reported_differently(
            self, tmp_path, monkeypatch):
        import qwen_train.f2_s8_pilot_build as pb
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname='x'\nrequires-python='>=3.8'\n", encoding="utf-8")
        exe = tmp_path / "python.exe"
        exe.write_bytes(b"")
        monkeypatch.setattr(pb, "available_interpreters",
                            lambda prefer=None: [str(exe)])
        monkeypatch.setattr(pb, "_version_of", lambda p: (3, 7))
        with pytest.raises(pb.DeclaredInterpreterError) as exc:
            pb.pick_interpreter(tmp_path, prefer=((3, 7),))
        message = str(exc.value)
        assert "requires-python" in message
        assert "INTERPRETER_SEARCH_ROOTS" not in message
