"""Regression tests for the deterministic project-root helper (swarm_os/lib/paths).

The backend's cwd-dependent paths (event_bus patch log, screen audit/screenshots,
control file containment, LSP workspace root) were previously bound to whatever
the process cwd was — silently relocated under a chdir'd test/embed. project_root()
resolves deterministically from this module's own path with an optional
ZENITH_PROJECT_ROOT override and an AGENTS.md existence check.
"""

import os

from swarm_os.lib.paths import project_root

PROJECT_ROOT = r"C:\Users\rober\Projects\v-horseshoe-v2"


def _reset(monkeypatch):
    import swarm_os.lib.paths as p

    monkeypatch.setattr(p, "_PROJECT_ROOT_CACHE", None)


def test_project_root_resolves_to_repo_root():
    r = project_root()
    assert r.is_absolute()
    assert (r / "AGENTS.md").exists()
    assert str(r).replace("\\", "/").endswith("v-horseshoe-v2")


def test_project_root_deterministic_under_chdir(monkeypatch, tmp_path):
    _reset(monkeypatch)
    before = project_root()
    monkeypatch.chdir(tmp_path)
    after = project_root()
    assert after == before, "project_root must not depend on the process cwd"


def test_project_root_honors_zenv_override(monkeypatch):
    _reset(monkeypatch)
    # A valid override (points at the real root) is honored.
    monkeypatch.setenv("ZENITH_PROJECT_ROOT", PROJECT_ROOT)
    got = os.path.normpath(str(project_root()))
    want = os.path.normpath(os.path.abspath(PROJECT_ROOT))
    assert got == want, (got, want)


def test_project_root_invalid_override_falls_back(monkeypatch):
    _reset(monkeypatch)
    # An override that is NOT a project (no AGENTS.md) falls back to the
    # deterministic file-resolved root rather than honoring the bad value.
    monkeypatch.setenv("ZENITH_PROJECT_ROOT", PROJECT_ROOT + r"\swarm_os")
    r = project_root()
    assert r.is_absolute()
    # It must still resolve to a real project root (AGENTS.md present).
    assert (r / "AGENTS.md").exists()