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
