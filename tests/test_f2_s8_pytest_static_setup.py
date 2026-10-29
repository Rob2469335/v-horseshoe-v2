"""Static setup.py requirement-file resolution (conan / Cirq shapes).

The analyser refuses a dynamic ``install_requires`` rather than guessing.  Two
very common shapes are still fully static data flow -- a file read -- and are
resolved without executing the setup script:

  NAME = get_requires("requirements.txt")        # conan
  NAME = open("requirements.txt").readlines()    # cirq-core
  setup(install_requires=NAME)

Anything else stays blocking.
"""
from __future__ import annotations

from pathlib import Path

from qwen_train.f2_s8_pytest_policy import collect_pytest_declarations


def _project(tmp_path: Path, setup_py: str, files: dict[str, str]) -> Path:
    (tmp_path / "setup.py").write_text(setup_py, encoding="utf-8")
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


CONAN_SHAPE = '''
from setuptools import setup


def get_requires(filename):
    requirements = []
    with open(filename, "rt") as req_file:
        for line in req_file.read().splitlines():
            if not line.strip().startswith("#"):
                requirements.append(line)
    return requirements


project_requirements = get_requires("conans/requirements.txt")
setup(name="conan", install_requires=project_requirements)
'''

CIRQ_SHAPE = '''
from setuptools import setup

requirements = open("requirements.txt").readlines()
requirements = [r.strip() for r in requirements]
setup(name="cirq-core", install_requires=requirements)
'''


def _blocking(result):
    return [p for p in result["problems"] if p.get("scope") == "blocking"]


def test_get_requires_file_is_resolved_statically(tmp_path):
    project = _project(tmp_path, CONAN_SHAPE,
                       {"conans/requirements.txt": "# comment\nrequests>=2.25\npytest==7.4.4\n"})
    result = collect_pytest_declarations(project=project)
    assert _blocking(result) == [], _blocking(result)
    raw = [d["raw"] for d in result["declarations"]]
    assert "pytest==7.4.4" in raw, raw


def test_open_readlines_file_is_resolved_statically(tmp_path):
    project = _project(tmp_path, CIRQ_SHAPE,
                       {"requirements.txt": "attrs>=21.3\npytest==8.3.5\n"})
    result = collect_pytest_declarations(project=project)
    assert _blocking(result) == [], _blocking(result)
    raw = [d["raw"] for d in result["declarations"]]
    assert "pytest==8.3.5" in raw, raw


def test_unresolvable_dynamic_declaration_stays_blocking(tmp_path):
    setup_py = '''
import os
from setuptools import setup
setup(name="x", install_requires=os.environ["REQS"].split(","))
'''
    project = _project(tmp_path, setup_py, {})
    result = collect_pytest_declarations(project=project)
    codes = [p["code"] for p in _blocking(result)]
    assert "setup_py_dynamic_declaration" in codes, codes


def test_file_read_outside_the_project_stays_blocking(tmp_path):
    setup_py = '''
from setuptools import setup


def get_requires(filename):
    with open(filename) as fh:
        return fh.read().splitlines()


reqs = get_requires("../outside.txt")
setup(name="x", install_requires=reqs)
'''
    (tmp_path / "outside.txt").write_text("pytest==8.3.5\n", encoding="utf-8")
    project = _project(tmp_path, setup_py, {})
    result = collect_pytest_declarations(project=project)
    codes = [p["code"] for p in _blocking(result)]
    assert "setup_py_dynamic_declaration" in codes, codes
