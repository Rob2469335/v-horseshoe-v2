"""Derive a task's frozen relevant_file_set (R8) from its reference fix (F2-IMPL-AUTH-029).

Definition (existing authority, ``qwen_train/f2_endpoint_derivation.py``):
``relevant_file_set`` = the **non-test source paths touched by the task's reference
fix commit**.  The acquisition source (SWE-bench-Live ``patch`` field) is the gold
fix in unified-diff form, whose ``diff --git a/<old> b/<new>`` headers name exactly
those paths.  Deriving R8 from them is deterministic, metadata-only and needs no
clone.

This is an **oracle-style proxy for edit scope, NOT semantic ground truth** -- the
same concession the existing Q8 methodology records.

No network, no subprocess, no filesystem access.  Pure text -> tuple.
"""
from __future__ import annotations

import re

__all__ = [
    "changed_paths_from_patch",
    "relevant_file_set_from_patch",
    "patch_test_paths",
    "is_test_path",
    "is_source_path",
]

_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+?)\s*$", re.MULTILINE)

#: A path is a TEST path if any segment is a conventional test directory or the
#: basename matches ``test_*``/``*_test.py``/``tests.py``/``conftest.py``. The F1
#: precedent (F1-OP-003) excluded the test file from the endpoint because editing a
#: test is not the behaviour F0's endpoint measures.
_TEST_DIRS = {"tests", "test", "testing", "spec", "specs"}
#: A ``spec``/``specs``/``testing`` directory only counts as a test directory when
#: it is at the repository root (JS/TS convention). Deeper occurrences such as
#: ``litserve/specs/`` are source packages, not test dirs -- treating them as tests
#: silently emptied a valid task's relevant set.
_ROOT_ONLY_TEST_DIRS = {"testing", "spec", "specs"}
_TEST_BASENAMES = re.compile(r"^(test_.*\.py|.*_test\.py|tests\.py|conftest\.py)$", re.I)

#: "Source" means source CODE. The endpoint measures a source edit (S7), so docs,
#: lockfiles and config are not source and must not become the endpoint -- and the
#: endpoint safety check refuses many of their names anyway. Unknown extensions are
#: excluded (fail-closed): an unknown file is not proven to be source code.
_SOURCE_EXTS = {
    ".py", ".pyi", ".pyx",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".go", ".java", ".kt", ".kts", ".scala", ".rs",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh",
    ".cs", ".rb", ".php", ".swift", ".m", ".mm",
    ".sh", ".bash", ".pl", ".lua", ".r", ".jl", ".ex", ".exs", ".erl", ".hs",
}


def is_source_path(path: str) -> bool:
    """True when the path's extension is a recognised source-code extension."""
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return False
    return name[dot:].lower() in _SOURCE_EXTS


def is_test_path(path: str) -> bool:
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    if not parts:
        return False
    dirs = [p.lower() for p in parts[:-1]]
    if any(d in _TEST_DIRS and d not in _ROOT_ONLY_TEST_DIRS for d in dirs):
        return True
    if dirs and dirs[0] in _ROOT_ONLY_TEST_DIRS:
        return True
    return bool(_TEST_BASENAMES.match(parts[-1]))


def changed_paths_from_patch(patch: str) -> tuple[str, ...]:
    """Every path a unified diff touches, in first-seen order, de-duplicated.

    Deletions (``b/`` is ``/dev/null``) contribute the ``a/`` path; additions
    contribute the ``b/`` path (``a/`` is ``/dev/null``).
    """
    out: list[str] = []
    seen: set[str] = set()

    for match in _DIFF_GIT.finditer(str(patch or "")):
        old, new = match.group(1).strip(), match.group(2).strip()
        path = old if new == "/dev/null" else new
        if path and path != "/dev/null" and path not in seen:
            seen.add(path)
            out.append(path)
    return tuple(out)


def patch_test_paths(patch: str) -> tuple[str, ...]:
    return tuple(p for p in changed_paths_from_patch(patch) if is_test_path(p))


def relevant_file_set_from_patch(patch: str) -> tuple[str, ...]:
    """The non-test SOURCE paths of the reference fix -- the R8 set, sorted.

    Docs/config/lockfiles are excluded because the endpoint measures a source edit
    (S7); an unrecognised extension is excluded (fail-closed).
    """
    return tuple(
        sorted(
            p
            for p in changed_paths_from_patch(patch)
            if not is_test_path(p) and is_source_path(p)
        )
    )
