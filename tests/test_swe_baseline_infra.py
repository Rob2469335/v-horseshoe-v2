"""SWE baseline: INFRA failures are excluded from the capability measurement.

The 2026-09-15 lesson this file exists to pin: a 14-task batch recorded 12
"f2p: 0/N passed" rows and 2 "cli_error" rows as if they were capability
results. The truth was a write-root misconfiguration plus a cold backend — the
CLI was never allowed to act. A measurement system that cannot separate
INFRASTRUCTURE from CAPABILITY will happily train the next system to fix a
problem that did not exist.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1] / "qwen_train"
_MOD = _HERE / "cli_baseline_swe.py"
_RC_MOD = _HERE / "run_curriculum.py"

_spec = importlib.util.spec_from_file_location("cli_baseline_swe_infra", _MOD)
cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cb)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_timeout_is_an_infra_failure():
    # A timed-out run never attempted the task. The 2026-09-15 600s hangs were
    # each the FIRST run after a backend restart; the warm run took 227s.
    assert cb._is_infra_failure(True, False, []) is True
    # ...even when the partial output looks like a normal CLI result.
    assert cb._is_infra_failure(True, True, ["filesystem"]) is True


def test_cli_error_with_no_tool_activity_is_infra():
    # Never got past the gate — nothing was attempted.
    assert cb._is_infra_failure(False, False, []) is True


def test_cli_error_after_acting_is_a_capability_failure():
    # It used tools, so it DID attempt the task — that is a real datapoint.
    assert cb._is_infra_failure(False, False, ["filesystem"]) is False


def test_successful_run_is_never_infra():
    assert cb._is_infra_failure(False, True, ["filesystem"]) is False


# --------------------------------------------------------------------------
# Summary excludes infra from the rate
# --------------------------------------------------------------------------


def test_summary_excludes_infra_from_the_pass_rate():
    rows = [
        {"first_attempt_success": True, "infra_failure": False},
        {"first_attempt_success": False, "infra_failure": False},
        {"first_attempt_success": False, "infra_failure": True},
    ]
    s = cb._summarize(rows)
    assert s["total"] == 3
    assert s["infra_failures_excluded"] == 1
    assert s["measured"] == 2
    assert s["solved"] == 1
    assert s["capability_pass_rate"] == 0.5  # NOT 1/3


def test_summary_rederives_for_legacy_rows():
    # Rows written before the flag existed must not be silently counted as
    # capability failures (the 14-task batch is exactly such a file).
    rows = [
        {
            "first_attempt_success": False,
            "timed_out": True,
            "cli_ok": False,
            "first_tool_order": [],
        },
        {
            "first_attempt_success": True,
            "timed_out": False,
            "cli_ok": True,
            "first_tool_order": ["filesystem"],
        },
    ]
    s = cb._summarize(rows)
    assert s["infra_failures_excluded"] == 1
    assert s["measured"] == 1
    assert s["capability_pass_rate"] == 1.0


def test_summary_all_infra_has_no_rate():
    s = cb._summarize([{"infra_failure": True, "first_attempt_success": False}])
    assert s["measured"] == 0
    assert s["capability_pass_rate"] is None


# --------------------------------------------------------------------------
# The timeout trail
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Shadowing metadata stubs (the twine false negative)
# --------------------------------------------------------------------------


class _Rc:
    def __init__(self, code):
        self.returncode = code


def test_clean_shadowing_metadata_removes_untracked_dist_info(tmp_path, monkeypatch):
    """A test-created `*.dist-info` stub in the repo root must be removed.

    Measured 2026-09-15: the twine suite creates `twine-4.0.0.dist-info/`; pytest
    puts the repo root first on sys.path, so `importlib_metadata` resolves the
    stub (no `Summary`) over the editable install and `twine/__init__.py` dies
    with `KeyError: 'summary'` — every test in the file errors, so a CORRECT fix
    is recorded as a failure. Revert-proof: pre-fix the stub survives.
    """
    src = tmp_path / "repo"
    src.mkdir()
    (src / "twine-4.0.0.dist-info").mkdir()
    (src / "twine-4.0.0.dist-info" / "METADATA").write_text("Name: twine")
    (src / "twine.egg-info").mkdir()  # the editable install's metadata

    monkeypatch.setattr(cb.subprocess, "run", lambda *a, **k: _Rc(1))  # untracked

    removed = cb._clean_shadowing_metadata(src)
    assert removed == ["twine-4.0.0.dist-info"]
    assert not (src / "twine-4.0.0.dist-info").exists()
    # The editable install's egg-info is REQUIRED (why the reset uses -fd, not
    # -fdx) and must survive.
    assert (src / "twine.egg-info").exists()


def test_clean_shadowing_metadata_never_deletes_tracked(tmp_path, monkeypatch):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "real-1.0.dist-info").mkdir()

    monkeypatch.setattr(cb.subprocess, "run", lambda *a, **k: _Rc(0))  # tracked

    assert cb._clean_shadowing_metadata(src) == []
    assert (src / "real-1.0.dist-info").exists()


def test_attempt_once_uses_the_popen_capture_pattern():
    """Source pin: `subprocess.run` cannot carry partial output on a timeout.

    Verified live 2026-09-15 — a 600s timeout recorded content="" / tools=[],
    leaving the hang completely unexplained. The fix is the documented Popen
    pattern (kill, then re-communicate), which DOES return the buffered output
    (proven with a real subprocess: a print-then-sleep child yields its line).
    """
    src = _RC_MOD.read_text(encoding="utf-8")
    fn = src.split("def _attempt_once(", 1)[1].split("\ndef ", 1)[0]
    assert "Popen(" in fn
    assert "subprocess.run(" not in fn
    assert "communicate(" in fn
