"""Shared TEST-ONLY helpers for the F2 authorization/binding/ledger tests.

Everything here is an isolated fixture: ephemeral ed25519 keys, temporary trust
roots, temporary ledgers. Nothing in this module can become a production
default: it never writes ``qwen_train/authorization/``, never uses the
production ledger path, and every key it generates exists only inside the
calling test's ``tmp_path``.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import Any, Mapping

from qwen_train import f2_authorization as A
from qwen_train.f2_authz_ledger import AuthorizationLedger, execution_binding_digest

#: Fixed, obviously-fake repository revision used by tests that must not
#: depend on the live working tree.
TEST_REPO_REVISION = "e" * 40


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, base64.b64encode(pub).decode()


def write_trust_root(
    tmp: Path,
    signers: Mapping[str, tuple[Any, str]],
    *,
    revoked: tuple[str, ...] = (),
    epochs: Mapping[str, int] | None = None,
    name: str = "trust_root.json",
) -> Path:
    """signers maps kid -> (unused_priv, pub_b64). ``epochs`` adds key epochs."""
    body: dict[str, Any] = {"signers": {}}
    epochs = dict(epochs or {})
    for kid, (_priv, pub) in signers.items():
        if kid in epochs:
            body["signers"][kid] = {"public_key": pub, "epoch": epochs[kid]}
        else:
            body["signers"][kid] = pub
    if revoked:
        body["revoked_key_ids"] = list(revoked)
    p = tmp / name
    p.write_text(json.dumps(body), "utf-8")
    return p


def signed_record(
    stage: str = "POP-001",
    ops: tuple[str, ...] = ("acquire",),
    *,
    repository_revision: str | None = TEST_REPO_REVISION,
    **over: Any,
) -> dict[str, Any]:
    """A schema-valid record with the bindings its operation requires."""
    t = now()
    rec: dict[str, Any] = {
        "id": "AUTH-TEST-1",
        "schema": A.SCHEMA,
        "stage": stage,
        "operations": list(ops),
        "approver": "owner",
        "not_before": (t - dt.timedelta(minutes=1)).isoformat(),
        "not_after": (t + dt.timedelta(days=1)).isoformat(),
        "protocol_digest": "proto-sha",
    }
    if repository_revision is not None:
        rec["repository_revision"] = repository_revision
    if (stage, ops[0]) in A.OPERATION_REQUIRED_BINDINGS:
        required = A.OPERATION_REQUIRED_BINDINGS[(stage, ops[0])]
        if "experiment_id" in required:
            rec.setdefault("experiment_id", "experiment_j")
        if "protocol_version" in required:
            rec.setdefault("protocol_version", "f2_v1")
        if "arm" in required:
            rec.setdefault("arm", "X")
        if "population_manifest_digest" in required:
            rec.setdefault("population_manifest_digest", "pop-manifest-sha")
    rec.update(over)
    return rec


def write_record(
    tmp: Path,
    record: dict[str, Any],
    priv,
    *,
    key_id: str = "owner",
    key_epoch: int | None = None,
    name: str = "auth.json",
) -> Path:
    sig = {
        "alg": "ed25519",
        "key_id": key_id,
        "signer": record.get("approver"),
        "sig": base64.b64encode(priv.sign(A.canonical_bytes(record))).decode(),
    }
    if key_epoch is not None:
        sig["key_epoch"] = key_epoch
    p = tmp / name
    p.write_text(json.dumps({"record": record, "signature": sig}), "utf-8")
    return p


def apply_repo_identity(monkeypatch, revision: str = TEST_REPO_REVISION, dirty: bool = False):
    """Pin repository_identity to a fixed value so tests never depend on the
    live working tree (the real git behaviour is covered separately in
    test_f2_authz_binding_and_replay.py)."""
    monkeypatch.setattr(
        A,
        "repository_identity",
        lambda root=None: {"revision": revision, "dirty": dirty},
    )


def provision_record_env(
    tmp: Path,
    monkeypatch,
    *,
    stage: str,
    ops: tuple[str, ...],
    binding: Mapping[str, Any] | None = None,
    ledger: bool = True,
    **record_over: Any,
) -> dict[str, Any]:
    """Ephemeral key + trust root + signed record + isolated env for one op."""
    priv, pub = keypair()
    root = write_trust_root(tmp, {"owner": (priv, pub)})
    rec = signed_record(stage, ops, **record_over)
    rec.update(dict(binding or {}))
    path = write_record(tmp, rec, priv)
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(root))
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(path))
    if ledger:
        monkeypatch.setenv("F2_AUTHZ_LEDGER_DIR", str(tmp / "ledger"))
    return {
        "priv": priv,
        "pub": pub,
        "trust_root": root,
        "record": rec,
        "record_path": path,
    }


def provision_parent_receipt(
    tmp: Path,
    monkeypatch,
    *,
    stage: str = "CONF-001",
    operation: str = "confirmatory_run",
    arm: str = "X",
    experiment_id: str = "experiment_j",
    protocol_version: str = "f2_v1",
    rollout_id: str = "roll-test-1",
) -> dict[str, Any]:
    """Provision a FULL valid parent-side consumption and its child env,
    exactly as ``run_f2_arm(execute=True)`` does, without spawning anything."""
    apply_repo_identity(monkeypatch)
    ctx = provision_record_env(
        tmp,
        monkeypatch,
        stage=stage,
        ops=(operation,),
        binding={
            "experiment_id": experiment_id,
            "protocol_version": protocol_version,
            "arm": arm,
            "rollout_id": rollout_id,
        },
    )
    verified, entry = A.verify_and_consume(
        stage=stage,
        operation=operation,
        binding={
            "experiment_id": experiment_id,
            "protocol_version": protocol_version,
            "arm": arm,
            "rollout_id": rollout_id,
        },
        record_path=ctx["record_path"],
        trust_root=ctx["trust_root"],
    )
    monkeypatch.setenv("SWARM_F2_AUTHZ_STAGE", stage)
    monkeypatch.setenv("SWARM_F2_AUTHZ_OPERATION", operation)
    monkeypatch.setenv("SWARM_F2_AUTHZ_BINDING", json.dumps(dict(verified.binding)))
    monkeypatch.setenv("SWARM_F2_AUTHZ_BINDING_DIGEST", verified.binding_digest)
    monkeypatch.setenv("SWARM_F2_AUTHZ_RECORD_DIGEST", verified.digest)
    monkeypatch.setenv("SWARM_F2_AUTHZ_CONSUMPTION_ID", str(entry["consumption_id"]))
    ctx.update(
        {
            "verified": verified,
            "entry": entry,
            "arm": arm,
            "experiment_id": experiment_id,
            "protocol_version": protocol_version,
            "rollout_id": rollout_id,
        }
    )
    return ctx


def consume_parent_receipt_from_env(
    monkeypatch,
    *,
    stage: str = "CONF-001",
    operation: str = "confirmatory_run",
    arm: str = "X",
    experiment_id: str = "experiment_j",
    protocol_version: str = "f2_v1",
    rollout_id: str = "roll-test-1",
) -> dict[str, Any]:
    """Consume whatever record/trust-root the environment already carries and
    publish the child binding env - compatible with module-level signing
    oracles that re-sign the env record (unlike ``provision_parent_receipt``,
    which brings its own key material)."""
    binding = {
        "experiment_id": experiment_id,
        "protocol_version": protocol_version,
        "arm": arm,
        "rollout_id": rollout_id,
    }
    verified, entry = A.verify_and_consume(
        stage=stage, operation=operation, binding=binding
    )
    monkeypatch.setenv("SWARM_F2_AUTHZ_STAGE", stage)
    monkeypatch.setenv("SWARM_F2_AUTHZ_OPERATION", operation)
    monkeypatch.setenv("SWARM_F2_AUTHZ_BINDING", json.dumps(dict(verified.binding)))
    monkeypatch.setenv("SWARM_F2_AUTHZ_BINDING_DIGEST", verified.binding_digest)
    monkeypatch.setenv("SWARM_F2_AUTHZ_RECORD_DIGEST", verified.digest)
    monkeypatch.setenv("SWARM_F2_AUTHZ_CONSUMPTION_ID", str(entry["consumption_id"]))
    return {"verified": verified, "entry": entry, "binding": binding}


__all__ = [
    "TEST_REPO_REVISION",
    "AuthorizationLedger",
    "apply_repo_identity",
    "consume_parent_receipt_from_env",
    "execution_binding_digest",
    "keypair",
    "now",
    "provision_parent_receipt",
    "provision_record_env",
    "signed_record",
    "write_record",
    "write_trust_root",
]
