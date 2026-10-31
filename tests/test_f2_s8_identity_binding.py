"""Identity is the process TOKEN, not a settable environment string.

These tests prove the gate's decision logic (mocked identities) and, on Windows,
that the token resolver returns a real SID/name.  They do NOT prove Windows ACL
behaviour or that a live f2builder logon is accepted; that requires a real
f2builder session (not run here).
"""
from __future__ import annotations

import os

import pytest

from qwen_train import f2_s8_v2_gate as G
from qwen_train.f2_s8_acquisition import (
    IdentityLookupError,
    resolve_process_identity,
)
from tests.test_f2_s8_probe_authz import _identity, _setup_probe_env


def _run(env, tmp_path, **kwargs):
    return G.authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        compat_report_file=env["compat_file"],
        trust_root_path=env["trust_root"],
        budget_ledger_root=tmp_path / "broot_identity",
        **kwargs,
    )


def test_correct_token_identity_passes(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=("testuser",))
    auth = _run(env, tmp_path, current_identity=_identity("testuser"))
    assert auth.current_identity.account == "testuser"


def test_spoofed_username_does_not_grant_identity(tmp_path, monkeypatch):
    """USERNAME=authorized must not help a process whose token is someone else."""
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=("testuser",))
    monkeypatch.setenv("USERNAME", "testuser")
    monkeypatch.setenv("USERDOMAIN", "ROB3")
    with pytest.raises(G.V2GateError, match="not in allowed_identities"):
        _run(env, tmp_path, current_identity=_identity("evil"))


def test_identity_lookup_failure_fails_closed(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=("testuser",))

    def _boom():
        raise IdentityLookupError("token unavailable")

    monkeypatch.setattr("qwen_train.f2_s8_acquisition.resolve_process_identity", _boom)
    with pytest.raises(G.V2GateError) as exc:
        _run(env, tmp_path)
    assert exc.value.code == G.PROBE_IDENTITY_UNAVAILABLE


def test_malformed_identity_object_is_refused(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=("testuser",))
    with pytest.raises(G.V2GateError) as exc:
        _run(env, tmp_path, current_identity="testuser")
    assert exc.value.code == G.PROBE_IDENTITY_UNAVAILABLE


def test_allowed_sid_matches_token_sid(tmp_path, monkeypatch):
    sid = "S-1-5-21-0-0-0-4242"
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=(sid,))
    auth = _run(env, tmp_path, current_identity=_identity("testuser", sid=sid))
    assert auth.current_identity.sid == sid


def test_wrong_sid_is_rejected(tmp_path, monkeypatch):
    env = _setup_probe_env(
        tmp_path, monkeypatch, allowed_identities=("S-1-5-21-0-0-0-4242",)
    )
    with pytest.raises(G.V2GateError, match="not in allowed_identities"):
        _run(env, tmp_path, current_identity=_identity("testuser", sid="S-1-5-21-0-0-0-9999"))


def test_domain_qualified_allowed_entry_matches(tmp_path, monkeypatch):
    env = _setup_probe_env(
        tmp_path, monkeypatch, allowed_identities=("rob3\\testuser",)
    )
    auth = _run(env, tmp_path, current_identity=_identity("testuser"))
    assert auth.current_identity.name.lower() == "rob3\\testuser"


@pytest.mark.skipif(os.name != "nt", reason="Windows process-token API")
def test_real_process_token_resolves_sid_and_name():
    ident = resolve_process_identity()
    assert ident.sid.startswith("S-1-")
    assert "\\" in ident.name
    assert ident.account


def test_gate_accepts_an_uppercase_sid_allow_entry(tmp_path, monkeypatch):
    sid = "S-1-5-21-0-0-0-7777"
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=(sid,))
    auth = _run(env, tmp_path, current_identity=_identity("testuser", sid=sid))
    assert auth.current_identity.sid == sid


def test_gate_accepts_a_mixed_case_domain_allow_entry(tmp_path, monkeypatch):
    env = _setup_probe_env(
        tmp_path, monkeypatch, allowed_identities=("ROB3\\F2BUILDER",)
    )
    auth = _run(env, tmp_path, current_identity=_identity("f2builder"))
    assert auth.current_identity.account == "f2builder"


def test_gate_rejects_a_malformed_sid_allow_entry(tmp_path, monkeypatch):
    env = _setup_probe_env(
        tmp_path, monkeypatch, allowed_identities=("S-1-5-21-abc",)
    )
    with pytest.raises(G.V2GateError, match="not in allowed_identities"):
        _run(env, tmp_path, current_identity=_identity("testuser"))
