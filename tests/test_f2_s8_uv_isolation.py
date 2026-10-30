"""Path-isolation regressions for the builder's approved uv executable.

The profile guard must reject owner-profile paths, and a directory NAME must
never establish trust: ``f2_scratch`` / ``f2_evidence`` / ``pytest_tmp`` are not
trust anchors by themselves.  Only an explicitly injected fixture root (tests
only; production never passes one) may exempt a path.
"""
from __future__ import annotations

import hashlib
import pathlib

import pytest

from qwen_train.f2_s8_pilot_build import resolve_and_verify_uv

PROFILE = pathlib.Path(r"C:\Users\rober")


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
