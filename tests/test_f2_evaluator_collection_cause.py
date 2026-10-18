"""D1 (owner-approved): cause-sensitive collection-error classification.

Pre-D1 the rule was: collection errors present AND a declared node missing =>
inconclusive, with NO cause analysis.  That is wrong for the standard SWE-bench
shape, where the test file -- applied to BOTH sides -- imports a symbol that only
the GOLD PATCH adds.  The base side then cannot even collect, which is a
LEGITIMATE base failure, yet the pair was written off as infrastructure and the
admission was lost.

D1 decides the cause from the GOLD PATCH'S OWN CHANGED-FILE LIST, not from the
wording of the error.  A missing module that the patch owns => the base-side error
is consistent with a genuine base failure => scored normally.  Anything else
(environment, or unprovable) => INCONCLUSIVE, fail closed.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_evaluator import (
    RESULT_ERROR,
    changed_module_paths,
    classify_collection_cause,
    collection_error_texts,
    evaluate_execution,
)

F2P = ["pkg/tests/test_mod.py::test_target"]
_TARGET = "pkg.tests.test_mod"


def _junit(*cases: str) -> bytes:
    return ('<testsuites><testsuite name="pytest">' + "".join(cases)
            + "</testsuite></testsuites>").encode()


def _ce(module: str, exc: str) -> str:
    return (f'<testcase classname="" name="{module}"><error message="collection failure">'
            f"ImportError while importing test module {module}.\nE   {exc}</error></testcase>")


def _fail() -> str:
    return f'<testcase classname="{_TARGET}" name="test_target"><failure message="a"/></testcase>'


def _ok() -> str:
    return f'<testcase classname="{_TARGET}" name="test_target"/>'


def _skip(reason: str) -> str:
    return f'<testcase classname="{_TARGET}" name="test_target"><skipped message="{reason}"/></testcase>'


PATCH_SYMBOL = "diff --git a/pkg/api.py b/pkg/api.py\n+++ b/pkg/api.py\n"
PATCH_NEWMOD = "diff --git a/pkg/newmod.py b/pkg/newmod.py\n+++ b/pkg/newmod.py\n"
PATCH_SRC = "diff --git a/src/fastmcp/elicitation.py b/src/fastmcp/elicitation.py\n+++ b/src/fastmcp/elicitation.py\n"


def _pair(base_bytes, gold_bytes, patch):
    mods = changed_module_paths(patch)
    b = evaluate_execution(base_bytes, fail_to_pass=F2P, changed_modules=mods)
    g = evaluate_execution(gold_bytes, fail_to_pass=F2P, changed_modules=mods)
    if b.declared_result == "error" or g.declared_result == "error":
        return "INFRA", b, g
    if b.declared_result == "fail" and g.declared_result == "pass":
        return "ADMIT", b, g
    return ("base_pass" if b.declared_result == "pass" else "gold_fail"), b, g


class TestPatchParsing:
    def test_extracts_changed_module_paths(self):
        assert changed_module_paths(PATCH_SYMBOL) == ("pkg.api",)

    def test_strips_a_layout_wrapper_and_keeps_both_forms(self):
        assert set(changed_module_paths(PATCH_SRC)) == {"src.fastmcp.elicitation",
                                                       "fastmcp.elicitation"}

    def test_ignores_dev_null_and_empty(self):
        assert changed_module_paths("+++ /dev/null\n") == ()
        assert changed_module_paths(None) == ()
        assert changed_module_paths("") == ()

    def test_never_invents_a_module_from_prose(self):
        """Only ``+++ b/`` diff headers count, never a sentence in a message."""
        assert changed_module_paths("the patch adds pkg/newmod.py somewhere") == ()


class TestCauseDiscrimination:
    def test_project_symbol_error_is_owned_by_the_patch(self):
        assert classify_collection_cause(
            ["ImportError: cannot import name 'new_api' from 'pkg'"],
            changed_modules=changed_module_paths(PATCH_SYMBOL)) == "project"

    def test_a_whole_new_module_added_by_the_patch_is_owned_by_it(self):
        """`No module named 'pkg.newmod'` must NOT read as a missing package."""
        assert classify_collection_cause(
            ["ModuleNotFoundError: No module named 'pkg.newmod'"],
            changed_modules=changed_module_paths(PATCH_NEWMOD)) == "project"

    def test_a_third_party_package_is_the_environment(self):
        assert classify_collection_cause(
            ["ModuleNotFoundError: No module named 'fastapi'"],
            changed_modules=changed_module_paths(PATCH_SRC)) == "environment"

    def test_no_patch_evidence_is_unknown_never_project(self):
        assert classify_collection_cause(
            ["ModuleNotFoundError: No module named 'pkg.newmod'"], changed_modules=()) == "unknown"

    def test_a_message_naming_nothing_is_unknown(self):
        assert classify_collection_cause(["ImportError while importing test module"],
                                         changed_modules=("pkg.api",)) == "unknown"

    def test_mixed_causes_resolve_to_environment(self):
        """One genuinely missing third-party dep is enough to be the environment."""
        errs = ["ModuleNotFoundError: No module named 'pkg.newmod'",
                "ModuleNotFoundError: No module named 'fastapi'"]
        assert classify_collection_cause(
            errs, changed_modules=changed_module_paths(PATCH_NEWMOD)) == "environment"

    def test_a_lookalike_name_is_not_assumed_project_owned(self):
        """`fastapi` must not match `src/fastmcp/...` just by resembling it."""
        assert classify_collection_cause(
            ["ModuleNotFoundError: No module named 'fastapi'"],
            changed_modules=changed_module_paths(PATCH_SRC)) == "environment"


class TestFixtures:
    @pytest.mark.parametrize("name,base,gold,patch,expected", [
        ("A gold adds a project symbol; base cannot import it",
         _junit(_ce(_TARGET, "ImportError: cannot import name 'new_api' from 'pkg'")),
         _junit(_ok()), PATCH_SYMBOL, "ADMIT"),
        ("H gold adds a WHOLE NEW MODULE",
         _junit(_ce(_TARGET, "ModuleNotFoundError: No module named 'pkg.newmod'")),
         _junit(_ok()), PATCH_NEWMOD, "ADMIT"),
        ("B third-party dep missing on base",
         _junit(_ce(_TARGET, "ModuleNotFoundError: No module named 'fastapi'")),
         _junit(_ok()), PATCH_SYMBOL, "INFRA"),
        ("C third-party dep missing on both",
         _junit(_ce(_TARGET, "ModuleNotFoundError: No module named 'mock'")),
         _junit(_ce(_TARGET, "ModuleNotFoundError: No module named 'mock'")), PATCH_SYMBOL, "INFRA"),
        ("D unrelated collection error while the target genuinely fails",
         _junit(_fail(), _ce("pkg.tests.test_other", "ModuleNotFoundError: No module named 'zzz'")),
         _junit(_fail()), PATCH_SYMBOL, "gold_fail"),
        ("E genuine failure both sides", _junit(_fail()), _junit(_fail()), None, "gold_fail"),
        ("F genuine base fail / gold pass", _junit(_fail()), _junit(_ok()), None, "ADMIT"),
        ("G target skipped, no collection errors",
         _junit(_skip("no numpy")), _junit(_skip("no numpy")), None, "gold_fail"),
        ("I unknown cause (no patch evidence)",
         _junit(_ce(_TARGET, "ModuleNotFoundError: No module named 'pkg.newmod'")),
         _junit(_ok()), None, "INFRA"),
    ])
    def test_fixture_pair(self, name, base, gold, patch, expected):
        got, b, g = _pair(base, gold, patch)
        assert got == expected, f"{name}: got {got} (base={b.declared_result}, gold={g.declared_result})"

    def test_collection_error_texts_exposes_the_bodies(self):
        bodies = collection_error_texts(_junit(_ce(_TARGET, "ModuleNotFoundError: No module named 'pkg.newmod'")))
        assert len(bodies) == 1 and "pkg.newmod" in bodies[0]


class TestFailClosed:
    def test_unknown_cause_never_becomes_a_pass(self):
        out = evaluate_execution(
            _junit(_ce(_TARGET, "ModuleNotFoundError: No module named 'pkg.newmod'")),
            fail_to_pass=F2P)
        assert out.declared_result == RESULT_ERROR

    def test_malformed_patch_metadata_yields_unknown(self):
        assert changed_module_paths("+++ b/\n+++ b/   \n") == ()

    def test_existing_behaviour_without_the_new_context_is_unchanged(self):
        out = evaluate_execution(_junit(_fail()), fail_to_pass=F2P)
        assert out.declared_result == "fail"
        out = evaluate_execution(_junit(_ok()), fail_to_pass=F2P)
        assert out.declared_result == "pass"
