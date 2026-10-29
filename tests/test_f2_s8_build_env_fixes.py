"""Build-environment defects found by the 15-task rehearsal.

1. conan-18842 (Python 3.7) failed PYTEST_INSTALLED_UNVERIFIABLE because the
   probe imports ``importlib.metadata``, which does not exist before 3.8.
2. SDV-2658 failed DEPENDENCY_RESOLUTION_FAILURE because the install command
   passes the approved pytest pin AND a declared pytest requirement, so pip
   sees the same project twice ("Double requirement given").
3. privacyidea-4613 failed SOURCE_ACQUISITION_FAILURE on a Windows MAX_PATH
   clone; git's documented per-invocation fix is ``-c core.longpaths=true``.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from qwen_train import f2_s8_pilot_build as pb

_REAL_POPEN = subprocess.Popen

PY37 = pathlib.Path(
    r"C:\Users\rober\F2_SCRATCH\s8_resolve_out\interp\py37_20261021\python.exe"
)


def test_pytest_version_probe_works_on_python_37():
    if not PY37.is_file():
        pytest.skip("Python 3.7 interpreter not present on this machine")
    # A bare 3.7 has no pytest: the expected refusal is "pytest is not
    # installed", never "No module named 'importlib.metadata'".
    from unittest.mock import patch
    with patch("subprocess.Popen", _REAL_POPEN):
        with pytest.raises(RuntimeError) as exc:
            pb.installed_pytest_version(PY37)
    message = str(exc.value)
    assert "pytest is not installed" in message, message
    assert "importlib.metadata" not in message, message


def test_declared_pytest_requirements_are_filtered_from_install_argv():
    kept = pb._without_pytest([
        "pytest==8.3.5,>=3.4.2", "typing_extensions>=4.2", "pytest>=7",
        "pytest_asyncio==1.2.0",
    ])
    assert kept == ["typing_extensions>=4.2", "pytest_asyncio==1.2.0"], kept


def test_git_commands_enable_long_paths():
    argv = pb._git_command("clone", "--quiet", "url", "dest")
    assert argv[:3] == [pb.GIT, "-c", "core.longpaths=true"], argv
    assert argv[3:] == ["clone", "--quiet", "url", "dest"], argv
