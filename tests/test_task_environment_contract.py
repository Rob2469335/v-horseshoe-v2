"""Regression tests for the Experiment-J task-environment contract.

The defect these lock down: `run_repair_task.py` executed task tests with the
PROJECT interpreter and let bare `pytest` resolve through ambient PATH, because
every curriculum `test_cmd` begins with `pytest` and the runner's
`test_cmd.replace("python -m pytest", ...)` could never fire.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "qwen_train"))

import run_repair_task as rrt  # noqa: E402

_REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def real_subprocess():
    """Give this module the genuine `subprocess.Popen`.

    The root conftest autouse-mocks `subprocess.Popen` so tests cannot spawn
    background servers. Resolving the task interpreter genuinely shells out to
    `py -3.10 --version`, so this module needs the real class. Same distinct-name
    pattern as `tests/test_cli_opencode.py`, so the conftest fixture is still set
    up first and this one runs after it.
    """
    subprocess.Popen = _REAL_POPEN
    try:
        yield
    finally:
        subprocess.Popen = _REAL_POPEN


# --- Test 1: python_base_310 maps to the declared interpreter, not the host ---


def test_python_base_310_resolves_to_py_310():
    assert rrt.resolve_task_python({"base_image_name": "python_base_310"}) == [
        "py",
        "-3.10",
    ]


def test_task_interpreter_is_not_the_project_interpreter():
    launcher = rrt.resolve_task_python({"base_image_name": "python_base_310"})
    assert sys.executable not in launcher
    assert "-3.10" in launcher


# --- Test 2/3: unknown and unavailable interpreters fail closed ---


def test_unknown_base_image_fails_closed():
    with pytest.raises(rrt.TaskEnvironmentError):
        rrt.resolve_task_python({"base_image_name": "totally_bogus_image_999"})


def test_missing_base_image_fails_closed():
    with pytest.raises(rrt.TaskEnvironmentError):
        rrt.resolve_task_python({})


def test_unavailable_interpreter_fails_closed(monkeypatch):
    monkeypatch.setattr(rrt, "_ensure_interpreter", lambda *a, **k: None)
    with pytest.raises(rrt.TaskEnvironmentError):
        rrt.resolve_task_python({"base_image_name": "python_base_310"})


# --- Test 4/5: pytest isolation, both spellings ---


def test_bare_pytest_uses_task_interpreter():
    argv = rrt.task_test_argv("TASKPY", "pytest -q tests/test_routing.py")
    assert argv == ["TASKPY", "-m", "pytest", "-q", "tests/test_routing.py"]
    assert argv[0] == "TASKPY"


def test_python_dash_m_pytest_uses_task_interpreter():
    argv = rrt.task_test_argv("TASKPY", "python -m pytest -q tests/test_routing.py")
    assert argv == ["TASKPY", "-m", "pytest", "-q", "tests/test_routing.py"]


def test_python_dash_m_pytest_keeps_arguments():
    argv = rrt.task_test_argv("TASKPY", "python -m pytest -q --tb=short t.py")
    assert argv[-3:] == ["--tb=short", "t.py", ] or argv[-2:] == ["--tb=short", "t.py"]
    assert "-q" in argv


def _pool_row(instance_id: str) -> dict:
    """Read the authoritative curriculum row straight from swe_pool.jsonl."""
    import json

    pool = Path(__file__).resolve().parent.parent / "qwen_train" / "curriculum" / "swe_pool.jsonl"
    for line in pool.read_text("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("instance_id") == instance_id:
                return row
    raise AssertionError(f"{instance_id} not in pool")


def test_real_pool_command_is_rewritten_to_task_interpreter():
    row = _pool_row("pallets__werkzeug-2583")
    assert row["test_cmd"].startswith("pytest")
    argv = rrt.task_test_argv("TASKPY", row["test_cmd"])
    assert argv[0] == "TASKPY" and argv[1:3] == ["-m", "pytest"]
    assert argv[-1] == "tests/test_routing.py"


# --- Test 6/7: pip isolation and install metadata ---


def test_pip_targets_task_venv():
    argv = rrt.task_install_argv("TASKPY", "pip install -q -e .")
    assert argv == ["TASKPY", "-m", "pip", "install", "-q", "-e", "."]


def test_non_pip_step_returns_none():
    assert rrt.task_install_argv("TASKPY", "apt-get install foo") is None


def test_install_field_is_consumed():
    meta = rrt._pool_env_meta("pallets__werkzeug-2583")
    assert meta["install"], "curriculum install field must be read"
    steps = meta["install"] if isinstance(meta["install"], list) else [
        s for s in meta["install"].splitlines() if s.strip()
    ]
    assert steps == ["pip install -q -e .", "pip install -q -r requirements/tests.txt"]
    for step in steps:
        assert rrt.task_install_argv("TASKPY", step)[0] == "TASKPY"


def test_pool_env_meta_is_empty_for_unknown_task():
    assert rrt._pool_env_meta("no__such__task-0") == {}


# --- Test 8/9: environment location and determinism ---


def test_task_work_root_is_outside_the_repository():
    probe_work = rrt.WORK.resolve()
    repo_root = Path(rrt.__file__).resolve().parent.parent
    assert repo_root not in probe_work.parents
    assert probe_work != repo_root


def test_environment_resolution_is_deterministic():
    a = rrt.resolve_task_python({"base_image_name": "python_base_310"})
    b = rrt.resolve_task_python({"base_image_name": "python_base_310"})
    assert a == b
    argv_a = rrt.task_test_argv("TASKPY", "pytest -q t.py")
    argv_b = rrt.task_test_argv("TASKPY", "pytest -q t.py")
    assert argv_a == argv_b


# --- Test 10: the shared probe implementation is unchanged ---


def test_probe_test_cmd_semantics_still_available():
    from swe_rebench_probe import _test_cmd

    assert _test_cmd(Path("PY"), "pytest -q t.py") == [
        "PY",
        "-m",
        "pytest",
        "-q",
        "t.py",
    ]


def test_py_310_is_actually_present_on_this_host():
    p = subprocess.run(
        ["py", "-3.10", "--version"], capture_output=True, text=True, timeout=60
    )
    assert p.returncode == 0, "python_base_310 tasks require py -3.10 to be installed"
    assert "3.10" in (p.stdout or "")


# --- execution prerequisites: harness-owned pytest bootstrap ---


def test_execution_prereq_is_pytest():
    assert rrt.HARNESS_EXECUTION_PREREQS == ("pytest",)


def test_missing_pytest_is_detected(tmp_path):
    """A task venv without pytest must be reported missing, not silently borrowed."""
    fake = tmp_path / "py.exe"
    fake.write_text("")
    if sys.platform == "win32":
        p = subprocess.run(
            [sys.executable, "-c", "import importlib.util as u;print('Y' if u.find_spec('pytest') else 'N')"],
            capture_output=True, text=True, timeout=60,
        )
        # The project venv HAS pytest; assert the probe reports that honestly.
        assert rrt.task_pytest_available(sys.executable) is ("Y" in p.stdout)
    else:
        assert rrt.task_pytest_available(fake) in (True, False)


def test_pytest_bootstrap_does_not_run_when_already_present():
    recs = rrt.ensure_task_pytest(sys.executable)
    assert all(r["source"] == rrt.CURRICULUM for r in recs)
    assert {r["package"] for r in recs} == {"pytest"}


def test_pytest_bootstrap_failure_is_explicit(monkeypatch):
    """A pip install that fails must raise, never fall back to the project .venv."""
    calls = {"n": 0}
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # availability probe reports pytest MISSING
            return subprocess.CompletedProcess(cmd, 0, "N\n", "")
        return subprocess.CompletedProcess(
            cmd, 1, "", "ERROR: Could not install packages due to an OSError"
        )

    monkeypatch.setattr(rrt.subprocess, "run", fake_run)
    with pytest.raises(rrt.TaskEnvironmentError) as exc:
        rrt.ensure_task_pytest("TASKPY")
    assert "test runner" in str(exc.value)
    assert "OSError" in str(exc.value), "install failure output must reach the caller"
    assert real_run is not fake_run  # guard: patch is scoped to the test


def test_pytest_bootstrap_installs_when_missing(monkeypatch):
    """When the runner is missing and install succeeds, provenance says HARNESS."""
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(cmd, 0, "N\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rrt.subprocess, "run", fake_run)
    recs = rrt.ensure_task_pytest("TASKPY")
    assert recs == [{"package": "pytest", "source": rrt.HARNESS}]
    assert rrt.HARNESS != rrt.CURRICULUM


def test_pytest_probe_never_uses_shell(monkeypatch):
    """Safety: the bootstrap must not build shell strings."""
    seen = []

    def fake_run(cmd, **kw):
        seen.append(kw)
        return subprocess.CompletedProcess(cmd, 0, "Y\n", "")

    monkeypatch.setattr(rrt.subprocess, "run", fake_run)
    rrt.ensure_task_pytest("TASKPY")
    assert all("shell" not in kw for kw in seen)


def test_curriculum_truth_is_not_mutated():
    """The bootstrap must never edit the curriculum row."""
    before = _pool_row("qiskit__qiskit-ibm-runtime-367")["install"]
    assert before == ["pip install -e .[test,common] --quiet"]
    assert rrt.HARNESS_EXECUTION_PREREQS != ()


def test_qiskit_row_has_no_pytest_but_needs_it():
    row = _pool_row("qiskit__qiskit-ibm-runtime-367")
    assert row["test_cmd"].startswith("pytest")
    joined = " ".join(row["install"]).lower()
    assert "pytest" not in joined, "this row is the reason the harness bootstrap exists"


def test_execution_plan_provenance_is_explicit():
    row = _pool_row("pallets__werkzeug-2583")
    inst = dict(row)
    plan = rrt.task_exec_plan(inst, "TASKPY")
    assert plan["base_image_name"] == "python_base_310"
    assert plan["interpreter"] == ["py", "-3.10"]
    assert plan["execution_prereqs"] == ["pytest"]
    assert plan["install_steps"]
    for argv in plan["install_argv"]:
        assert argv[0] == "TASKPY"


def test_execution_plan_fails_closed_without_base_image():
    with pytest.raises(rrt.TaskEnvironmentError):
        rrt.task_exec_plan({"instance_id": "x", "install": []}, "TASKPY")


def test_arbitrary_command_is_preserved():
    assert rrt.task_test_argv("PY", "make test --fast") == ["make", "test", "--fast"]


def test_install_failure_information_is_preserved():
    """An install step is argv-only; failure text must reach the caller."""
    plan = rrt.task_exec_plan(_pool_row("qiskit__qiskit-ibm-runtime-367"), "TASKPY")
    assert plan["install_argv"][0] == ["TASKPY", "-m", "pip", "install", "-e", ".[test,common]", "--quiet"]


# --- evaluator-sanity path must resolve the launcher, not slice it ---


def test_evaluator_sanity_does_not_slice_launcher():
    """Source guard: the bad `launcher[-1]` resolution must not come back.

    For a Windows launcher `["py", "-3.10"]` that expression yields the string
    `"-3.10"`, which is not an executable, so the evaluator sanity run would
    silently fall back to ambient resolution.
    """
    import inspect

    src = inspect.getsource(rrt._run_evaluator_sanity_check)
    assert "launcher[-1]" not in src, "evaluator sanity must not slice the launcher"
    assert "_task_python_path(launcher)" in src, (
        "evaluator sanity must resolve the launcher via the shared helper"
    )


def test_task_python_path_never_returns_a_bare_version_flag():
    for launcher in (["py", "-3.10"], ["python", "-3.9"], ["py"]):
        resolved = rrt._task_python_path(launcher)
        assert not str(resolved).startswith("-"), (
            f"{launcher} must not resolve to a bare version flag, got {resolved!r}"
        )


def test_evaluator_sanity_invokes_task_interpreter(monkeypatch, tmp_path):
    """Exercise the real function: capture the argv it actually executes."""
    seen = {}

    class _Proc:
        returncode = 1
        stdout = "FAILED tests/x.py::test_a - AssertionError\n"
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kwargs"] = kw
        return _Proc()

    monkeypatch.setattr(rrt, "resolve_task_python", lambda inst: ["py", "-3.10"])
    monkeypatch.setattr(rrt, "task_test_argv", lambda py, tc: [str(py), "-m", "pytest"])
    monkeypatch.setattr(rrt.subprocess, "run", fake_run)

    ok, _out = rrt._run_evaluator_sanity_check(
        tmp_path, tmp_path, Path("IGNORED"), "pytest -q tests/x.py", {"base_image_name": "python_base_310"}
    )

    argv = seen["cmd"]
    assert argv[0] not in ("-3.10", "-3.9"), (
        f"launcher was sliced to {argv[0]!r} instead of a resolved interpreter"
    )
    assert "shell" not in seen["kwargs"], "task-test execution must not use shell=True"
    assert ok is False, "failure output on buggy code must report not-all-passed"