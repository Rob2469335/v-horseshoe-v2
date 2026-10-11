"""Adversarial regression suite: F2 authorization binding, durable single-use
consumption, trust-root identity, and parent/child propagation.

NO protected F2 stage is executed here. Every signature uses an ephemeral
test key; every trust root and ledger lives in a temporary directory; the
production adapter's spawn seam is stubbed or reached only far enough to prove
the guard denied first.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from qwen_train import f2_authorization as A
from qwen_train.f2_authz_ledger import (
    DEFAULT_LEDGER_ROOT,
    AuthorizationLedger,
    LedgerError,
    consumption_key,
    execution_binding_digest,
)
from tests.f2_authz_testkit import (
    apply_repo_identity,
    keypair,
    provision_parent_receipt,
    provision_record_env,
    signed_record,
    write_record,
    write_trust_root,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def real_subprocess(monkeypatch):
    """Restore the genuine subprocess.Popen for tests that must spawn real
    helper processes (the suite-wide safety mock replaces it with a MagicMock)."""
    from tests.conftest import _REAL_POPEN

    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)


def _expect_deny(fn):
    with pytest.raises(A.AuthorizationError):
        fn()


# ===========================================================================
# 1-3. Durable single-use: first, second, concurrent
# ===========================================================================

def _binding(rollout="r1"):
    return {"stage": "CONF-001", "operation": "confirmatory_run",
            "rollout_id": rollout, "arm": "X",
            "experiment_id": "experiment_j", "protocol_version": "f2_v1"}


def test_first_consumption_succeeds(tmp_path):
    entry = AuthorizationLedger(tmp_path / "ledger").consume(
        record_digest="a" * 64, binding=_binding(),
        binding_digest=execution_binding_digest(_binding()),
        stage="CONF-001", operation="confirmatory_run",
    )
    assert entry["schema"] == "f2-authz-consumption/v1"
    assert (tmp_path / "ledger").is_dir()


def test_second_consumption_same_record_and_binding_denied(tmp_path):
    ledger = AuthorizationLedger(tmp_path / "ledger")
    bd = execution_binding_digest(_binding())
    ledger.consume(record_digest="a" * 64, binding=_binding(), binding_digest=bd,
                   stage="CONF-001", operation="confirmatory_run")
    _expect_deny(lambda: ledger.consume(
        record_digest="a" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run"))


def test_concurrent_processes_exactly_one_winner(tmp_path, real_subprocess):
    """Genuine multi-process race on the same consumption key."""
    ledger_dir = tmp_path / "ledger"
    script = textwrap.dedent(f"""
        import json, sys
        from qwen_train.f2_authz_ledger import AuthorizationLedger, execution_binding_digest
        b = {repr(_binding())}
        try:
            AuthorizationLedger({str(ledger_dir)!r}).consume(
                record_digest="{'a' * 64}", binding=b,
                binding_digest=execution_binding_digest(b),
                stage="CONF-001", operation="confirmatory_run")
            print("WON")
        except Exception as exc:
            print(type(exc).__name__)
        """)
    procs = [subprocess.Popen(
        [sys.executable, "-c", script], cwd=str(_REPO_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for _ in range(4)]
    results = [p.communicate(timeout=120) for p in procs]
    won = sum(1 for out, _ in results if out.strip() == "WON")
    assert won == 1, f"expected exactly one winner, got {won}: {results!r}"
    denied = [out.strip() for out, _ in results if out.strip() == "AuthorizationError"]
    assert len(denied) == 3


# ===========================================================================
# 4-6. Restart replay, crash-after-consume, corrupt ledger
# ===========================================================================

def test_replay_denied_after_process_restart(tmp_path, real_subprocess):
    ledger_dir = tmp_path / "ledger"
    bd = execution_binding_digest(_binding())
    AuthorizationLedger(ledger_dir).consume(
        record_digest="b" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run")
    # "restart": a brand-new ledger object AND a brand-new interpreter.
    script = textwrap.dedent(f"""
        from qwen_train.f2_authz_ledger import AuthorizationLedger, execution_binding_digest
        b = {repr(_binding())}
        try:
            AuthorizationLedger({str(ledger_dir)!r}).consume(
                record_digest="{'b' * 64}", binding=b,
                binding_digest=execution_binding_digest(b),
                stage="CONF-001", operation="confirmatory_run")
            print("REPLAYED")
        except Exception as exc:
            print(type(exc).__name__)
        """)
    out = subprocess.run([sys.executable, "-c", script], cwd=str(_REPO_ROOT),
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "AuthorizationError", out
    _expect_deny(lambda: AuthorizationLedger(ledger_dir).consume(
        record_digest="b" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run"))


def test_crash_after_consume_before_completion_stays_consumed(tmp_path):
    """A crash between consumption and execution must not restore the grant."""
    ledger = AuthorizationLedger(tmp_path / "ledger")
    bd = execution_binding_digest(_binding())
    entry = ledger.consume(record_digest="c" * 64, binding=_binding(), binding_digest=bd,
                           stage="CONF-001", operation="confirmatory_run")
    # simulate the "crashed worker": nothing else ever happens. Restart:
    restarted = AuthorizationLedger(tmp_path / "ledger")
    got = restarted.verify_consumption(
        consumption_id=entry["consumption_id"], record_digest="c" * 64,
        binding_digest=bd)
    assert got["consumption_id"] == entry["consumption_id"]
    _expect_deny(lambda: restarted.consume(
        record_digest="c" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run"))


def test_corrupt_ledger_entry_fails_closed(tmp_path):
    ledger_dir = tmp_path / "ledger"
    bd = execution_binding_digest(_binding())
    entry = AuthorizationLedger(ledger_dir).consume(
        record_digest="d" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run")
    path = ledger_dir / f"{consumption_key('d' * 64, bd)}.json"
    path.write_text('{"schema": "f2-authz-consumption/v1", "trunc', "utf-8")
    with pytest.raises(LedgerError):
        AuthorizationLedger(ledger_dir).verify_consumption(
            consumption_id=entry["consumption_id"], record_digest="d" * 64,
            binding_digest=bd)
    with pytest.raises(LedgerError):
        AuthorizationLedger(ledger_dir).consume(
            record_digest="d" * 64, binding=_binding(), binding_digest=bd,
            stage="CONF-001", operation="confirmatory_run")


def test_crashed_creator_partial_write_fails_closed_not_unused(tmp_path):
    """A file left by a crashed creator (valid schema, wrong content) must
    never read as 'unused'."""
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    bd = execution_binding_digest(_binding())
    (ledger_dir / f"{consumption_key('e' * 64, bd)}.json").write_text(
        '{"schema": "f2-authz-consumption/v1"}', "utf-8")
    # Not "unused": the key exists, so consumption is refused (fail closed).
    _expect_deny(lambda: AuthorizationLedger(ledger_dir).consume(
        record_digest="e" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run"))


# ===========================================================================
# 7-10. Revocation, record substitution, binding substitution, duplicate ids
# ===========================================================================

def test_revoked_key_in_trust_root_denies_even_when_still_listed(tmp_path):
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)}, revoked=("owner",))
    rec = signed_record()
    p = write_record(tmp_path, rec, priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


def test_record_revoked_flag_denies(tmp_path):
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)})
    p = write_record(tmp_path, signed_record(revoked=True), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


def test_record_substitution_after_signing_denied(tmp_path):
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)})
    p = write_record(tmp_path, signed_record(), priv)
    doc = json.loads(p.read_text("utf-8"))
    doc["record"]["id"] = "IMPOSTOR"
    p.write_text(json.dumps(doc), "utf-8")
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


def test_consumption_cannot_be_reused_for_a_different_binding(tmp_path):
    ledger = AuthorizationLedger(tmp_path / "ledger")
    bd1 = execution_binding_digest(_binding("r1"))
    entry = ledger.consume(record_digest="f" * 64, binding=_binding("r1"),
                           binding_digest=bd1, stage="CONF-001",
                           operation="confirmatory_run")
    bd2 = execution_binding_digest(_binding("r2"))
    with pytest.raises(A.AuthorizationError):
        ledger.verify_consumption(
            consumption_id=entry["consumption_id"], record_digest="f" * 64,
            binding_digest=bd2)


def test_duplicate_consumption_ids_fail_closed(tmp_path):
    ledger_dir = tmp_path / "ledger"
    bd = execution_binding_digest(_binding())
    entry = AuthorizationLedger(ledger_dir).consume(
        record_digest="9" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run")
    # Forge a second file carrying the same consumption_id.
    forged = dict(entry)
    (ledger_dir / "forged.json").write_text(json.dumps(forged), "utf-8")
    with pytest.raises(LedgerError):
        AuthorizationLedger(ledger_dir).verify_consumption(
            consumption_id=entry["consumption_id"], record_digest="9" * 64,
            binding_digest=bd)


# ===========================================================================
# 11-12. Unavailable store; recovery preserves consumed state
# ===========================================================================

def test_unavailable_ledger_store_fails_closed(tmp_path):
    file_root = tmp_path / "not-a-dir"
    file_root.write_text("occupied", "utf-8")
    with pytest.raises(LedgerError):
        AuthorizationLedger(file_root).consume(
            record_digest="0" * 64, binding=_binding(),
            binding_digest=execution_binding_digest(_binding()),
            stage="CONF-001", operation="confirmatory_run")


def test_recovery_preserves_consumed_state_across_sibling_corruption(tmp_path):
    ledger_dir = tmp_path / "ledger"
    bd = execution_binding_digest(_binding())
    entry = AuthorizationLedger(ledger_dir).consume(
        record_digest="1" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run")
    (ledger_dir / "zz-junk.txt").write_text("junk", "utf-8")
    # An unexpected file poisons the whole ledger read: everything fails
    # closed; nothing silently re-opens a consumed binding.
    with pytest.raises(LedgerError):
        AuthorizationLedger(ledger_dir).verify_consumption(
            consumption_id=entry["consumption_id"], record_digest="1" * 64,
            binding_digest=bd)
    _expect_deny(lambda: AuthorizationLedger(ledger_dir).consume(
        record_digest="1" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run"))


def test_admin_deletion_of_ledger_state_is_a_documented_gap_not_a_control(tmp_path):
    """HONESTY TEST: a principal who can delete ledger files CAN restore a
    consumed binding. The module docstring must say so; the software must
    never claim otherwise."""
    from qwen_train import f2_authz_ledger as L

    assert "NOT independent tamper resistance" in L.__doc__
    assert "hostile local administrator" in L.__doc__
    ledger_dir = tmp_path / "ledger"
    bd = execution_binding_digest(_binding())
    AuthorizationLedger(ledger_dir).consume(
        record_digest="2" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run")
    for f in ledger_dir.glob("*.json"):
        f.unlink()  # what an administrator could do
    # Replay is then possible - proving the boundary is the DIRECTORY's
    # writability, not cryptography. Recorded, not claimed as protected.
    AuthorizationLedger(ledger_dir).consume(
        record_digest="2" * 64, binding=_binding(), binding_digest=bd,
        stage="CONF-001", operation="confirmatory_run")


# ===========================================================================
# Binding contract: missing / mismatched / caller-substitution
# ===========================================================================

def test_conf_record_missing_experiment_id_denied(tmp_path, monkeypatch):
    import base64

    apply_repo_identity(monkeypatch)
    ctx = provision_record_env(
        tmp_path, monkeypatch, stage="CONF-001", ops=("confirmatory_run",))
    doc = json.loads(ctx["record_path"].read_text("utf-8"))
    del doc["record"]["experiment_id"]
    # re-sign the shortened record so only the binding (not the signature) is
    # what fails: a compliant signature must not smuggle a missing binding.
    doc["signature"]["sig"] = base64.b64encode(
        ctx["priv"].sign(A.canonical_bytes(doc["record"]))).decode()
    ctx["record_path"].write_text(json.dumps(doc), "utf-8")
    _expect_deny(lambda: A.verify_authorization(
        ctx["record_path"], stage="CONF-001", operation="confirmatory_run",
        trust_root=ctx["trust_root"],
        binding={"experiment_id": "experiment_j",
                 "protocol_version": "f2_v1"}))


def test_conf_record_mismatched_protocol_version_denied(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    ctx = provision_record_env(
        tmp_path, monkeypatch, stage="CONF-001", ops=("confirmatory_run",),
        binding={"experiment_id": "experiment_j", "protocol_version": "f2_v1",
                 "arm": "X"})
    _expect_deny(lambda: A.verify_authorization(
        ctx["record_path"], stage="CONF-001", operation="confirmatory_run",
        trust_root=ctx["trust_root"],
        binding={"experiment_id": "experiment_j",
                 "protocol_version": "EVIL-V"}))


def test_required_binding_without_independent_expected_value_denied(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    ctx = provision_record_env(
        tmp_path, monkeypatch, stage="CONF-001", ops=("confirmatory_run",),
        binding={"experiment_id": "experiment_j", "protocol_version": "f2_v1"})
    _expect_deny(lambda: A.verify_authorization(
        ctx["record_path"], stage="CONF-001", operation="confirmatory_run",
        trust_root=ctx["trust_root"]))  # no expected values supplied at all


def test_unknown_binding_field_rejected(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    ctx = provision_record_env(
        tmp_path, monkeypatch, stage="POP-001", ops=("acquire",))
    _expect_deny(lambda: A.verify_authorization(
        ctx["record_path"], stage="POP-001", operation="acquire",
        trust_root=ctx["trust_root"], binding={"revsion": "typo"}))


def test_caller_supplied_repository_revision_never_overrides_git(tmp_path, monkeypatch):
    ctx = provision_record_env(
        tmp_path, monkeypatch, stage="POP-001", ops=("acquire",))
    # No identity pin here: the REAL git identity of this repository is used.
    _expect_deny(lambda: A.verify_authorization(
        ctx["record_path"], stage="POP-001", operation="acquire",
        trust_root=ctx["trust_root"],
        binding={"repository_revision": "f" * 40}))


def test_record_repository_revision_must_equal_executing_revision(tmp_path, monkeypatch):
    ctx = provision_record_env(
        tmp_path, monkeypatch, stage="POP-001", ops=("acquire",),
        repository_revision="b" * 40)  # != real HEAD (identity NOT pinned)
    _expect_deny(lambda: A.verify_authorization(
        ctx["record_path"], stage="POP-001", operation="acquire",
        trust_root=ctx["trust_root"]))


# ===========================================================================
# Real git identity semantics (normal / dirty / detached / worktree / no git)
# ===========================================================================

def _init_repo(path: Path) -> str:
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=str(path), check=True, capture_output=True)
    (path / "a.txt").write_text("one", "utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(path), check=True,
                   capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(path), check=True,
                          capture_output=True, text=True).stdout.strip()


def test_repository_identity_normal_and_dirty(tmp_path, real_subprocess):
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _init_repo(repo)
    ident = A.repository_identity(repo)
    assert ident == {"revision": head, "dirty": False}
    (repo / "a.txt").write_text("two", "utf-8")
    assert A.repository_identity(repo)["dirty"] is True


def test_repository_identity_detached_head(tmp_path, real_subprocess):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "checkout", "-q", "--detach", "HEAD"], cwd=str(repo),
                   check=True, capture_output=True)
    assert A.repository_identity(repo)["revision"]


def test_repository_identity_worktree_shares_head(tmp_path, real_subprocess):
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _init_repo(repo)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(wt), "HEAD"], cwd=str(repo),
                   check=True, capture_output=True)
    assert A.repository_identity(wt)["revision"] == head


def test_repository_identity_without_git_metadata_fails_closed(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    _expect_deny(lambda: A.repository_identity(plain))


def test_dirty_tree_denies_protected_operation_with_real_git(tmp_path, monkeypatch, real_subprocess):
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _init_repo(repo)
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)})
    rec = signed_record(repository_revision=head)
    p = write_record(tmp_path, rec, priv)
    # clean tree: passes (identity derived from the REAL tmp repo)
    A.verify_authorization(p, stage="POP-001", operation="acquire",
                           trust_root=root, repository_root=repo)
    (repo / "a.txt").write_text("dirty", "utf-8")
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root,
        repository_root=repo))


# ===========================================================================
# Trust-root identity, epochs, digest pin, replacement
# ===========================================================================

def test_trust_root_rejects_wrong_key_length(tmp_path):
    import base64

    bad = base64.b64encode(b"short").decode()
    (tmp_path / "bad_root.json").write_text(
        json.dumps({"signers": {"owner": bad}}), "utf-8")
    _expect_deny(lambda: A.load_trust_root(tmp_path / "bad_root.json"))


def test_trust_root_rejects_non_base64_key(tmp_path):
    (tmp_path / "bad_root2.json").write_text(
        json.dumps({"signers": {"owner": "!!!not-base64!!!"}}), "utf-8")
    _expect_deny(lambda: A.load_trust_root(tmp_path / "bad_root2.json"))


def test_trust_root_digest_pin_mismatch_denies(tmp_path, monkeypatch):
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)})
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", "0" * 64)
    _expect_deny(lambda: A.load_trust_root(root))


def test_trust_root_digest_pin_match_allows(tmp_path, monkeypatch):
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)})
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(root))
    signers, revoked, epochs = A.load_trust_root(root)
    assert "owner" in signers and not revoked and not epochs


def test_key_epoch_mismatch_denies_correct_epoch_allows(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)}, epochs={"owner": 2})
    p_wrong = write_record(tmp_path, signed_record(), priv, key_epoch=1,
                           name="auth_wrong_epoch.json")
    _expect_deny(lambda: A.verify_authorization(
        p_wrong, stage="POP-001", operation="acquire", trust_root=root))
    # missing epoch when the root declares one -> deny
    p_missing = write_record(tmp_path, signed_record(), priv, name="auth_missing_epoch.json")
    _expect_deny(lambda: A.verify_authorization(
        p_missing, stage="POP-001", operation="acquire", trust_root=root))
    # correct epoch -> allow
    p_ok = write_record(tmp_path, signed_record(), priv, key_epoch=2,
                        name="auth_ok_epoch.json")
    got = A.verify_authorization(
        p_ok, stage="POP-001", operation="acquire", trust_root=root)
    assert got["id"] == "AUTH-TEST-1"


def test_trust_root_replacement_with_new_key_rejects_old_records(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    old_priv, old_pub = keypair()
    old_root = write_trust_root(tmp_path, {"owner": (old_priv, old_pub)},
                                name="root_old.json")
    rec = signed_record()
    p = write_record(tmp_path, rec, old_priv)
    A.verify_authorization(p, stage="POP-001", operation="acquire",
                           trust_root=old_root)
    # Owner rotates: new root with a DIFFERENT key. Old record must be denied.
    new_priv, new_pub = keypair()
    new_root = write_trust_root(tmp_path, {"owner-2": (new_priv, new_pub)},
                                name="root_new.json")
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=new_root))


def test_describe_trust_root_exposes_public_fingerprint_only(tmp_path):
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {"owner": (priv, pub)}, epochs={"owner": 1})
    desc = A.describe_trust_root(root)
    assert desc["signer_key_ids"] == ["owner"]
    assert desc["key_epochs"] == {"owner": 1}
    assert len(desc["digest_sha256"]) == 64
    assert "priv" not in json.dumps(desc).lower()


# ===========================================================================
# Path hardening (A)
# ===========================================================================

def test_nul_byte_path_denied():
    _expect_deny(lambda: A.verify_authorization(
        "auth\x00.json", stage="POP-001", operation="acquire"))


def test_whitespace_only_path_denied():
    _expect_deny(lambda: A.verify_authorization(
        "   ", stage="POP-001", operation="acquire"))


# ===========================================================================
# Orchestrator / worker propagation (B): fail closed before side effects
# ===========================================================================

def test_run_f2_arm_execute_without_stage_denied_before_any_work(tmp_path, monkeypatch):
    from qwen_train import f2_arm_orchestrator as orch

    called = []
    monkeypatch.setattr(orch, "_enforce_readiness", lambda **k: called.append("ready"))
    _expect_deny(lambda: orch.run_f2_arm(
        arm="T", manifest_dir=tmp_path, worker_script=tmp_path / "w.py",
        repo_root=tmp_path, render_t=lambda: called.append("render"),
        lesson_l_id="l", lesson_l_hash="h", task_id="t", execute=True))
    assert called == []  # denied before readiness, render, freeze, spawn


def test_run_f2_arm_execute_denied_before_readiness_when_record_invalid(
        tmp_path, monkeypatch):
    from qwen_train import f2_arm_orchestrator as orch

    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(tmp_path / "absent.json"))
    called = []
    _expect_deny(lambda: orch.run_f2_arm(
        arm="X", manifest_dir=tmp_path, worker_script=tmp_path / "w.py",
        repo_root=tmp_path, render_t=lambda: called.append("render"),
        lesson_l_id="l", lesson_l_hash="h", task_id="t", execute=True,
        stage="CONF-001"))
    assert called == []


def test_readiness_failure_does_not_burn_the_consumption(tmp_path, monkeypatch):
    from qwen_train import f2_arm_orchestrator as orch
    from runtime_v2.services.task_readiness import ReadinessGateError

    apply_repo_identity(monkeypatch)
    ws = tmp_path / "task-ws"
    ws.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
    ledger_dir = tmp_path / "fresh-ledger"
    monkeypatch.setenv("F2_AUTHZ_LEDGER_DIR", str(ledger_dir))
    provision_record_env(
        tmp_path, monkeypatch, stage="CONF-001", ops=("confirmatory_run",),
        binding={"experiment_id": "experiment_j", "protocol_version": "f2_v1",
                 "arm": "X"})
    with pytest.raises(ReadinessGateError):
        orch.run_f2_arm(
            arm="X", manifest_dir=tmp_path, worker_script=tmp_path / "w.py",
            repo_root=tmp_path, render_t=lambda: (_ for _ in ()).throw(
                AssertionError("render must not run")),
            lesson_l_id="l", lesson_l_hash="h", task_id="t", execute=True,
            stage="CONF-001", operation="confirmatory_run",
            readiness=None, rollout_id="roll-fresh")
    # Readiness failed BEFORE the consume point: the fresh ledger has no
    # consumption, so the grant was not burned by a readiness failure.
    assert not list(ledger_dir.glob("*.json"))


def _last_json_line(text: str) -> dict:
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise AssertionError(f"no JSON receipt in output: {text[:300]!r}")


def test_worker_execute_without_parent_binding_env_denied_before_workspace(
        tmp_path, monkeypatch, capsys):
    from qwen_train import f2_arm_worker as W

    artifact = type("Artifact", (), {"arm": "X", "experiment_id": "experiment_j",
                                     "protocol_version": "f2_v1",
                                     "task_readiness": {}, "task_id": "t"})()
    monkeypatch.setattr(W, "load_manifest", lambda _p: artifact)
    monkeypatch.setattr(W, "verify_manifest", lambda _a: None)
    monkeypatch.setattr(W, "install_replay_state", lambda *a: None)
    monkeypatch.setattr(W, "get_delivery_artifact", lambda: "block")
    import qwen_train.arm_workspace as aw

    touched = []
    monkeypatch.setattr(aw, "resolve_required_workspace_root",
                        lambda **k: touched.append("ws"))
    rc = W.run_worker([
        "--manifest", str(tmp_path / "m.json"), "--arm", "X",
        "--execute", "--authorization-stage", "CONF-001",
        "--authorization-operation", "confirmatory_run",
        "--instance-id", "i", "--task-id", "t",
    ])
    receipt = _last_json_line(capsys.readouterr().err)
    assert rc != 0
    assert receipt["error_type"] == "AuthorizationError"
    assert "parent orchestrator" in receipt["error"]
    assert touched == []  # denied BEFORE workspace preparation


def test_worker_expl_refused_before_workspace(tmp_path, monkeypatch, capsys):
    from qwen_train import f2_arm_worker as W

    artifact = type("Artifact", (), {"arm": "X", "experiment_id": "experiment_j",
                                     "protocol_version": "f2_v1",
                                     "task_readiness": {}, "task_id": "t"})()
    monkeypatch.setattr(W, "load_manifest", lambda _p: artifact)
    monkeypatch.setattr(W, "verify_manifest", lambda _a: None)
    monkeypatch.setattr(W, "install_replay_state", lambda *a: None)
    monkeypatch.setattr(W, "get_delivery_artifact", lambda: "block")
    import qwen_train.arm_workspace as aw

    touched = []
    monkeypatch.setattr(aw, "resolve_required_workspace_root",
                        lambda **k: touched.append("ws"))
    rc = W.run_worker([
        "--manifest", str(tmp_path / "m.json"), "--arm", "X",
        "--execute", "--authorization-stage", "EXPL-001",
        "--authorization-operation", "exploratory_run",
        "--instance-id", "i",
    ])
    receipt = _last_json_line(capsys.readouterr().err)
    assert rc != 0
    assert receipt["error_type"] == "AuthorizationError"
    assert "exploratory" in receipt["error"]
    assert touched == []


def test_worker_manifest_disagreeing_with_parent_binding_denied(
        tmp_path, monkeypatch, capsys):
    """Parent binding says experiment_j; the child's verified manifest says
    something else => the child refuses (parent's word and manifest must agree)."""
    from qwen_train import f2_arm_worker as W

    provision_parent_receipt(tmp_path, monkeypatch)
    artifact = type("Artifact", (), {"arm": "X", "experiment_id": "EVIL",
                                     "protocol_version": "f2_v1",
                                     "task_readiness": {}, "task_id": "t"})()
    monkeypatch.setattr(W, "load_manifest", lambda _p: artifact)
    monkeypatch.setattr(W, "verify_manifest", lambda _a: None)
    monkeypatch.setattr(W, "install_replay_state", lambda *a: None)
    monkeypatch.setattr(W, "get_delivery_artifact", lambda: "block")
    rc = W.run_worker([
        "--manifest", str(tmp_path / "m.json"), "--arm", "X",
        "--execute", "--authorization-stage", "CONF-001",
        "--authorization-operation", "confirmatory_run",
        "--instance-id", "i",
    ])
    receipt = _last_json_line(capsys.readouterr().err)
    assert rc != 0
    assert receipt["error_type"] == "AuthorizationError"
    assert "disagrees with the verified manifest" in receipt["error"]


# ===========================================================================
# Meta-tests: test fixtures cannot become production defaults
# ===========================================================================

def test_default_ledger_root_is_gitignored_runtime_state():
    assert DEFAULT_LEDGER_ROOT.parent.name == "data"
    gitignore = (_REPO_ROOT / ".gitignore").read_text("utf-8")
    assert "data/" in gitignore
    assert not DEFAULT_LEDGER_ROOT.exists(), (
        "the production ledger must not exist in a checkout (no test may have "
        "written it)")


def test_no_production_trust_root_is_provisioned_by_tests():
    prod_root = _REPO_ROOT / "qwen_train" / "authorization" / "trust_root.json"
    assert not prod_root.exists(), (
        "a production trust root appeared; the owner ceremony must provision "
        "it, never a test")


def test_testkit_never_references_production_paths():
    kit = (Path(__file__).parent / "f2_authz_testkit.py").read_text("utf-8")
    assert "DEFAULT_LEDGER_ROOT" not in kit
    assert "DEFAULT_TRUST_ROOT" not in kit


def test_protected_execution_modules_never_reference_test_ledger_defaults():
    """Production boundaries must take the ledger from env/constructor only;
    no production module may hardcode a test directory."""
    for mod in ("f2_arm_orchestrator.py", "f2_arm_worker.py",
                "f2_execution_adapter.py", "f2_registration.py"):
        src = (_REPO_ROOT / "qwen_train" / mod).read_text("utf-8")
        assert "F2_AUTHZ_LEDGER_DIR" not in src or "AuthorizationLedger(None)" in src


def test_verify_and_consume_happy_and_replay(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    binding = {"experiment_id": "experiment_j", "protocol_version": "f2_v1",
               "arm": "X", "rollout_id": "r9"}
    ctx = provision_record_env(
        tmp_path, monkeypatch, stage="CONF-001", ops=("confirmatory_run",),
        binding=binding)
    verified, entry = A.verify_and_consume(
        stage="CONF-001", operation="confirmatory_run",
        binding=binding, record_path=ctx["record_path"],
        trust_root=ctx["trust_root"])
    assert entry["record_digest"] == verified.digest
    # 1. exact replay denied (single-use)
    _expect_deny(lambda: A.verify_and_consume(
        stage="CONF-001", operation="confirmatory_run",
        binding=binding, record_path=ctx["record_path"],
        trust_root=ctx["trust_root"]))
    # 2. a record that PINS rollout r9 can never authorise a different rollout
    other = dict(binding, rollout_id="r10")
    _expect_deny(lambda: A.verify_and_consume(
        stage="CONF-001", operation="confirmatory_run",
        binding=other, record_path=ctx["record_path"],
        trust_root=ctx["trust_root"]))
    # 3. an UNPINNED record (no rollout_id bound) may authorise a distinct
    #    rollout - a separate execution unit with its own single-use entry.
    rec2 = signed_record("CONF-001", ("confirmatory_run",),
                         experiment_id="experiment_j", protocol_version="f2_v1")
    p2 = write_record(tmp_path, rec2, ctx["priv"], name="auth_unpinned.json")
    v2, e2 = A.verify_and_consume(
        stage="CONF-001", operation="confirmatory_run",
        binding=dict(binding, rollout_id="r10"), record_path=p2,
        trust_root=ctx["trust_root"])
    assert e2["consumption_id"] != entry["consumption_id"]
    assert v2.digest != verified.digest
