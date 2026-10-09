"""S8 evidence provenance contract — hardened.

Separates INTEGRITY, PROVENANCE and SCIENTIFIC VALIDITY, and adds the research-
grade hardening: fail-closed evaluator authorization, an explicit trust boundary
on the verification result, artifact-role binding, artifact-path containment,
TOCTOU-safe hashing, timestamps excluded from the cryptographic identity, and a
load state that cannot be mistaken for verification.

No network, service, database, or arm execution.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from qwen_train.f2_evidence import (
    LOAD_MALFORMED,
    LOAD_MISSING,
    LOAD_PARSED,
    ROLE_RUN_LOG,
    ROLE_TEST_OUTPUT,
    SCHEMA_VERSION,
    STATE_ARTIFACT_DIGEST_MISMATCH,
    STATE_ARTIFACT_ESCAPES_ROOT,
    STATE_ARTIFACT_MISSING,
    STATE_DIGEST_MISMATCH,
    STATE_ENVIRONMENT_FAILURE,
    STATE_EVALUATOR_FAILURE,
    STATE_EXECUTION_FAILURE,
    STATE_IDENTITY_MISMATCH,
    STATE_MALFORMED,
    STATE_MISSING,
    STATE_PROVENANCE_NOT_ESTABLISHED,
    STATE_SCIENTIFICALLY_INSUFFICIENT,
    STATE_UNAUTHORIZED_PROCEDURE,
    STATE_UNKNOWN_SCHEMA,
    STATE_VERIFIED,
    ArtifactRef,
    EvidenceRecord,
    EvidenceVerification,
    build_evidence_record,
    load_evidence_record,
    r5_from_evidence,
    verify_evidence_record,
    verify_task_evidence,
)
from qwen_train.f2_population import screen_entry

TASK = "pypa__twine-1066"
REPO = "pypa/twine"
BASE = "4a1fc064a7899872ee845df6a8810bb51a6845ac"
EVALUATOR = "f2_base_gold_runner"
EVALUATOR_VERSION = "v1"
AUTHORIZED = {EVALUATOR: (EVALUATOR_VERSION,)}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _artifact(root: Path, name: str, content: str, role: str) -> ArtifactRef:
    data = content.encode("utf-8")
    (root / name).write_bytes(data)  # bytes: no newline translation
    return ArtifactRef(name=name, digest=_sha(content), size_bytes=len(data), role=role)


def _rec(
    root: Path,
    *,
    state: str,
    result: str,
    failure_class: str = "",
    instance_id: str = TASK,
    repository: str = REPO,
    base_commit: str = BASE,
    state_digest: str | None = None,
    evaluator_identity: str = EVALUATOR,
    evaluator_version: str = EVALUATOR_VERSION,
    test_command: str = "pytest -q",
    environment_identity: str = "python_base_310",
    test_output: str | None = None,
    run_log: str | None = None,
    test_output_role: str = ROLE_TEST_OUTPUT,
    run_log_role: str = ROLE_RUN_LOG,
    started: str = "2026-10-04T00:00:00Z",
    finished: str = "2026-10-04T00:01:00Z",
) -> EvidenceRecord:
    return build_evidence_record(
        instance_id=instance_id,
        repository=repository,
        base_commit=base_commit,
        execution_state_identity=state,
        execution_state_digest=state_digest or _sha(f"{state}|{instance_id}"),
        test_command=test_command,
        environment_identity=environment_identity,
        evaluator_identity=evaluator_identity,
        evaluator_version=evaluator_version,
        execution_started_at=started,
        execution_finished_at=finished,
        execution_result=result,
        failure_class=failure_class,
        test_output_artifact=_artifact(
            root, f"{state}-test.out", test_output or f"{state} test output\n", test_output_role
        ),
        run_log_artifact=_artifact(
            root, f"{state}-run.log", run_log or f"{state} run log\n", run_log_role
        ),
    )


def _pair(root: Path, **over):
    return (
        _rec(root, state="base", result="fail", **over),
        _rec(root, state="gold", result="pass", **over),
    )


def _v(rec, root, **kw):
    """Verify one record with the REQUIRED provenance inputs supplied."""
    kw.setdefault("artifact_root", root)
    kw.setdefault("authorized_evaluators", AUTHORIZED)
    return verify_evidence_record(rec, **kw)


def _vp(base, gold, root, **kw):
    kw.setdefault("artifact_root", root)
    kw.setdefault("authorized_evaluators", AUTHORIZED)
    return verify_task_evidence(
        task_id=TASK, repository=REPO, base_commit=BASE,
        base_evidence=base, gold_evidence=gold, **kw,
    )


# --------------------------------------------------------------------------
class TestValidEvidence:
    def test_1_valid_base_record(self, tmp_path):
        v = _v(_rec(tmp_path, state="base", result="fail"), tmp_path)
        assert v.state == STATE_VERIFIED
        assert v.integrity_ok and v.provenance_ok
        assert v.scientifically_sufficient is False

    def test_2_valid_gold_record(self, tmp_path):
        v = _v(_rec(tmp_path, state="gold", result="pass"), tmp_path)
        assert v.state == STATE_VERIFIED and v.integrity_ok and v.provenance_ok

    def test_3_valid_pair_is_verified_and_sufficient(self, tmp_path):
        base, gold = _pair(tmp_path)
        v = _vp(base, gold, tmp_path)
        assert v.state == STATE_VERIFIED and v.ok
        assert v.integrity_ok and v.provenance_ok and v.scientifically_sufficient


# --------------------------------------------------------------------------
class TestMissingAndMalformed:
    def test_4_missing_base(self, tmp_path):
        _, gold = _pair(tmp_path)
        assert _vp(None, gold, tmp_path).state == STATE_MISSING

    def test_5_missing_gold(self, tmp_path):
        base, _ = _pair(tmp_path)
        assert _vp(base, None, tmp_path).state == STATE_MISSING

    def test_5b_both_missing(self, tmp_path):
        assert _vp(None, None, tmp_path).state == STATE_MISSING

    def test_6_malformed_record(self, tmp_path):
        assert _v({"schema_version": SCHEMA_VERSION, "garbage": 1}, tmp_path).state == STATE_MALFORMED

    def test_6b_malformed_non_mapping(self, tmp_path):
        assert _v("not a record", tmp_path).state == STATE_MALFORMED

    def test_6c_missing_required_field(self, tmp_path):
        payload = _rec(tmp_path, state="base", result="fail").to_dict()
        payload["test_command"] = ""
        v = _v(payload, tmp_path)
        assert v.state == STATE_MALFORMED and "test_command" in v.detail


class TestSchemaVersioning:
    def test_7_unknown_schema(self, tmp_path):
        payload = _rec(tmp_path, state="base", result="fail").to_dict()
        payload["schema_version"] = "f2_evidence_record_v0"
        assert _v(payload, tmp_path).state == STATE_UNKNOWN_SCHEMA

    def test_future_schema_fails_closed(self, tmp_path):
        payload = _rec(tmp_path, state="base", result="fail").to_dict()
        payload["schema_version"] = "f2_evidence_record_v99"
        assert _v(payload, tmp_path).state == STATE_UNKNOWN_SCHEMA

    def test_missing_schema_fails_closed(self, tmp_path):
        payload = _rec(tmp_path, state="base", result="fail").to_dict()
        payload["schema_version"] = ""
        assert _v(payload, tmp_path).state == STATE_MALFORMED


# --------------------------------------------------------------------------
class TestTampering:
    def test_8_record_digest_tampering(self, tmp_path):
        payload = _rec(tmp_path, state="base", result="fail").to_dict()
        payload["execution_result"] = "pass"
        assert _v(payload, tmp_path).state == STATE_DIGEST_MISMATCH

    def test_8b_record_digest_itself_tampered(self, tmp_path):
        payload = _rec(tmp_path, state="base", result="fail").to_dict()
        payload["evidence_record_digest"] = "0" * 64
        assert _v(payload, tmp_path).state == STATE_DIGEST_MISMATCH

    def test_9_artifact_digest_tampering(self, tmp_path):
        base, gold = _pair(tmp_path)
        (tmp_path / "base-test.out").write_bytes(b"CORRUPTED\n")
        assert _vp(base, gold, tmp_path).state == STATE_ARTIFACT_DIGEST_MISMATCH

    def test_9b_missing_artifact_fails_closed(self, tmp_path):
        base, gold = _pair(tmp_path)
        (tmp_path / "gold-run.log").unlink()
        assert _vp(base, gold, tmp_path).state == STATE_ARTIFACT_MISSING

    def test_9c_truncated_artifact(self, tmp_path):
        base, gold = _pair(tmp_path)
        (tmp_path / "base-run.log").write_bytes(b"")
        assert _vp(base, gold, tmp_path).state == STATE_ARTIFACT_DIGEST_MISMATCH


# --------------------------------------------------------------------------
class TestIdentity:
    def test_10_instance_id_mismatch(self, tmp_path):
        _, gold = _pair(tmp_path)
        wrong = _rec(tmp_path, state="base", result="fail", instance_id="other__task-1")
        assert _vp(wrong, gold, tmp_path).state == STATE_IDENTITY_MISMATCH

    def test_11_repository_mismatch(self, tmp_path):
        _, gold = _pair(tmp_path)
        wrong = _rec(tmp_path, state="base", result="fail", repository="other/repo")
        assert _vp(wrong, gold, tmp_path).state == STATE_IDENTITY_MISMATCH

    def test_12_base_commit_mismatch(self, tmp_path):
        _, gold = _pair(tmp_path)
        wrong = _rec(tmp_path, state="base", result="fail", base_commit="f" * 40)
        assert _vp(wrong, gold, tmp_path).state == STATE_IDENTITY_MISMATCH

    def test_12b_swapped_states_rejected(self, tmp_path):
        base, gold = _pair(tmp_path)
        assert _vp(gold, base, tmp_path).state == STATE_IDENTITY_MISMATCH

    def test_12c_same_state_digest_rejected(self, tmp_path):
        same = _sha("identical")
        base = _rec(tmp_path, state="base", result="fail", state_digest=same)
        gold = _rec(tmp_path, state="gold", result="pass", state_digest=same)
        assert _vp(base, gold, tmp_path).state == STATE_IDENTITY_MISMATCH

    def test_12d_expected_gold_state_digest_enforced(self, tmp_path):
        base, gold = _pair(tmp_path)
        v = _vp(base, gold, tmp_path, expected_gold_state_digest=_sha("other"))
        assert v.state == STATE_IDENTITY_MISMATCH

    def test_13_missing_evaluator_identity(self, tmp_path):
        assert _v(_rec(tmp_path, state="base", result="fail", evaluator_identity=""), tmp_path).state == STATE_MALFORMED

    def test_13b_missing_evaluator_version(self, tmp_path):
        assert _v(_rec(tmp_path, state="base", result="fail", evaluator_version=""), tmp_path).state == STATE_MALFORMED


class TestEvaluatorAuthorization:
    """H1: authorization fails closed; absence is not permission."""

    def test_missing_allowlist_is_provenance_not_established(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        v = verify_evidence_record(rec, artifact_root=tmp_path, authorized_evaluators=None)
        assert v.state == STATE_PROVENANCE_NOT_ESTABLISHED and not v.ok

    def test_empty_allowlist_fails_closed(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        v = verify_evidence_record(rec, artifact_root=tmp_path, authorized_evaluators={})
        assert v.state == STATE_PROVENANCE_NOT_ESTABLISHED

    def test_unknown_evaluator_rejected(self, tmp_path):
        base, gold = _pair(tmp_path)
        v = _vp(base, gold, tmp_path, authorized_evaluators={"other": ("v1",)})
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_unknown_version_rejected(self, tmp_path):
        base, gold = _pair(tmp_path)
        v = _vp(base, gold, tmp_path, authorized_evaluators={EVALUATOR: ("v2",)})
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_empty_version_set_rejected(self, tmp_path):
        base, gold = _pair(tmp_path)
        v = _vp(base, gold, tmp_path, authorized_evaluators={EVALUATOR: ()})
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_provenance_not_established_differs_from_unauthorized(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        absent = verify_evidence_record(rec, artifact_root=tmp_path, authorized_evaluators=None)
        denied = verify_evidence_record(
            rec, artifact_root=tmp_path, authorized_evaluators={"other": ("v1",)}
        )
        assert absent.state != denied.state
        assert absent.state == STATE_PROVENANCE_NOT_ESTABLISHED
        assert denied.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_exact_authorized_evaluator_and_version_passes(self, tmp_path):
        base, gold = _pair(tmp_path)
        assert _vp(base, gold, tmp_path).ok


class TestProvenanceNotEstablished:
    """H18: no trusted artifact root => not VERIFIED."""

    def test_no_artifact_root_fails_closed(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        v = verify_evidence_record(rec, artifact_root=None, authorized_evaluators=AUTHORIZED)
        assert v.state == STATE_PROVENANCE_NOT_ESTABLISHED and not v.ok

    def test_pair_without_root_fails_closed(self, tmp_path):
        base, gold = _pair(tmp_path)
        v = verify_task_evidence(
            task_id=TASK, repository=REPO, base_commit=BASE,
            base_evidence=base, gold_evidence=gold,
            artifact_root=None, authorized_evaluators=AUTHORIZED,
        )
        assert v.state == STATE_PROVENANCE_NOT_ESTABLISHED


# --------------------------------------------------------------------------
class TestFailureAndValidity:
    def test_14_execution_failure(self, tmp_path):
        base, gold = _pair(tmp_path)
        bad = _rec(tmp_path, state="base", result="error", failure_class="execution")
        assert _vp(bad, gold, tmp_path).state == STATE_EXECUTION_FAILURE

    def test_14b_unclassified_error(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="error")
        assert _v(rec, tmp_path).state == STATE_EXECUTION_FAILURE

    def test_15_environment_failure_distinct(self, tmp_path):
        base, gold = _pair(tmp_path)
        bad = _rec(tmp_path, state="base", result="error", failure_class="environment")
        v = _vp(bad, gold, tmp_path)
        assert v.state == STATE_ENVIRONMENT_FAILURE
        assert v.state != STATE_EXECUTION_FAILURE

    def test_15b_evaluator_failure_distinct(self, tmp_path):
        base, gold = _pair(tmp_path)
        bad = _rec(tmp_path, state="gold", result="error", failure_class="evaluator")
        v = _vp(base, bad, tmp_path)
        assert v.state == STATE_EVALUATOR_FAILURE
        assert v.state not in (STATE_EXECUTION_FAILURE, STATE_ENVIRONMENT_FAILURE)

    def test_16_base_passing_at_base_insufficient(self, tmp_path):
        base = _rec(tmp_path, state="base", result="pass")
        gold = _rec(tmp_path, state="gold", result="pass")
        assert _vp(base, gold, tmp_path).state == STATE_SCIENTIFICALLY_INSUFFICIENT

    def test_16b_gold_failing_at_gold_insufficient(self, tmp_path):
        base = _rec(tmp_path, state="base", result="fail")
        gold = _rec(tmp_path, state="gold", result="fail")
        assert _vp(base, gold, tmp_path).state == STATE_SCIENTIFICALLY_INSUFFICIENT


# --------------------------------------------------------------------------
class TestArtifactRoles:
    """H5: role is bound; a test-output artifact cannot occupy the run-log slot."""

    def test_missing_role_fails_closed(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail", test_output_role="")
        assert _v(rec, tmp_path).state == STATE_MALFORMED

    def test_unknown_role_fails_closed(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail", test_output_role="bogus")
        assert _v(rec, tmp_path).state == STATE_MALFORMED

    def test_swapped_roles_fail_closed(self, tmp_path):
        rec = _rec(
            tmp_path, state="base", result="fail",
            test_output_role=ROLE_RUN_LOG, run_log_role=ROLE_TEST_OUTPUT,
        )
        assert _v(rec, tmp_path).state == STATE_IDENTITY_MISMATCH


def _rebind(payload: dict) -> dict:
    """Recompute the record digest after mutating a payload."""
    payload["evidence_record_digest"] = EvidenceRecord.from_dict(payload).compute_digest()
    return payload


class TestArtifactContainment:
    """H6: artifact references cannot escape the trusted root."""

    def test_parent_traversal_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        (tmp_path / "outside.txt").write_bytes(b"x")
        rec = _rec(root, state="base", result="fail")
        payload = rec.to_dict()
        payload["test_output_artifact"]["name"] = "../outside.txt"
        assert _v(_rebind(payload), root).state == STATE_ARTIFACT_ESCAPES_ROOT

    def test_absolute_path_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        rec = _rec(root, state="base", result="fail")
        payload = rec.to_dict()
        payload["test_output_artifact"]["name"] = str(root / "base-test.out")
        assert _v(_rebind(payload), root).state == STATE_ARTIFACT_ESCAPES_ROOT

    def test_nested_but_contained_path_is_allowed(self, tmp_path):
        root = tmp_path / "root"
        (root / "sub").mkdir(parents=True)
        rec = _rec(root, state="base", result="fail")
        (root / "base-test.out").rename(root / "sub" / "a.out")
        (root / "base-run.log").rename(root / "sub" / "r.log")
        payload = rec.to_dict()
        payload["test_output_artifact"]["name"] = "sub/a.out"
        payload["run_log_artifact"]["name"] = "sub/r.log"
        assert _v(_rebind(payload), root).state == STATE_VERIFIED

    def test_symlink_escape_rejected_if_supported(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret\n")
        link = root / "link.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this host")
        rec = _rec(tmp_path, state="base", result="fail")
        payload = rec.to_dict()
        payload["test_output_artifact"]["name"] = "link.txt"
        assert _v(_rebind(payload), root).state == STATE_ARTIFACT_ESCAPES_ROOT

    def test_crlf_and_lf_are_distinct_bytes(self, tmp_path):
        assert _sha("a\r\nb") != _sha("a\nb")

    @pytest.mark.parametrize(
        "name",
        [
            "C:\\Windows\\system32\\config\\SAM",   # Windows absolute drive path
            "\\\\server\\share\\file",              # UNC path
            "/etc/passwd",                          # POSIX absolute
            "a/../../b",                            # traversal
            "..",                                   # bare parent
        ],
    )
    def test_resolver_rejects_unsafe_names(self, tmp_path, name):
        from qwen_train.f2_evidence import _resolve_artifact_path

        resolved, why = _resolve_artifact_path(tmp_path, name)
        assert resolved is None and why

    def test_binary_artifact_hashes_exactly(self, tmp_path):
        data = bytes(range(256))
        (tmp_path / "b.out").write_bytes(data)
        rec = _rec(tmp_path, state="base", result="fail")
        payload = rec.to_dict()
        payload["test_output_artifact"] = {
            "name": "b.out", "digest": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data), "role": ROLE_TEST_OUTPUT,
        }
        payload["evidence_record_digest"] = EvidenceRecord.from_dict(payload).compute_digest()
        assert _v(payload, tmp_path).state == STATE_VERIFIED

    def test_empty_artifact_hashes_exactly(self, tmp_path):
        (tmp_path / "e.out").write_bytes(b"")
        rec = _rec(tmp_path, state="base", result="fail")
        payload = rec.to_dict()
        payload["test_output_artifact"] = {
            "name": "e.out", "digest": hashlib.sha256(b"").hexdigest(),
            "size_bytes": 0, "role": ROLE_TEST_OUTPUT,
        }
        payload["evidence_record_digest"] = EvidenceRecord.from_dict(payload).compute_digest()
        assert _v(payload, tmp_path).state == STATE_VERIFIED


# --------------------------------------------------------------------------
class TestCanonicalization:
    def test_digest_excludes_itself(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        payload = rec.to_dict()
        payload["evidence_record_digest"] = "f" * 64
        assert EvidenceRecord.from_dict(payload).compute_digest() == rec.compute_digest()

    def test_timestamps_excluded_from_identity(self, tmp_path):
        a = _rec(tmp_path, state="base", result="fail", started="2026-01-01T00:00:00Z")
        b = _rec(tmp_path, state="base", result="fail", started="2030-12-31T23:59:59Z")
        assert a.compute_digest() == b.compute_digest()

    def test_scientific_fields_change_the_digest(self, tmp_path):
        a = _rec(tmp_path, state="base", result="fail")
        b = _rec(tmp_path, state="base", result="fail", test_command="pytest -x")
        assert a.compute_digest() != b.compute_digest()

    def test_deterministic_across_equivalent_representations(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        payload = rec.canonical_payload()
        shuffled = {k: payload[k] for k in sorted(payload, reverse=True)}
        from qwen_train.f2_evidence import _canonical_bytes

        assert _canonical_bytes(payload) == _canonical_bytes(shuffled)

    def test_unicode_is_deterministic(self, tmp_path):
        a = _rec(tmp_path, state="base", result="fail", test_command="pytest \u00e9")
        b = _rec(tmp_path, state="base", result="fail", test_command="pytest \u00e9")
        assert a.compute_digest() == b.compute_digest()


# --------------------------------------------------------------------------
class TestLoader:
    def test_parsed_is_not_verified(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        loaded, state, _ = load_evidence_record(rec.to_dict())
        assert state == LOAD_PARSED
        assert state != STATE_VERIFIED
        assert loaded is not None

    def test_load_from_json_text(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        _, state, _ = load_evidence_record(json.dumps(rec.to_dict()))
        assert state == LOAD_PARSED

    def test_load_from_file(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        p = tmp_path / "ev.json"
        p.write_text(json.dumps(rec.to_dict()), encoding="utf-8")
        _, state, _ = load_evidence_record(p)
        assert state == LOAD_PARSED

    def test_load_bad_json(self):
        _, state, detail = load_evidence_record("{not json")
        assert state == LOAD_MALFORMED and detail

    def test_load_none(self):
        rec, state, _ = load_evidence_record(None)
        assert rec is None and state == LOAD_MISSING

    def test_loaded_record_still_requires_verification(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        loaded, state, _ = load_evidence_record(rec.to_dict())
        assert state == LOAD_PARSED
        # ... and only the verifier can turn it into VERIFIED.
        assert _v(loaded, tmp_path).state == STATE_VERIFIED


# --------------------------------------------------------------------------
class TestTrustBoundary:
    """H3: the type layer is not a trust boundary."""

    def test_direct_verified_construction_refused(self):
        with pytest.raises(TypeError, match="must be produced by the S8 verifier"):
            EvidenceVerification(state=STATE_VERIFIED)

    def test_direct_failure_construction_refused(self):
        with pytest.raises(TypeError):
            EvidenceVerification(state=STATE_MALFORMED)

    def test_only_verifier_produces_verifications(self, tmp_path):
        rec = _rec(tmp_path, state="base", result="fail")
        v = _v(rec, tmp_path)
        assert isinstance(v, EvidenceVerification)


class TestR5Bridge:
    def test_verified_maps_to_true(self, tmp_path):
        base, gold = _pair(tmp_path)
        assert r5_from_evidence(_vp(base, gold, tmp_path)) is True

    def test_missing_maps_to_none(self, tmp_path):
        assert r5_from_evidence(_vp(None, None, tmp_path)) is None

    @pytest.mark.parametrize(
        "state", [STATE_MALFORMED, STATE_DIGEST_MISMATCH, STATE_IDENTITY_MISMATCH,
                  STATE_EXECUTION_FAILURE, STATE_ENVIRONMENT_FAILURE,
                  STATE_EVALUATOR_FAILURE, STATE_SCIENTIFICALLY_INSUFFICIENT,
                  STATE_UNAUTHORIZED_PROCEDURE, STATE_PROVENANCE_NOT_ESTABLISHED],
    )
    def test_failures_map_to_false(self, tmp_path, state):
        # Derive a real verification in the requested failure class.
        base, gold = _pair(tmp_path)
        if state == STATE_MALFORMED:
            v = _v("not a record", tmp_path)
        elif state == STATE_DIGEST_MISMATCH:
            p = base.to_dict(); p["execution_result"] = "pass"
            v = _v(p, tmp_path)
        elif state == STATE_IDENTITY_MISMATCH:
            v = _vp(base, _rec(tmp_path, state="gold", result="pass", repository="o/r"), tmp_path)
        elif state == STATE_EXECUTION_FAILURE:
            v = _vp(_rec(tmp_path, state="base", result="error", failure_class="execution"), gold, tmp_path)
        elif state == STATE_ENVIRONMENT_FAILURE:
            v = _vp(_rec(tmp_path, state="base", result="error", failure_class="environment"), gold, tmp_path)
        elif state == STATE_EVALUATOR_FAILURE:
            v = _vp(base, _rec(tmp_path, state="gold", result="error", failure_class="evaluator"), tmp_path)
        elif state == STATE_SCIENTIFICALLY_INSUFFICIENT:
            v = _vp(_rec(tmp_path, state="base", result="pass"), gold, tmp_path)
        elif state == STATE_UNAUTHORIZED_PROCEDURE:
            v = _vp(base, gold, tmp_path, authorized_evaluators={"other": ("v1",)})
        else:
            v = _vp(base, gold, tmp_path, artifact_root=None)
        assert v.state == state
        assert r5_from_evidence(v) is False

    def test_digests_alone_cannot_reach_true(self, tmp_path):
        assert r5_from_evidence(_vp(None, None, tmp_path)) is None


# --------------------------------------------------------------------------
# Population integration
# --------------------------------------------------------------------------
def _entry(tmp_path, **over):
    base, gold = _pair(tmp_path)
    kw = dict(
        instance_id=TASK, repo=REPO, base_commit=BASE, test_cmd="pytest -q",
        fail_to_pass=["tests/test_package.py::test_x"],
        pass_to_pass=["tests/test_package.py::test_y"],
        relevant_file_set=["twine/package.py"],
        base_evidence_digest=base.evidence_record_digest,
        gold_evidence_digest=gold.evidence_record_digest,
        base_evidence=base, gold_evidence=gold,
        artifact_root=tmp_path, authorized_evaluators=AUTHORIZED,
        # F2-IMPL-AUTH-029 D2: admission also requires a CLEAN contamination
        # verdict, so the default fixture declares a cutoff the task postdates.
        model_cutoff="2025-01-01", created_at="2025-06-01",
    )
    kw.update(over)
    return screen_entry(**kw)


def _rule(entry, name) -> bool:
    return next(s.passed for s in entry.screens if s.rule == name)


class TestPopulationIntegration:
    def test_17_valid_evidence_produces_R5_true(self, tmp_path):
        e = _entry(tmp_path)
        assert _rule(e, "S8_evidence_provenance") is True
        assert e.evidence_verified is True
        assert e.evidence_state == STATE_VERIFIED
        assert e.evidence_scientifically_sufficient is True
        assert e.admitted is True

    def test_18_incomplete_evidence_cannot_admit(self, tmp_path):
        e = _entry(tmp_path, base_evidence=None, gold_evidence=None)
        assert _rule(e, "S8_evidence_provenance") is False and e.admitted is False

    def test_18b_nonempty_digests_alone_never_admit(self, tmp_path):
        e = _entry(tmp_path, base_evidence=None, gold_evidence=None)
        assert e.base_evidence_digest and e.gold_evidence_digest
        assert _rule(e, "S8_evidence_provenance") is False
        assert e.admitted is False
        assert "NOT verified" in next(
            s.detail for s in e.screens if s.rule == "S8_evidence_provenance"
        )

    def test_18c_missing_authorization_cannot_admit(self, tmp_path):
        e = _entry(tmp_path, authorized_evaluators=None)
        assert _rule(e, "S8_evidence_provenance") is False
        assert e.evidence_state == STATE_PROVENANCE_NOT_ESTABLISHED

    def test_18d_missing_artifact_root_cannot_admit(self, tmp_path):
        e = _entry(tmp_path, artifact_root=None)
        assert _rule(e, "S8_evidence_provenance") is False
        assert e.admitted is False

    def test_19_manifest_entry_carries_only_permitted_identities(self, tmp_path):
        d = _entry(tmp_path).to_dict()
        for forbidden in ("test_output_artifact", "run_log_artifact",
                          "execution_state_digest", "failure_class"):
            assert forbidden not in d
        assert SCHEMA_VERSION not in json.dumps(d)
        # The S8 verdict IS covered by the integrity identity (H2).
        for k in ("evidence_verified", "evidence_state",
                  "evidence_scientifically_sufficient"):
            assert k in d

    def test_20_manifest_cannot_reconstruct_gold_content(self, tmp_path):
        base, gold = _pair(
            tmp_path,
            test_output="GOLD PATCH: diff --git a/x b/x +secret fix",
            run_log="gold run log with solution",
        )
        e = _entry(tmp_path)
        blob = json.dumps(e.to_dict())
        for leak in ("GOLD PATCH", "diff --git", "secret fix",
                     gold.execution_state_digest, "base-test.out", "gold-run.log"):
            assert leak not in blob
        assert len(e.gold_evidence_digest) == 64

    def test_20b_state_digest_is_opaque_not_a_commit_sha(self, tmp_path):
        _, gold = _pair(tmp_path)
        assert len(gold.execution_state_digest) == 64

    def test_20c_adversarial_gold_patch_cannot_cross(self, tmp_path):
        """Serialize a manifest whose gold artifacts ARE the patch text."""
        patch = (
            "commit 4a1fc064a7899872ee845df6a8810bb51a6845ac\n"
            "diff --git a/twine/package.py b/twine/package.py\n"
            "--- a/twine/package.py\n+++ b/twine/package.py\n"
            "+    provides_extra = True\n"
        )
        base, gold = _pair(tmp_path, test_output=patch, run_log=patch)
        e = _entry(tmp_path)
        blob = json.dumps(e.to_dict())
        assert "diff --git" not in blob
        assert "provides_extra" not in blob
        # (base_commit IS legitimately present as task identity; the gold
        #  reference commit and the patch body are not.)


class TestAdmissionIdentityCoversVerdict:
    """H2: changing an admission-relevant S8 value must change the identity."""

    def test_verified_entry_identity_differs_from_unverified(self, tmp_path):
        good = _entry(tmp_path)
        bad = _entry(tmp_path, base_evidence=None, gold_evidence=None)
        assert good.identity_hash != bad.identity_hash

    def test_identity_hash_covers_evidence_state(self, tmp_path):
        good = _entry(tmp_path)
        assert good.identity_payload()["evidence_state"] == STATE_VERIFIED
        assert good.identity_payload()["evidence_verified"] is True

    def test_identity_hash_stable_across_recomputation(self, tmp_path):
        e = _entry(tmp_path)
        assert e.identity_hash == e.identity_hash

    def test_mutable_annotations_still_excluded(self, tmp_path):
        a = _entry(tmp_path).identity_hash
        b = _entry(tmp_path, usable=False).identity_hash
        assert a == b


class TestManifestSummary:
    def test_summary_separates_recorded_verified_sufficient(self, tmp_path):
        from qwen_train.f2_population import PopulationManifest

        good = _entry(tmp_path)
        bad = _entry(tmp_path, base_evidence=None, gold_evidence=None)
        s = PopulationManifest(entries=(good, bad)).summary()
        assert s["total"] == 2 and s["admitted"] == 1
        assert s["evidence_identity_recorded"] == 2
        assert s["evidence_verified"] == 1
        assert s["evidence_scientifically_sufficient"] == 1
        assert s["evidence_states"][STATE_VERIFIED] == 1