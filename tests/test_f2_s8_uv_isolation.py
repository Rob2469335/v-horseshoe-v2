"""Path-isolation regressions for the builder's approved uv executable.

The profile guard must reject owner-profile paths, and a directory NAME must
never establish trust: ``f2_scratch`` / ``f2_evidence`` / ``pytest_tmp`` are not
trust anchors by themselves.  Only an explicitly injected fixture root (tests
only; production never passes one) may exempt a path.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess

import pytest

from qwen_train.f2_s8_pilot_build import (
    _is_within,
    _resolved_norm,
    resolve_and_verify_uv,
)

PROFILE = pathlib.Path(r"C:\Users\rober")

#: These tests assert WINDOWS path semantics (drive paths, UNC/device spellings,
#: junctions).  On POSIX the subject does not exist, so the module is not
#: applicable there; it is not skipped to hide a failure on Windows.
pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Windows path-spelling semantics are the subject of this module",
)

#: Captured at import time, before the suite's autouse Popen mock is installed.
_REAL_POPEN = subprocess.Popen


@pytest.fixture
def real_subprocess(monkeypatch):
    """Allow the real ``cmd`` binary (junction creation) for one test."""
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)



@pytest.mark.parametrize("rel", [
    ".local/bin/uv.exe",
    "f2_pytest/uv.exe",
    ".local/bin/f2_scratch/uv.exe",
    ".local/bin/pytest_tmp/uv.exe",
    ".local/bin/f2_evidence/uv.exe",
])
def test_owner_profile_paths_are_rejected_regardless_of_component_name(rel):
    target = PROFILE / pathlib.Path(rel)
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=target, expected_sha256="0" * 64)


def test_case_variants_do_not_bypass_the_profile_guard():
    target = pathlib.Path(r"C:\Users\ROBER\.local\bin\F2_SCRATCH\uv.exe")
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=target, expected_sha256="0" * 64)


def test_approved_fixture_root_allows_a_real_fixture(tmp_path):
    stub = tmp_path / "uv.exe"
    stub.write_bytes(b"fixture-uv")
    digest = hashlib.sha256(stub.read_bytes()).hexdigest()
    got = resolve_and_verify_uv(custom_path=stub, expected_sha256=digest,
                                fixture_root=tmp_path)
    assert pathlib.Path(got) == stub.resolve()


def test_production_callers_get_no_exemption_even_under_temp(tmp_path):
    """A temporary location is not intrinsically trusted; only injection is."""
    stub = tmp_path / "f2_pytest" / "uv.exe"
    stub.parent.mkdir()
    stub.write_bytes(b"fixture-uv")
    digest = hashlib.sha256(stub.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=stub, expected_sha256=digest)


def test_traversal_out_of_an_approved_fixture_root_is_rejected(tmp_path):
    escaping = tmp_path / "sub" / ".." / ".." / ".." / ".local" / "bin" / "uv.exe"
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=escaping, expected_sha256="0" * 64,
                              fixture_root=tmp_path)


def test_intermediate_component_outside_the_fixture_root_is_rejected(tmp_path):
    other = tmp_path / "elsewhere" / "f2_scratch" / "uv.exe"
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=other, expected_sha256="0" * 64,
                              fixture_root=tmp_path / "approved")


# ---------------------------------------------------------------------------
# Windows alternate path spellings must canonicalise to the same identity
# ---------------------------------------------------------------------------

SPELLINGS = [
    pytest.param(lambda p: "\\\\?\\" + str(p), id="extended-length"),
    pytest.param(lambda p: "\\\\?\\UNC\\localhost\\C$" + str(p)[2:], id="extended-unc"),
    pytest.param(lambda p: "\\\\localhost\\C$" + str(p)[2:], id="unc-admin-share"),
    pytest.param(lambda p: "\\\\.\\" + str(p), id="device-namespace"),
    pytest.param(lambda p: str(p).replace("\\", "/"), id="forward-slashes"),
    pytest.param(lambda p: str(p).upper(), id="upper-case"),
    pytest.param(lambda p: str(p) + ".", id="trailing-dot"),
    pytest.param(lambda p: str(p) + "::$DATA", id="alternate-data-stream"),
]


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_alternate_windows_spellings_of_a_profile_path_are_rejected(tmp_path, spelling):
    stub = tmp_path / "uv.exe"
    stub.write_bytes(b"fixture-uv")
    digest = hashlib.sha256(stub.read_bytes()).hexdigest()
    attempt = spelling(stub)
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=attempt, expected_sha256=digest)


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_all_spellings_canonicalise_to_one_identity(tmp_path, spelling):
    target = tmp_path / "uv.exe"
    target.write_bytes(b"fixture-uv")
    key = _resolved_norm(target)
    assert key is not None
    assert _resolved_norm(spelling(target)) == key


def test_junction_escape_is_rejected(tmp_path, real_subprocess):
    """A junction inside the fixture root that points at the profile must fail.

    Resolution happens before containment, so the escape target (not the link
    spelling) decides.  If this host cannot create a junction the test reports
    the capability gap instead of pretending to pass.
    """
    link = tmp_path / "escape_link"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(PROFILE / ".local")],
        capture_output=True, text=True,
    )
    if created.returncode != 0:
        pytest.skip(
            "junction creation unavailable on this host: "
            f"{created.returncode} {created.stderr.strip()[:120]}"
        )
    for kwargs in ({}, {"fixture_root": tmp_path}):
        with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
            resolve_and_verify_uv(custom_path=link / "uv.exe",
                                  expected_sha256="0" * 64, **kwargs)


def test_nonexistent_profile_path_is_rejected(tmp_path):
    missing = PROFILE / ".local" / "bin" / "definitely-not-here-audit.exe"
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=missing, expected_sha256="0" * 64)


def test_lookalike_temp_directory_outside_the_fixture_root_is_rejected(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    lookalike = tmp_path / "f2_pytest" / "uv.exe"
    lookalike.parent.mkdir()
    lookalike.write_bytes(b"fixture-uv")
    digest = hashlib.sha256(lookalike.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="PROFILE_ISOLATION_VIOLATION"):
        resolve_and_verify_uv(custom_path=lookalike, expected_sha256=digest,
                              fixture_root=approved)


def test_production_tool_path_is_outside_the_profile():
    assert _is_within(r"C:\f2_pilot\tools\uv.exe", PROFILE) is False
    assert _is_within(r"\\?\C:\f2_pilot\tools\uv.exe", PROFILE) is False
    assert _is_within(r"C:\f2_pilot", PROFILE) is False

