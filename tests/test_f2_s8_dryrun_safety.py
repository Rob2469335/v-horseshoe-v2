"""Dry-run authorization verification must not touch budget/ledger state.

These tests prove the *verification* path (``reserve_execution=False``) creates
no budget directory and leaves the fixture tree byte-for-byte and entry-for-entry
unchanged.  They do not exercise a live budget store; they use temporary roots.
"""
from __future__ import annotations

import hashlib
import pathlib

from qwen_train import f2_s8_v2_gate as G
from tests.test_f2_s8_probe_authz import _identity, _setup_probe_env


def _inventory(root: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        out[rel] = "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _verify(env, budget_root: pathlib.Path):
    return G.authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        compat_report_file=env["compat_file"],
        trust_root_path=env["trust_root"],
        budget_ledger_root=budget_root,
        reserve_execution=False,
        current_identity=_identity("testuser"),
    )


def test_verification_does_not_create_the_budget_root(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=("testuser",))
    budget_root = tmp_path / "budget_dry_run"
    assert not budget_root.exists()

    _verify(env, budget_root)

    assert not budget_root.exists(), "verification created the budget root"


def test_repeated_verification_leaves_every_fixture_unchanged(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch, allowed_identities=("testuser",))
    budget_root = tmp_path / "budget_repeat"
    before = _inventory(tmp_path)

    for _ in range(2):
        _verify(env, budget_root)

    after = _inventory(tmp_path)
    assert after == before, "verification changed fixture bytes or entries"
    assert not budget_root.exists()
