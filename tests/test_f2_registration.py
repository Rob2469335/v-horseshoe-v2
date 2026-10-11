"""Tests for the operative S8 task registration (qwen_train.f2_registration).

Synthetic inputs only: a tiny union report plus a tiny acquired JSONL and its
PROVENANCE stub.  No task command is executed; test_cmd is inert text.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train.f2_registration import (
    RegistrationError,
    PREDICATE,
    REGISTRATION_ID,
    SCHEMA_VERSION,
    all_pytest_node_ids,
    build_manifest,
    eligible,
    evaluator_compatible,
    is_pytest_command,
    python_confirmed,
)

_S8 = "S8_evidence_provenance"


class TestPredicates:
    @pytest.mark.parametrize("cmd,ok", [
        ("pytest -q", True),
        ("python -m pytest -rA .", True),
        ("py.test tests/", True),
        ("pytest -q --junitxml=x.xml", False),   # augment_test_command refuses it
        ("make test", False),
        ("npm test", False),
        ("", False),
        ("   ", False),
        (None, False),
    ])
    def test_is_pytest_command(self, cmd, ok):
        assert is_pytest_command(cmd) is ok

    @pytest.mark.parametrize("nodes,ok", [
        (["t/test_x.py::test_a"], True),
        (["t/test_x.py::TestC::test_a"], True),
        (["t/test_x.py::test_a[1]"], True),
        (["tests/x.yml::test_y"], True),          # well-formed, just not .py
        (["no_separator"], False),
        (["t/test_x.py::"], False),
        (["::test_a"], False),
        ([], False),
        (None, False),
    ])
    def test_all_pytest_node_ids(self, nodes, ok):
        assert all_pytest_node_ids(nodes) is ok

    def test_python_confirmed_requires_py_paths_on_both_fields(self):
        assert python_confirmed(["t/test_x.py::test_a"], ["t/test_x.py::test_b"]) is True
        assert python_confirmed(["tests/x.yml::test_y"], ["t/test_x.py::test_b"]) is False
        assert python_confirmed(["t/test_x.py::test_a"], ["IPython/p/test_x.txt::test_x"]) is False
        assert python_confirmed([], ["t/test_x.py::test_b"]) is False

    def test_evaluator_compatible_requires_all_three(self):
        assert evaluator_compatible("pytest -q", ["t/t.py::test_a"], ["t/t.py::test_b"]) is True
        assert evaluator_compatible("make test", ["t/t.py::test_a"], ["t/t.py::test_b"]) is False
        assert evaluator_compatible("pytest -q", ["nope"], ["t/t.py::test_b"]) is False

    def test_eligible_is_compatible_and_python_confirmed(self):
        assert eligible("pytest -q", ["t/t.py::a"], ["t/t.py::b"]) is True
        assert eligible("pytest -q", ["x.yml::a"], ["x.yml::b"]) is False   # compatible, not .py
        assert eligible("npm test", ["t/t.py::a"], ["t/t.py::b"]) is False


def _write_fixture(tmp_path: Path):
    d = tmp_path / "acq"
    d.mkdir()
    rows = [
        {  # eligible
            "instance_id": "a__ok-1", "created_at": "2025-06-01T00:00:00", "repo": "o/a",
            "test_cmds": ["pytest -q"],
            "FAIL_TO_PASS": ["t/test_x.py::test_a"], "PASS_TO_PASS": ["t/test_x.py::test_b"],
        },
        {  # evaluator-compatible but non-.py node paths
            "instance_id": "b__yml-1", "created_at": "2025-06-02T00:00:00", "repo": "o/b",
            "test_cmds": ["pytest -q"],
            "FAIL_TO_PASS": ["tests/x.yml::test_y"], "PASS_TO_PASS": ["tests/x.yml::test_z"],
        },
        {  # not evaluator-compatible (not a pytest command)
            "instance_id": "c__make-1", "created_at": "2025-06-03T00:00:00", "repo": "o/c",
            "test_cmds": ["make test"],
            "FAIL_TO_PASS": ["t/test_x.py::test_a"], "PASS_TO_PASS": ["t/test_x.py::test_b"],
        },
    ]
    p = d / "acquired.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (d / "PROVENANCE.json").write_text(
        json.dumps({"source_key": "swe-bench-live", "normalised_sha256": hashlib.sha256(p.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    report = tmp_path / "union_report.json"
    report.write_text(json.dumps({
        "total_raw_rows": 3, "exclusions": 0, "total_accepted_after_union": 3,
        "screen_rejections": [
            {"instance_id": "a__ok-1", "source": "swe-bench-live", "failing_rules": [_S8]},
            {"instance_id": "b__yml-1", "source": "swe-bench-live", "failing_rules": [_S8]},
            {"instance_id": "c__make-1", "source": "swe-bench-live", "failing_rules": [_S8]},
        ],
    }), encoding="utf-8")
    return report, p


class TestBuildManifest:
    def test_reconciles_compatible_from_confirmed(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        manifest, ordered, excluded = build_manifest(
            union_report=report, acquired_paths=[acquired], generated_at="T"
        )
        c = manifest["counts"]
        assert c["metadata_eligible"] == 3
        assert c["evaluator_compatible"] == 2
        assert c["python_confirmed"] == 1
        assert c["registered"] == 1
        assert c["excluded"] == 0
        assert c["shortfall"] == {"not_evaluator_compatible": 1, "not_python_confirmed": 1}
        assert [r["instance_id"] for r in ordered] == ["a__ok-1"]

    def test_manifest_is_hash_only_and_records_rule_and_inputs(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        manifest, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["registration_id"] == REGISTRATION_ID
        assert manifest["predicate"] == PREDICATE
        assert manifest["ordering_rule"] == "created_at_desc_instance_id_asc_v1"
        assert manifest["inputs"]["union_report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
        for key in ("registration_content_sha256",):
            assert len(manifest[key]) == 64
        for value in manifest["artifacts_sha256"].values():
            assert len(value) == 64

    def test_registration_is_deterministic(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        a, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        b, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T2")
        assert a["registration_content_sha256"] == b["registration_content_sha256"]

    def test_digest_changes_when_registered_metadata_changes(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        base, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        text = acquired.read_text(encoding="utf-8").replace('"o/a"', '"o/other"')
        acquired.write_text(text, encoding="utf-8")
        provenance = acquired.parent / "PROVENANCE.json"
        provenance.write_text(json.dumps({"normalised_sha256": hashlib.sha256(acquired.read_bytes()).hexdigest()}), encoding="utf-8")
        after, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        assert base["registration_content_sha256"] != after["registration_content_sha256"]

    def test_acquired_bytes_must_match_provenance(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        acquired.write_text(acquired.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(RegistrationError, match="digest mismatch"):
            build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")

    @pytest.mark.parametrize("declared", [None, "not-a-digest", "A" * 64])
    def test_missing_or_malformed_acquired_digest_is_rejected(self, tmp_path, declared):
        report, acquired = _write_fixture(tmp_path)
        (acquired.parent / "PROVENANCE.json").write_text(
            json.dumps({"normalised_sha256": declared}), encoding="utf-8"
        )
        with pytest.raises(RegistrationError, match="valid normalised_sha256"):
            build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")

    def test_empty_eligible_set_is_refused(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        report.write_text(json.dumps({"screen_rejections": []}), encoding="utf-8")
        with pytest.raises(Exception, match="no metadata-eligible"):
            build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")


# ---------------------------------------------------------------------------
# Isolated TEST authorization for build_manifest's logic tests. This is NOT a
# production trust root and cannot authorise real acquisition/admission/execution.
# ---------------------------------------------------------------------------
import base64 as _b64
import datetime as _dt
import json as _json

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _authorize_registration(tmp_path, monkeypatch):
    """Isolated TEST authorization for build_manifest's logic tests.

    Ephemeral ed25519 key + temp trust root. This is NOT a production trust
    root and cannot authorise real acquisition/admission/execution. The
    fixture plays the OWNER role: it lazily signs whatever union-report digest
    the guard demands (the owner signs the population manifest it approves);
    every check - signature, scope, dates, binding equality, repository
    revision - is the REAL verifier's.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from qwen_train import f2_authorization as _A
    from tests.f2_authz_testkit import TEST_REPO_REVISION, apply_repo_identity

    apply_repo_identity(monkeypatch)
    priv = Ed25519PrivateKey.generate()
    pub = _b64.b64encode(
        priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode()
    root = tmp_path / "trust_root.json"
    root.write_text(_json.dumps({"signers": {"owner": pub}}), "utf-8")
    now = _dt.datetime.now(_dt.timezone.utc)
    template = {
        "id": "TEST-REG", "schema": _A.SCHEMA, "stage": "POP-001",
        "operations": ["register"], "approver": "owner",
        "not_before": (now - _dt.timedelta(minutes=1)).isoformat(),
        "not_after": (now + _dt.timedelta(days=1)).isoformat(),
        "protocol_digest": "d",
        "repository_revision": TEST_REPO_REVISION,
    }
    recp = tmp_path / "auth.json"

    def _sign_then_verify(record_path, **kw):
        binding = dict(kw.get("binding") or {})
        rec = dict(template)
        rec.update(binding)
        sig = priv.sign(_A.canonical_bytes(rec))
        Path(str(record_path)).write_text(_json.dumps({
            "record": rec,
            "signature": {"alg": "ed25519", "key_id": "owner", "signer": "owner",
                          "sig": _b64.b64encode(sig).decode()},
        }), "utf-8")
        return real_verify(record_path, **kw)

    real_verify = _A.verify_authorization
    # Seed the env record (some tests inspect it), then route every
    # verification through the signing oracle.
    recp.write_text(_json.dumps({
        "record": template,
        "signature": {"alg": "ed25519", "key_id": "owner", "signer": "owner",
                      "sig": _b64.b64encode(priv.sign(_A.canonical_bytes(template))).decode()},
    }), "utf-8")
    monkeypatch.setattr(_A, "verify_authorization", _sign_then_verify)
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(root))
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(recp))
    yield
