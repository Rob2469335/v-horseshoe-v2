"""Import ``qwen_train.f2_s8_winjob`` before any Popen mock can exist.

``f2_s8_winjob`` captures the genuine ``subprocess.Popen`` at module import:

    _REAL_POPEN = subprocess.Popen   # qwen_train/f2_s8_winjob.py:32

and then uses it to spawn the child it must actually contain. If the module's
**first** import happens while ``subprocess.Popen`` is patched - the suite's
autouse ``global_subprocess_mock`` in ``tests/conftest.py``, reached through the
lazy ``from qwen_train.f2_s8_winjob import run_in_job`` at
``qwen_train/f2_s8_build_executor.py:168`` or
``qwen_train/f2_s8_pilot_build.py:631`` - then ``_REAL_POPEN`` is a MagicMock
and every containment test silently exercises the mock: ``run_in_job`` returns
the mock's ``(returncode=0, stdout=b"")``, ``Popen``/``Popen(...).pid`` calls
raise ``TypeError``, and the timeout tests assert against a fake.

PROVEN mechanism (2026-11-01): with ``Popen`` patched,
``import qwen_train.f2_s8_winjob`` gives ``_REAL_POPEN is <MagicMock>`` and
``run_in_job([... "-c", "print('hello')"])`` returns ``stdout=b''``; with the
capture poisoned, 6 of the 15 tests in ``test_f2_s8_winjob.py`` fail
(``test_run_in_job_returns_output``, ``test_timeout_raises_and_kills_grandchild``,
``test_timeout_reports_a_confirmed_job_or_fallback_outcome``,
``test_assignment_failure_uses_and_reports_the_fallback``,
``test_failed_cleanup_is_reported_as_failure``,
``test_timeout_does_not_wait_for_a_pipe_holding_descendant``).

Importing this module is therefore a *test-file* responsibility: any test module
that drives the builder's lazy import must import ``qwen_train.f2_s8_winjob`` at
module scope, because collection imports every test module before the first
fixture runs. ``REAL_POPEN`` below is the genuine class for the same reason, and
``assert_capture_is_genuine()`` makes a poisoned capture a loud failure instead
of a silent pass.

This module changes no production code: ``f2_s8_winjob``'s capture is correct
by construction, and the ordering is the tests' to get right.
"""

from __future__ import annotations

import subprocess
import unittest.mock

from qwen_train import f2_s8_winjob as WINJOB

#: The genuine class, captured while this module is imported at collection time.
REAL_POPEN = subprocess.Popen


def assert_capture_is_genuine() -> None:
    """Fail if ``f2_s8_winjob`` captured a mock instead of the real Popen."""
    captured = WINJOB._REAL_POPEN
    if isinstance(captured, unittest.mock.NonCallableMock):
        raise AssertionError(
            "qwen_train.f2_s8_winjob was first imported while subprocess.Popen "
            "was mocked, so its containment capture is a MagicMock; import "
            "tests.f2_winjob_support (module scope) in the test module that "
            "reached the builder's lazy import"
        )
    if captured is not REAL_POPEN:
        raise AssertionError(
            f"qwen_train.f2_s8_winjob captured {captured!r}, not the genuine "
            f"subprocess.Popen {REAL_POPEN!r}"
        )
