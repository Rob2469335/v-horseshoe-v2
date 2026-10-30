"""Focused tests for the owner-operated signer (qwen_train.f2_s8_sign_grant).

All keys, trust roots, repositories, specs and records here are ephemeral and
live only inside the test's tmp_path.  No production key is read, no grant is
consumed, no ledger or budget state is touched, and nothing is written to
``F2_AUTHZ`` or ``F2_EVIDENCE``.

The signing contract is interactive: every signing test supplies a fake TTY
whose typed line either matches the record digest prefix (success) or does not
(refusal).  Non-TTY refusal is tested explicitly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import uuid

import pytest

from qwen_train import f2_authorization as A
from qwen_train import f2_s8_sign_grant as S
from tests.f2_authz_testkit import keypair, write_trust_root

KID = "f2-s8-owner-v1"
MANIFEST = "a" * 64
REGISTRATION = "b" * 64
POOL = "c" * 64
SELECTION = "d" * 64
REG_MANIFEST = "e" * 64
SPEC_SCHEMA = "f2-expected-grant/v1"

#: Captured at import time, before the suite's autouse Popen mock is installed.
_REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def _real_git_subprocess(monkeypatch):
    """Allow the REAL git binary for this module only.

    ``tests/conftest.py`` installs a global ``subprocess.Popen`` mock so no test
    spawns servers.  These tests need a genuine throw-away git repository to
    prove provenance against real commit content, so Popen is restored here and
    reverted automatically by ``monkeypatch``.
    """
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)



class _TTY:
    """Minimal stdin stand-in that reports itself as an interactive terminal."""

    def __init__(self, text: str):
        self._text = text

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        return self._text


class _Pipe:
    """Minimal stdin stand-in for a non-interactive pipe."""

    def isatty(self) -> bool:
        return False

    def readline(self) -> str:  # pragma: no cover - must never be reached
        return ""


def _runtime_selection_digest(task_ids) -> str:
    """The RUNTIME's canonicalisation, duplicated here on purpose.

    Deliberately NOT ``S.selection_digest``: a test that calls the signer's own
    helper cannot detect a wrong canonicalisation in the signer.  This is the
    exact expression from
    ``f2_s8_probe_driver.compute_ordered_probe_candidates``.
    """
    return hashlib.sha256(
        json.dumps(list(task_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


TASKS = [
    "openai__openai-agents-python-1619",
    "a2aproject__a2a-python-443",
]
SELECTION = _runtime_selection_digest(TASKS)


def _record(**over):
    """The approval substance (no revision: that is bound to live git)."""
    rec = {
        "id": "s8-test-probe",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["build_probe"],
        "approver": KID,
        "not_before": "2026-01-01T00:00:00+00:00",
        "not_after": "2027-01-01T00:00:00+00:00",
        "protocol_digest": "f2_experiment_j_v1",
        "candidate_manifest_digest": MANIFEST,
        "registration_sha256": REGISTRATION,
        "registration_manifest_sha256": REG_MANIFEST,
        "probe_pool_digest": POOL,
        "probe_selection_digest": SELECTION,
        "ordering_policy": "REGISTRATION_ORDER_UNBUILT",
        "retention_policy": "RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE",
        "allowed_identities": ["f2builder"],
        "network_policy": "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        "selected_task_ids": list(TASKS),
        "target_compatible": 2,
        "target_credited": 0,
        "awards_credits": False,
        "budget": {
            "max_executions": 1,
            "max_total_tasks": 15,
            "batch_limit": 15,
            "max_retries": 0,
        },
    }
    rec.update(over)
    return rec


def _write_pem(tmp_path, priv, name="key.pem"):
    from cryptography.hazmat.primitives import serialization

    p = tmp_path / name
    p.write_bytes(
        priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return p


def _write_record(tmp_path, record, name="unsigned.json"):
    p = tmp_path / name
    p.write_text(json.dumps(record), "utf-8")
    return p


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _git(repo, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _spec_doc(record) -> dict:
    approved = {k: v for k, v in record.items() if k != "repository_revision"}
    return {
        "schema": SPEC_SCHEMA,
        "approved_record_sha256": hashlib.sha256(A.canonical_bytes(approved)).hexdigest(),
        "approved_record": approved,
    }


def _commit_spec(repo, record, name="expected_grant.json"):
    p = repo / name
    p.write_text(json.dumps(_spec_doc(record)), "utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", "owner-approved expected grant")
    return p


def _head_after_marker(repo, name="marker.txt") -> str:
    """Advance HEAD one commit past the spec so a spec can be checked at an
    ANCESTOR revision without the revision field hashing itself."""
    (repo / name).write_text(f"head marker {uuid.uuid4().hex}\n", "utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", "advance head past the approved spec")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "signer-test")

    priv, pub = keypair()
    root = write_trust_root(tmp_path, {KID: (priv, pub)}, epochs={KID: 1})

    c = {
        "tmp": tmp_path,
        "repo": repo,
        "priv": priv,
        "root": root,
        "pin": _sha(root),
        "audit": tmp_path / "audit.jsonl",
    }
    # Independently computed expected bindings (in a real run these come from
    # the gate's own artifact computation, never from the record).
    c["bindings"] = tmp_path / "bindings.json"
    c["bindings"].write_text(json.dumps({
        "registration_manifest_sha256": REG_MANIFEST,
        "probe_pool_digest": POOL,
        "probe_selection_digest": SELECTION,
        "ordering_policy": "REGISTRATION_ORDER_UNBUILT",
        "allowed_identities": ["f2builder"],
        "network_policy": "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        "retention_policy": "RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE",
        "awards_credits": False,
    }), "utf-8")

    c["record"] = _record()
    c["spec"] = _commit_spec(repo, c["record"])
    c["spec_rev"] = _git(repo, "rev-parse", "HEAD")
    c["rev"] = _head_after_marker(repo)
    c["record"]["repository_revision"] = c["rev"]
    c["spec_pin"] = _sha(c["spec"])
    return c


def repin(c, record) -> None:
    """Commit a new spec for ``record`` and rebind the case to the new HEAD."""
    c["record"] = dict(record)
    c["spec"] = _commit_spec(c["repo"], c["record"])
    c["spec_rev"] = _git(c["repo"], "rev-parse", "HEAD")
    c["rev"] = _head_after_marker(c["repo"])
    c["record"]["repository_revision"] = c["rev"]
    c["spec_pin"] = _sha(c["spec"])


def _confirm(monkeypatch, record) -> None:
    monkeypatch.setattr(sys, "stdin", _TTY(A.record_digest(record)[:12] + "\n"))


def _argv(c, *, record=None, record_path=None, out=None, key=None,
          verify_only=False, **over):
    rec = record if record is not None else c["record"]
    argv = [
        "--record", str(record_path or _write_record(c["tmp"], rec)),
        "--trust-root", str(c["root"]),
        "--trust-root-sha256", over.pop("pin", c["pin"]),
        "--repo", str(over.pop("repo", c["repo"])),
        "--expect-revision", over.pop("rev", c["rev"]),
        "--expect-manifest-sha256", over.pop("expect_manifest_sha256", MANIFEST),
        "--expect-registration-sha256", over.pop("expect_registration_sha256", REGISTRATION),
        "--expected-grant", str(over.pop("spec", c["spec"])),
        "--expected-grant-sha256", over.pop("spec_pin", c["spec_pin"]),
        "--audit-log", str(over.pop("audit", c["audit"])),
        "--stage", over.pop("stage", "S8-001"),
        "--operation", over.pop("operation", "build_probe"),
    ]
    if verify_only:
        argv.append("--verify-only")
        bindings = over.pop("bindings", c.get("bindings"))
        if bindings is not None:
            argv += ["--expect-bindings", str(bindings)]
    else:
        argv += ["--out", str(out or c["tmp"] / "signed.json"),
                 "--key", str(key or _write_pem(c["tmp"], c["priv"]))]
    return argv


def _strip(argv, *flags):
    out = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item in flags:
            skip = True
            continue
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Positive paths
# ---------------------------------------------------------------------------

def test_sign_roundtrip_then_verify(case, monkeypatch):
    c = case
    out = c["tmp"] / "signed.json"
    _confirm(monkeypatch, c["record"])

    assert S.main(_argv(c, out=out)) == 0
    assert out.is_file()
    env = json.loads(out.read_text("utf-8"))
    assert env["signature"]["alg"] == "ed25519"
    assert env["signature"]["key_id"] == KID
    assert env["signature"]["signer"] == KID
    assert env["signature"]["key_epoch"] == 1
    # signature is over canonical_bytes(record)
    c["priv"].public_key().verify(
        base64.b64decode(env["signature"]["sig"]), A.canonical_bytes(env["record"])
    )

    # one audit line, bound to the record, the signer and the revision
    lines = c["audit"].read_text("utf-8").splitlines()
    assert len(lines) == 1
    audit = json.loads(lines[0])
    assert audit["record_id"] == c["record"]["id"]
    assert audit["record_digest"] == A.record_digest(c["record"])
    assert audit["approver"] == KID
    assert audit["repository_revision"] == c["rev"]
    assert audit["expected_grant_sha256"] == c["spec_pin"]

    # verify-only accepts it and does NOT consume
    assert S.main(_argv(c, record_path=out, verify_only=True)) == 0
    assert len(c["audit"].read_text("utf-8").splitlines()) == 1


def test_preflight_mode_signs_nothing(case, monkeypatch):
    c = case
    argv = _strip(
        _argv(c, out=c["tmp"] / "signed.json", key=_write_pem(c["tmp"], c["priv"])),
        "--out", "--key",
    ) + ["--preflight"]
    monkeypatch.setattr(sys, "stdin", _Pipe())  # preflight needs no TTY
    assert S.main(argv) == 0
    assert not (c["tmp"] / "signed.json").exists()
    assert not c["audit"].exists()


def test_preflight_mode_key_match_and_mismatch(case, monkeypatch):
    c = case
    good = _write_pem(c["tmp"], c["priv"], name="good.pem")
    other_priv, _ = keypair()
    bad = _write_pem(c["tmp"], other_priv, name="bad.pem")
    base = _strip(_argv(c, key=good), "--out", "--key")
    monkeypatch.setattr(sys, "stdin", _Pipe())
    assert S.main(base + ["--preflight", "--key", str(good)]) == 0
    with pytest.raises(SystemExit, match="does not match the trust-root public key"):
        S.main(base + ["--preflight", "--key", str(bad)])


def test_preflight_labels_a_clock_override_as_non_binding(case, monkeypatch, capsys):
    c = case
    argv = _strip(_argv(c), "--out", "--key") + [
        "--preflight", "--now", "2026-06-01T00:00:00+00:00",
    ]
    monkeypatch.setattr(sys, "stdin", _Pipe())
    assert S.main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["binding"] is False
    assert payload["clock_overridden"] is True


# ---------------------------------------------------------------------------
# Output / key / trust-root guards (pre-existing behaviour)
# ---------------------------------------------------------------------------

def test_refuses_existing_output(case, monkeypatch):
    c = case
    out = c["tmp"] / "signed.json"
    out.write_text("already here", "utf-8")
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        S.main(_argv(c, out=out))
    assert out.read_text("utf-8") == "already here"


def test_refuses_expired(case, monkeypatch):
    c = case
    expired = _record(
        not_before="2020-01-01T00:00:00+00:00",
        not_after="2021-01-01T00:00:00+00:00",
    )
    repin(c, expired)
    _confirm(monkeypatch, expired)
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="outside validity window"):
        S.main(_argv(c, out=out))
    assert not out.exists()


def test_refuses_future_window(case, monkeypatch):
    c = case
    future = _record(
        not_before="2030-01-01T00:00:00+00:00",
        not_after="2031-01-01T00:00:00+00:00",
    )
    repin(c, future)
    _confirm(monkeypatch, future)
    with pytest.raises(SystemExit, match="outside validity window"):
        S.main(_argv(c))


def test_refuses_empty_validity_window(case, monkeypatch):
    c = case
    inverted = _record(
        not_before="2026-12-01T00:00:00+00:00",
        not_after="2026-01-01T00:00:00+00:00",
    )
    repin(c, inverted)
    _confirm(monkeypatch, inverted)
    with pytest.raises(SystemExit, match="validity window is empty"):
        S.main(_argv(c))


def test_refuses_key_not_matching_trust_root(case, monkeypatch):
    c = case
    other_priv, _other_pub = keypair()  # a DIFFERENT key than the trust root
    other_key = _write_pem(c["tmp"], other_priv, name="other.pem")
    out = c["tmp"] / "signed.json"
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="does not match the trust-root public key"):
        S.main(_argv(c, out=out, key=other_key))
    # fail-closed: the key-match guard fires BEFORE any output is written
    assert not out.exists(), "no signed output must be produced when the key does not match the trust root"


def test_refuses_revoked_approver(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "signer-test")
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {KID: (priv, pub)}, revoked=(KID,), epochs={KID: 1})
    c = {"tmp": tmp_path, "repo": repo, "priv": priv, "root": root, "pin": _sha(root),
         "audit": tmp_path / "audit.jsonl"}
    c["record"] = _record()
    c["spec"] = _commit_spec(repo, c["record"])
    c["spec_rev"] = _git(repo, "rev-parse", "HEAD")
    c["rev"] = _head_after_marker(repo)
    c["record"]["repository_revision"] = c["rev"]
    c["spec_pin"] = _sha(c["spec"])
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="revoked"):
        S.main(_argv(c))


def test_verify_only_rejects_tampered_signature(case, monkeypatch):
    c = case
    out = c["tmp"] / "signed.json"
    _confirm(monkeypatch, c["record"])
    assert S.main(_argv(c, out=out)) == 0
    env = json.loads(out.read_text("utf-8"))
    raw = bytearray(base64.b64decode(env["signature"]["sig"]))
    raw[0] ^= 0xFF
    env["signature"]["sig"] = base64.b64encode(bytes(raw)).decode()
    tampered = c["tmp"] / "tampered.json"
    tampered.write_text(json.dumps(env), "utf-8")
    with pytest.raises(A.AuthorizationError):
        S.main(_argv(c, record_path=tampered, verify_only=True))


def test_missing_pin_fails_closed(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    argv = _strip(_argv(c), "--trust-root-sha256")
    with pytest.raises(SystemExit, match="trust root rejected"):
        S.main(argv)


def test_verification_rejects_wrong_independent_binding(case, monkeypatch):
    """An expected value that disagrees with the signed record must be refused."""
    c = case
    out = c["tmp"] / "signed.json"
    _confirm(monkeypatch, c["record"])
    assert S.main(_argv(c, out=out)) == 0
    wrong = c["tmp"] / "bindings_wrong.json"
    payload = json.loads(c["bindings"].read_text("utf-8"))
    payload["probe_selection_digest"] = "9" * 64
    wrong.write_text(json.dumps(payload), "utf-8")
    with pytest.raises(A.AuthorizationError, match="probe_selection_digest mismatch"):
        S.main(_argv(c, record_path=out, verify_only=True, bindings=wrong))


def test_signing_and_verification_never_consume_or_reserve(case, monkeypatch):
    """Signing and --verify-only must never consume a grant or reserve budget."""
    from qwen_train import f2_authz_budget as B
    from qwen_train import f2_authz_ledger as L

    def _boom(*_a, **_k):
        raise AssertionError("grant consumption/reservation must not occur during signing/verification")

    monkeypatch.setattr(L.AuthorizationLedger, "consume", _boom, raising=True)
    monkeypatch.setattr(B, "reserve", _boom, raising=True)

    c = case
    out = c["tmp"] / "signed.json"
    _confirm(monkeypatch, c["record"])
    assert S.main(_argv(c, out=out)) == 0
    assert S.main(_argv(c, record_path=out, verify_only=True)) == 0


def test_refuses_wrong_stage(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="stage mismatch"):
        S.main(_argv(c, stage="CONF-001"))


# ---------------------------------------------------------------------------
# Signer-hardening: mandatory arguments, real UTC, interactive confirmation
# ---------------------------------------------------------------------------

def test_signing_requires_repository(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="--repo is required when signing"):
        S.main(_strip(_argv(c), "--repo"))


def test_signing_requires_trust_root_pin(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="trust root rejected"):
        S.main(_strip(_argv(c), "--trust-root-sha256"))


def test_signing_requires_expected_grant(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="--expected-grant is required"):
        S.main(_strip(_argv(c), "--expected-grant"))


def test_signing_requires_expected_grant_pin(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="--expected-grant-sha256 is required"):
        S.main(_strip(_argv(c), "--expected-grant-sha256"))


def test_signing_requires_audit_log(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="--audit-log is required"):
        S.main(_strip(_argv(c), "--audit-log"))


def test_audit_log_inside_worktree_is_refused(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="outside the signed worktree"):
        S.main(_argv(c, audit=c["repo"] / "audit.jsonl"))


def test_now_override_is_rejected_when_signing(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="preflight-only diagnostic clock"):
        S.main(_argv(c) + ["--now", "2026-06-01T00:00:00+00:00"])


def test_verification_requires_repository(case, monkeypatch):
    c = case
    out = c["tmp"] / "signed.json"
    _confirm(monkeypatch, c["record"])
    assert S.main(_argv(c, out=out)) == 0
    with pytest.raises(SystemExit, match="--repo is required for --verify-only"):
        S.main(_strip(_argv(c, record_path=out, verify_only=True), "--repo"))


def test_non_tty_signing_is_refused(case, monkeypatch):
    c = case
    monkeypatch.setattr(sys, "stdin", _Pipe())
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="interactive terminal"):
        S.main(_argv(c, out=out))
    assert not out.exists()


def test_wrong_typed_confirmation_is_refused(case, monkeypatch):
    c = case
    monkeypatch.setattr(sys, "stdin", _TTY("000000000000\n"))
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="confirmation did not match"):
        S.main(_argv(c, out=out))
    assert not out.exists()
    assert not c["audit"].exists()


def test_confirmation_precedes_key_loading(case, monkeypatch):
    """A refused confirmation must not load private key material."""
    c = case
    monkeypatch.setattr(sys, "stdin", _TTY("deadbeef0000\n"))

    def _boom(*_a, **_k):
        raise AssertionError("private key must not be loaded before confirmation")

    monkeypatch.setattr(S, "_load_signing_key", _boom)
    with pytest.raises(SystemExit, match="confirmation did not match"):
        S.main(_argv(c))


# ---------------------------------------------------------------------------
# Expected-grant provenance and content equality
# ---------------------------------------------------------------------------

def test_expected_grant_outside_repo_cannot_establish_provenance(case, monkeypatch):
    c = case
    outside = c["tmp"] / "spec_copy.json"
    outside.write_text(c["spec"].read_text("utf-8"), "utf-8")
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="outside the bound repository"):
        S.main(_argv(c, spec=outside, spec_pin=_sha(outside)))


def test_untracked_expected_grant_is_refused(case, monkeypatch):
    c = case
    untracked = c["repo"] / "untracked_spec.json"
    untracked.write_text(json.dumps(_spec_doc(c["record"])), "utf-8")
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="not tracked"):
        S.main(_argv(c, spec=untracked, spec_pin=_sha(untracked)))


def test_expected_grant_pin_mismatch_is_refused(case, monkeypatch):
    c = case
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="digest mismatch"):
        S.main(_argv(c, spec_pin="0" * 64))


def test_internally_inconsistent_spec_is_refused(case, monkeypatch):
    c = case
    doc = json.loads(c["spec"].read_text("utf-8"))
    doc["approved_record"]["probe_selection_digest"] = "f" * 64
    bad = c["repo"] / "tampered_spec.json"
    bad.write_text(json.dumps(doc), "utf-8")
    _git(c["repo"], "add", "tampered_spec.json")
    _git(c["repo"], "commit", "-q", "-m", "tampered spec")
    c["rev"] = _git(c["repo"], "rev-parse", "HEAD")
    rec = dict(c["record"], repository_revision=c["rev"])
    _confirm(monkeypatch, rec)
    with pytest.raises(SystemExit, match="internally inconsistent"):
        S.main(_argv(c, record=rec, spec=bad, spec_pin=_sha(bad)))


def test_spec_drift_from_bound_revision_is_refused(case, monkeypatch):
    c = case
    # Same file, modified after commit with a CONSISTENT self-digest, so the
    # only remaining failure is that the bytes are not the committed ones.
    doc = json.loads(c["spec"].read_text("utf-8"))
    doc["approved_record"]["budget"]["max_total_tasks"] = 99
    doc["approved_record_sha256"] = hashlib.sha256(
        A.canonical_bytes(doc["approved_record"])).hexdigest()
    c["spec"].write_text(json.dumps(doc), "utf-8")
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="differs from the bound revision"):
        S.main(_argv(c, spec_pin=_sha(c["spec"])))


def test_dirty_repository_is_refused(case, monkeypatch):
    c = case
    (c["repo"] / "junk.txt").write_text("x", "utf-8")
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="dirty"):
        S.main(_argv(c))


def test_wrong_revision_is_refused(case, monkeypatch):
    c = case
    # The spec IS present and byte-identical at the ancestor revision, so the
    # refusal comes from the revision binding, not from provenance.
    _confirm(monkeypatch, c["record"])
    with pytest.raises(SystemExit, match="repository_revision mismatch"):
        S.main(_argv(c, rev=c["spec_rev"]))


# ---------------------------------------------------------------------------
# Complete content validation: every field must equal owner review
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("probe_pool_digest", "1" * 64),
    ("candidate_manifest_digest", "3" * 64),
    ("registration_sha256", "4" * 64),
    ("registration_manifest_sha256", "5" * 64),
    ("budget", {"max_executions": 1, "max_total_tasks": 99,
                "batch_limit": 99, "max_retries": 5}),
    ("network_policy", "OFFLINE_WHEELHOUSE_ONLY"),
    ("allowed_identities", ["someoneelse"]),
    ("ordering_policy", "ASCII_SORTED_UNBUILT"),
    ("retention_policy", "DELETE_ALL_AFTER_CLASSIFICATION"),
])
def test_record_differing_from_approved_spec_is_refused(case, monkeypatch, field, value):
    c = case
    mutated = dict(c["record"], **{field: value})
    _confirm(monkeypatch, mutated)  # correct digest for the MUTATED record
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="differs from the owner-approved spec"):
        S.main(_argv(c, record=mutated, out=out))
    assert not out.exists()


def test_unapproved_extra_field_is_refused(case, monkeypatch):
    c = case
    mutated = dict(c["record"], extra_scope="unbounded")
    _confirm(monkeypatch, mutated)
    with pytest.raises(SystemExit, match="unapproved fields"):
        S.main(_argv(c, record=mutated))


@pytest.mark.parametrize("field,value,match", [
    ("probe_pool_digest", "not-hex", "64-hex probe_pool_digest"),
    ("selected_task_ids", ["t", "t"], "duplicates"),
    ("selected_task_ids", [], "non-empty list"),
    ("network_policy", "UNRESTRICTED", "unsupported network_policy"),
    ("allowed_identities", ["*"], "wildcard"),
    ("allowed_identities", ["F2Builder"], "lowercase"),
    ("budget", {"max_executions": 1, "max_total_tasks": 15,
                "batch_limit": 16, "max_retries": 0}, "batch_limit"),
])
def test_unsignable_record_content_is_refused(case, monkeypatch, field, value, match):
    c = case
    invalid = dict(c["record"], **{field: value})
    repin(c, invalid)
    _confirm(monkeypatch, invalid)
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match=match):
        S.main(_argv(c, out=out))
    assert not out.exists()


def test_probe_grant_cannot_award_credits(case, monkeypatch):
    c = case
    invalid = dict(c["record"], target_credited=5, awards_credits=True)
    repin(c, invalid)
    _confirm(monkeypatch, invalid)
    with pytest.raises(SystemExit, match="PROBE_CREDITS_FORBIDDEN"):
        S.main(_argv(c))


def test_scope_escalation_is_refused(case, monkeypatch):
    c = case
    invalid = dict(c["record"], operations=["build_probe", "confirmatory_run"])
    repin(c, invalid)
    _confirm(monkeypatch, invalid)
    with pytest.raises(SystemExit, match="scope escalation"):
        S.main(_argv(c))


# ---------------------------------------------------------------------------
# Approval integrity: the displayed task list IS the digested selection
# ---------------------------------------------------------------------------

def test_selection_helper_matches_the_runtime_function():
    """The signer's canonicalisation must equal the runtime's, byte for byte."""
    from qwen_train.f2_s8_probe_driver import compute_ordered_probe_candidates

    compat = {tid: "UNKNOWN" for tid in TASKS}
    _pool, selected, runtime_digest = compute_ordered_probe_candidates(
        registration_order=list(TASKS), compat_classes=compat, max_tasks=15,
    )
    assert selected == TASKS
    assert S.selection_digest(selected) == runtime_digest
    assert _runtime_selection_digest(TASKS) == runtime_digest


def test_canonical_summary_shows_the_verified_bound_selection():
    text = S._canonical_summary(_record())
    for tid in TASKS:
        assert tid in text
    assert _runtime_selection_digest(TASKS) in text
    assert "verified" in text


def test_list_changed_but_digest_kept_is_refused():
    rec = _record(selected_task_ids=["task-evil"])
    with pytest.raises(SystemExit, match="does not match probe_selection_digest"):
        S._validate_record_structure(rec)


def test_digest_changed_but_list_kept_is_refused():
    rec = _record(probe_selection_digest=_runtime_selection_digest(["task-evil"]))
    with pytest.raises(SystemExit, match="does not match probe_selection_digest"):
        S._validate_record_structure(rec)


def test_reversed_order_is_refused():
    rec = _record(selected_task_ids=list(reversed(TASKS)))
    with pytest.raises(SystemExit, match="does not match probe_selection_digest"):
        S._validate_record_structure(rec)


def test_reversed_order_with_its_own_digest_still_differs_from_the_original():
    """Ordering is significant: the reversed digest is a different selection."""
    reversed_ids = list(reversed(TASKS))
    assert _runtime_selection_digest(reversed_ids) != _runtime_selection_digest(TASKS)
    rec = _record(selected_task_ids=reversed_ids,
                  probe_selection_digest=_runtime_selection_digest(reversed_ids))
    # The record is internally consistent; it must differ from the committed spec.
    assert S.selection_digest(rec["selected_task_ids"]) == rec["probe_selection_digest"]


def test_missing_list_with_a_digest_is_refused():
    rec = _record()
    rec.pop("selected_task_ids")
    with pytest.raises(SystemExit, match="no selected_task_ids"):
        S._validate_record_structure(rec)


def test_list_without_a_digest_is_refused():
    rec = _record(
        operations=["base_gold_production"],
        target_credited=300,
        awards_credits=True,
        candidate_pool_digest="9" * 64,
    )
    rec.pop("probe_selection_digest")
    with pytest.raises(SystemExit, match="without probe_selection_digest"):
        S._validate_record_structure(rec)


def test_benign_list_with_a_digest_for_other_tasks_cannot_be_signed(case):
    """The exploit Claude described: a harmless displayed list + a different digest."""
    c = case
    exploit = dict(c["record"],
                   selected_task_ids=list(TASKS),
                   probe_selection_digest=_runtime_selection_digest(["task-evil"]))
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="does not match probe_selection_digest"):
        S.main(_argv(c, record=exploit, out=out))
    assert not out.exists()
    assert not c["audit"].exists()


def test_selection_binding_is_checked_before_the_confirmation_prompt(case, monkeypatch):
    """A refused selection must never reach the TTY prompt (or the key)."""
    c = case
    monkeypatch.setattr(sys, "stdin", _Pipe())  # non-TTY: would fail differently
    exploit = dict(c["record"], selected_task_ids=["task-evil"])
    with pytest.raises(SystemExit, match="does not match probe_selection_digest"):
        S.main(_argv(c, record=exploit))


# ---------------------------------------------------------------------------
# Signer validation must not accept records the runtime verifier rejects
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,over,match", [
    ("duplicate operations", {"operations": ["build_probe", "build_probe"]},
     "duplicate operations"),
    ("non-string operation", {"operations": ["build_probe", 7]},
     "list of strings"),
    ("non-string id", {"id": 123}, "id must be a non-empty string"),
    ("empty id", {"id": ""}, "id must be a non-empty string"),
    ("None id", {"id": None}, "id must be a non-empty string"),
])
def test_record_shapes_the_runtime_rejects_are_refused(label, over, match):
    rec = _record(**over)
    with pytest.raises(SystemExit, match=match):
        S._validate_record_structure(rec)


def test_duplicate_operations_cannot_be_signed(case):
    c = case
    out = c["tmp"] / "signed.json"
    bad = dict(c["record"], operations=["build_probe", "build_probe"])
    with pytest.raises(SystemExit, match="duplicate operations"):
        S.main(_argv(c, record=bad, out=out))
    assert not out.exists()


# ---------------------------------------------------------------------------
# v1 records: refuse with a reason, never crash or sign an unexecutable grant
# ---------------------------------------------------------------------------

def test_v1_probe_grant_is_refused_with_a_reason():
    """Regression: ``budget_of`` returns None for v1 and the signer must not
    subscript it; the refusal must be an S8-specific reason, not a TypeError."""
    rec = _record(schema="f2-authorization/v1")
    for key in ("budget", "candidate_pool_digest", "registration_manifest_sha256"):
        rec.pop(key, None)
    with pytest.raises(SystemExit, match="must use f2-authorization/v2"):
        S._validate_record_structure(rec)


def test_v1_record_with_v2_only_fields_is_refused():
    rec = _record(schema="f2-authorization/v1")
    with pytest.raises(SystemExit, match="v1 authorization record must not declare"):
        S._validate_record_structure(rec)



# ---------------------------------------------------------------------------
# Revision input is validated BEFORE any Git call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("make_rev", [
    lambda rev: rev[:12],
    lambda rev: rev.upper(),
    lambda rev: "HEAD",
    lambda rev: " " + rev,
    lambda rev: rev + "0",
])
def test_malformed_expect_revision_is_refused(case, monkeypatch, make_rev):
    c = case
    _confirm(monkeypatch, c["record"])
    bad_rev = make_rev(c["rev"])
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="full 40-character lowercase commit id"):
        S.main(_argv(c, out=out, rev=bad_rev))
    assert not out.exists()


def test_option_like_revision_reaches_validation_and_is_refused(case, monkeypatch):
    """``--expect-revision=--output=...`` must be rejected by our check.

    The separate-argument form is consumed by argparse as an option, so the
    equals form is used here to prove the value is validated rather than handed
    to Git.
    """
    c = case
    _confirm(monkeypatch, c["record"])
    out = c["tmp"] / "signed.json"
    argv = _argv(c, out=out)
    idx = argv.index("--expect-revision")
    argv[idx:idx + 2] = ["--expect-revision=--output=C:/tmp/evil"]
    with pytest.raises(SystemExit, match="full 40-character lowercase commit id"):
        S.main(argv)
    assert not out.exists()


def test_revision_validation_does_not_rely_on_git_failure():
    """The helper itself refuses before Git is invoked."""
    for value in ("", "HEAD", "abc1234", "A" * 40, "0" * 39, "  " + "0" * 40):
        with pytest.raises(SystemExit, match="full 40-character lowercase commit id"):
            S._require_full_revision(value, "--expect-revision")
    assert S._require_full_revision("a" * 40, "--expect-revision") == "a" * 40


# ---------------------------------------------------------------------------
# Documented audit-log limitation: envelope first, audit append second
# ---------------------------------------------------------------------------

def test_audit_append_failure_leaves_a_signed_artifact(case, monkeypatch):
    """Pins the documented limitation instead of claiming atomicity.

    If this behaviour ever changes (audit written first, or the output rolled
    back), this test must be updated deliberately.
    """
    c = case
    _confirm(monkeypatch, c["record"])
    bad_audit = c["tmp"] / "missing_dir" / "audit.jsonl"
    out = c["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="parent directory does not exist"):
        S.main(_argv(c, out=out, audit=bad_audit))
    assert out.is_file(), (
        "the envelope is written before the audit append; a failed append leaves "
        "a signed artifact that the operator must treat as unaudited"
    )
    assert not bad_audit.exists()

