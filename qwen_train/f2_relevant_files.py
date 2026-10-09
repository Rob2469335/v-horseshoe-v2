"""Derive a task's frozen relevant_file_set (R8) from its reference fix.

F2-IMPL-AUTH-031.  The frozen rule lives in
:mod:`qwen_train.f2_endpoint_derivation`: ``relevant_file_set`` is the set of
paths the task's reference fix touches **except conventional test paths** --
the same test/source partition SWE-bench uses to split ``patch`` from
``test_patch``.  There is **no file-extension allowlist**, and this module
imports the frozen predicate rather than re-implementing it, so the derivation
cannot drift from the planted rule.

The acquisition sources ship the reference fix as the gold ``patch`` (a unified
diff whose ``diff --git a/<old> b/<new>`` headers name exactly the paths the
commit changed), so the set is derived **deterministically from the source
artifact** -- no repository clone, no gold *content* read.

No network, no subprocess, no filesystem access.  Pure text -> tuple.
"""
from __future__ import annotations

import re

from qwen_train.f2_endpoint_derivation import is_test_path

__all__ = [
    "changed_paths_from_patch",
    "relevant_file_set_from_patch",
    "patch_test_paths",
    "is_test_path",
]

_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+?)\s*$", re.MULTILINE)


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
    """The test paths the reference fix touches (frozen test/source split)."""
    return tuple(p for p in changed_paths_from_patch(patch) if is_test_path(p))


def relevant_file_set_from_patch(patch: str) -> tuple[str, ...]:
    """The frozen R8 set: non-test paths of the reference fix, sorted for hashing."""
    return tuple(sorted(p for p in changed_paths_from_patch(patch) if not is_test_path(p)))
