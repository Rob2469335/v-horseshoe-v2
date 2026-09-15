"""SWE-rebench-V2 probe: interpreter mapping + field parsing (regression pins).

Both functions here fixed real bugs that produced FALSELY-FAILING experiments:
  1. `FAIL_TO_PASS` is a stringified list; `str(v).split()` shredded it into
     tokens like `"['tests/a.py::x',"` so every match read 0/0.
  2. `base_image_name` names the interpreter the instance was built for; using
     the host's (3.14) instead produced env errors that looked like task
     failures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "qwen_train" / "swe_rebench_probe.py"
_spec = importlib.util.spec_from_file_location("swe_rebench_probe", _MOD)
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


# --- interpreter mapping ---------------------------------------------------


def test_interpreter_maps_dotted_minor():
    # the bug: `python_base_310` once mapped to "-3.310"
    assert sp._interpreter_for("python_base_310") == ["py", "-3.10"]
    assert sp._interpreter_for("python_base_311") == ["py", "-3.11"]
    assert sp._interpreter_for("python_base_313") == ["py", "-3.13"]


def test_interpreter_maps_single_digit_minor():
    assert sp._interpreter_for("python_base_39") == ["py", "-3.9"]


def test_interpreter_refuses_unknown_image():
    # fail CLEAR, never silently use the host interpreter
    assert sp._interpreter_for("weird-image") is None
    assert sp._interpreter_for("python_base_999") is None
    assert sp._interpreter_for("") is None
    assert sp._interpreter_for(None) is None


# --- FAIL_TO_PASS parsing --------------------------------------------------


def test_parse_list_field_stringified_list():
    raw = "['tests/a.py::x', 'tests/a.py::y']"
    assert sp._parse_list_field(raw) == ["tests/a.py::x", "tests/a.py::y"]


def test_parse_list_field_real_list_passthrough():
    assert sp._parse_list_field(["a", "b"]) == ["a", "b"]


def test_parse_list_field_empty_and_none():
    assert sp._parse_list_field("") == []
    assert sp._parse_list_field(None) == []
    assert sp._parse_list_field("[]") == []


def test_parse_list_field_space_separated_fallback():
    assert sp._parse_list_field("tests/a.py::x tests/a.py::y") == [
        "tests/a.py::x",
        "tests/a.py::y",
    ]


# --- install step -> argv --------------------------------------------------


def test_pip_cmd_targets_instance_venv():
    py = Path("/x/venv/Scripts/python.exe")
    assert sp._pip_cmd(py, "pip install -q pytest pytest-socket") == [
        str(py),
        "-m",
        "pip",
        "install",
        "-q",
        "pytest",
        "pytest-socket",
    ]


def test_pip_cmd_non_pip_step_returns_none():
    assert sp._pip_cmd(Path("python.exe"), "apt-get install -y foo") is None
