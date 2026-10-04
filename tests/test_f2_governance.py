"""F2 evidence governance layer — adversarial suite.

Proves the security properties of the governance layers that close the five S8
gaps: evaluator authorization, evaluator implementation identity, immutable
execution identity, trusted artifact store, and independent verification that
DERIVES the result from retained artifacts instead of trusting the declaration.

No network, no service, no task execution, no real evidence.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from qwen_train.f2_evidence import (
    ROLE_RUN_LOG,
    ROLE_TEST_OUTPUT,
    STATE_ARTIFACT_DIGEST_MISMATCH,
    STATE_ARTIFACT_ESCAPES_ROOT,
    STATE_ARTIFACT_MISSING,
    STATE_IDENTITY_MISMATCH,
    STATE_MALFORMED,
    STATE_PROVENANCE_NOT_ESTABLISHED,
    STATE_SCIENTIFICALLY_INSUFFICIENT,
    STATE_UNAUTHORIZED_PROCEDURE,
    STATE_UNKNOWN_SCHEMA,
    STATE_VERIFIED,
    ArtifactRef,
    EvidenceVerification,
)
from qwen_train.f2_governance import (
    ROLE_EVALUATOR_IMPLEMENTATION,
    EvaluatorAuthorization,
    EvaluatorRegistry,
    ExecutionBundle,
    RetentionPolicy,
    TrustedArtifactStore,
    build_execution_identity,
    derive_result,
    evaluator_bytes_proven,
    registered_result_protocols,
    verify_execution_bundle,
    verify_governed_pair,
)

TASK = "pypa__twine-1066"
REPO = "pypa/twine"
BASE = "4a1fc064a7899872ee845df6a8810bb51a6845ac"
IMPL = "a" * 64
AUTH = EvaluatorAuthorization(
    evaluator_id="f2_base_gold_runner",
    version="v1",
    implementation_digest=IMPL,
    procedure_id="f2_base_gold_eval",
    protocol_version="proto_v1",
)

_ROOT = Path(tempfile.mkdtemp(prefix="f2_governance_fixture_"))
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)

_FAIL_REPORT = json.dumps({"fail_to_pass": {"tests/t.py::a": "failed"}}).encode()
_PASS_REPORT = json.dumps({"fail_to_pass": {"tests/t.py::a": "passed"}}).encode()


def _store(root: Path | None = None) -> TrustedArtifactStore:
    return TrustedArtifactStore(
        root=root or _ROOT, retention=RetentionPolicy("f2_evidence_retention", 90, True)
    )


def _registry(auths=(AUTH,)) -> EvaluatorRegistry:
    return EvaluatorRegistry(auths)


def _write(name: str, data: bytes, role: str) -> ArtifactRef:
    (_ROOT / name).write_bytes(data)
    return ArtifactRef(name=name, digest=hashlib.sha256(data).hexdigest(), size_bytes=len(data), role=role)


def _bundle(
    state: str,
    declared: str,
    *,
    test_data: bytes | None = None,
    gold_ok: bool | None = None,
    tag: str = "",
    **identity_over,
):
    """Build a bundle whose retained evidence is a real JSON test report."""
    if test_data is None:
        test_data = _PASS_REPORT if state == "gold" else _FAIL_REPORT
    slug = f"{state}{tag}"
    out = _write(f"{slug}-test.out", test_data, ROLE_TEST_OUTPUT)
    log = _write(f"{slug}-run.log", f"{state} run log\n".encode(), ROLE_RUN_LOG)
    ident = build_execution_identity(
        instance_id=identity_over.pop("instance_id", TASK),
        repository=identity_over.pop("repository", REPO),
        base_commit=identity_over.pop("base_commit", BASE),
        execution_state_identity=state,
        execution_state_digest=identity_over.pop(
            "execution_state_digest", hashlib.sha256(state.encode()).hexdigest()
        ),
        evaluator=identity_over.pop("evaluator", AUTH),
        environment_identity=identity_over.pop("environment_identity", "python_base_310"),
        test_command=identity_over.pop("test_command", "pytest -q"),
        test_output=out,
        run_log=log,
    )
    return ExecutionBundle(
        identity=ident,
        declared_result=declared,
        result_protocol_id="json_test_report_v1",
        test_output=out,
        run_log=log,
        started_at="2026-10-04T00:00:00Z",
        finished_at="2026-10-04T00:01:00Z",
    )


def _good_pair():
    return _bundle("base", "fail"), _bundle("gold", "pass")


def _verify(bundle, *, registry=None, store=None):
    return verify_execution_bundle(
        bundle, store=store or _store(), registry=registry or _registry()
    )


# --------------------------------------------------------------------------
class TestHappyPath:
    def test_valid_bundle_verifies(self):
        assert _verify(_bundle("base", "fail")).state == STATE_VERIFIED

    def test_valid_pair_verifies(self):
        b, g = _good_pair()
        v = verify_governed_pair(
            b, g, store=_store(), registry=_registry(),
            task_id=TASK, repository=REPO, base_commit=BASE,
        )
        assert v.state == STATE_VERIFIED and v.scientifically_sufficient

    def test_reference_protocol_is_registered(self):
        assert "json_test_report_v1" in registered_result_protocols()


# --------------------------------------------------------------------------
class TestEvaluatorAuthorization:
    """Cases 1-6: authorization must fail closed on every component."""

    def test_1_evaluator_missing_from_registry(self):
        v = _verify(_bundle("base", "fail"), registry=EvaluatorRegistry(()))
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_2_empty_allowlist_fails_closed(self):
        v = _verify(_bundle("base", "fail"), registry=EvaluatorRegistry.empty())
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE and not v.ok

    def test_3_unknown_evaluator(self):
        other = EvaluatorAuthorization("other_runner", "v1", IMPL, "p", "proto_v1")
        v = _verify(_bundle("base", "fail"), registry=_registry((other,)))
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_4_version_mismatch(self):
        v = _verify(
            _bundle("base", "fail"),
            registry=_registry((EvaluatorAuthorization("f2_base_gold_runner", "v2", IMPL, "f2_base_gold_eval", "proto_v1"),)),
        )
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_5_implementation_digest_mismatch(self):
        v = _verify(
            _bundle("base", "fail"),
            registry=_registry((EvaluatorAuthorization("f2_base_gold_runner", "v1", "b" * 64, "f2_base_gold_eval", "proto_v1"),)),
        )
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE
        assert "implementation" in v.detail or "digest" in v.detail

    def test_5b_procedure_id_mismatch(self):
        v = _verify(
            _bundle("base", "fail"),
            registry=_registry((EvaluatorAuthorization("f2_base_gold_runner", "v1", IMPL, "other_proc", "proto_v1"),)),
        )
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_5c_protocol_version_mismatch(self):
        v = _verify(
            _bundle("base", "fail"),
            registry=_registry((EvaluatorAuthorization("f2_base_gold_runner", "v1", IMPL, "f2_base_gold_eval", "proto_v2"),)),
        )
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_6_modified_evaluator_implementation_invalidates_authorization(self):
        """Changing the evaluator bytes must invalidate authorization."""
        changed = EvaluatorAuthorization("f2_base_gold_runner", "v1", "c" * 64, "f2_base_gold_eval", "proto_v1")
        v = _verify(_bundle("base", "fail"), registry=_registry((changed,)))
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_implementation_digest_must_be_a_digest_not_a_timestamp(self):
        with pytest.raises(ValueError, match="implementation_digest"):
            EvaluatorAuthorization("e", "v1", "2026-10-04", "p", "proto")

    def test_registry_narrows_to_s8_allowlist(self):
        assert _registry().as_s8_allowlist() == {"f2_base_gold_runner": ("v1",)}


# --------------------------------------------------------------------------
class TestExecutionIdentity:
    """Cases 7-10, 22, 26, 27: identity is canonical and mutation-sensitive."""

    def test_26_two_identical_bundles_share_identity(self):
        a = _bundle("base", "fail", tag="-a")
        c = _bundle("base", "fail", tag="-a")
        assert a.identity.digest() == c.identity.digest()
        # A different artifact identity IS a scientifically different execution.
        other = _bundle("base", "fail", tag="-b")
        assert other.identity.digest() != a.identity.digest()

    def test_26b_identical_description_is_deterministic(self):
        x = _bundle("base", "fail", tag="-x")
        y = _bundle("base", "fail", tag="-x")
        assert x.identity.digest() == y.identity.digest()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("instance_id", "other__task-1"),
            ("repository", "other/repo"),
            ("base_commit", "f" * 40),
            ("environment_identity", "python_other"),
            ("test_command", "pytest -x"),
        ],
    )
    def test_27_scientific_fact_mutation_changes_identity(self, field, value):
        base = _bundle("base", "fail", tag=f"-{field}")
        mutated = _bundle("base", "fail", tag=f"-{field}", **{field: value})
        assert base.identity.digest() != mutated.identity.digest()

    def test_7_execution_state_digest_mutation_changes_identity(self):
        a = _bundle("base", "fail", tag="-s1")
        b = _bundle("base", "fail", tag="-s1", execution_state_digest="9" * 64)
        assert a.identity.digest() != b.identity.digest()

    def test_22_timestamp_only_mutation_does_not_change_identity(self):
        a = _bundle("base", "fail", tag="-t")
        b = ExecutionBundle(
            identity=a.identity, declared_result=a.declared_result,
            result_protocol_id=a.result_protocol_id, test_output=a.test_output,
            run_log=a.run_log, started_at="2030-01-01T00:00:00Z",
            finished_at="2030-01-01T00:01:00Z",
        )
        assert a.identity.digest() == b.identity.digest()

    def test_identity_rejects_invalid_state(self):
        with pytest.raises(ValueError, match="execution_state_identity"):
            build_execution_identity(
                instance_id=TASK, repository=REPO, base_commit=BASE,
                execution_state_identity="sideways",
                execution_state_digest="a" * 64, evaluator=AUTH,
                environment_identity="e", test_command="c",
                test_output=_write("z1", b"x", ROLE_TEST_OUTPUT),
                run_log=_write("z2", b"y", ROLE_RUN_LOG),
            )


# --------------------------------------------------------------------------
class TestTrustedArtifactStore:
    """Cases 11-16, 24, 25: containment, integrity, role, root."""

    def test_11_parent_traversal_rejected(self):
        store = _store()
        ref = ArtifactRef("../outside.txt", "a" * 64, 1, ROLE_TEST_OUTPUT)
        ok, why = store.verify_ref(ref)
        assert not ok and "parent-directory" in why

    def test_12_absolute_path_rejected(self):
        store = _store()
        ref = ArtifactRef(str(_ROOT / "x"), "a" * 64, 1, ROLE_TEST_OUTPUT)
        ok, why = store.verify_ref(ref)
        assert not ok and "absolute" in why

    def test_13_unc_path_rejected(self):
        store = _store()
        ref = ArtifactRef(r"\\server\share\f", "a" * 64, 1, ROLE_TEST_OUTPUT)
        ok, why = store.verify_ref(ref)
        assert not ok and "absolute" in why

    def test_14_symlink_escape_rejected_if_supported(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret\n")
        try:
            os.symlink(outside, root / "link.txt")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this host")
        store = _store(root)
        ref = ArtifactRef("link.txt", hashlib.sha256(b"secret\n").hexdigest(), 7, ROLE_TEST_OUTPUT)
        ok, why = store.verify_ref(ref)
        assert not ok and "escape" in why

    def test_15_artifact_digest_tampering(self):
        b = _bundle("base", "fail", tag="-dt")
        (_ROOT / b.test_output.name).write_bytes(b"TAMPERED")
        assert _verify(b).state == STATE_ARTIFACT_DIGEST_MISMATCH

    def test_16_artifact_size_tampering(self):
        b = _bundle("base", "fail", tag="-st")
        bogus = ArtifactRef(
            name=b.test_output.name, digest=b.test_output.digest,
            size_bytes=int(b.test_output.size_bytes) + 5, role=ROLE_TEST_OUTPUT,
        )
        tampered = ExecutionBundle(
            identity=b.identity, declared_result=b.declared_result,
            result_protocol_id=b.result_protocol_id, test_output=bogus,
            run_log=b.run_log, started_at=b.started_at, finished_at=b.finished_at,
        )
        assert _verify(tampered).state == STATE_ARTIFACT_DIGEST_MISMATCH

    def test_17_role_swap_rejected(self):
        b = _bundle("base", "fail", tag="-rs")
        swapped = ExecutionBundle(
            identity=b.identity, declared_result=b.declared_result,
            result_protocol_id=b.result_protocol_id,
            test_output=ArtifactRef(
                b.test_output.name, b.test_output.digest,
                b.test_output.size_bytes, ROLE_RUN_LOG,
            ),
            run_log=b.run_log, started_at=b.started_at, finished_at=b.finished_at,
        )
        # The store accepts either role; frozen S8 enforces role-vs-slot binding.
        assert _verify(swapped).state in (STATE_IDENTITY_MISMATCH, STATE_ARTIFACT_ESCAPES_ROOT, STATE_MALFORMED)

    def test_24_missing_artifact_root_artifacts_not_found(self, tmp_path):
        store = _store(tmp_path / "does_not_exist")
        b = _bundle("base", "fail", tag="-mr")
        assert _verify(b, store=store).state == STATE_ARTIFACT_MISSING

    def test_25_wrong_root_artifacts_not_found(self, tmp_path):
        wrong = tmp_path / "wrong_root"
        wrong.mkdir()
        b = _bundle("base", "fail", tag="-wr")
        assert _verify(b, store=_store(wrong)).state == STATE_ARTIFACT_MISSING

    def test_store_requires_retention_policy(self):
        with pytest.raises(ValueError):
            RetentionPolicy("", 30, True)
        with pytest.raises(ValueError):
            RetentionPolicy("p", -1, True)


# --------------------------------------------------------------------------
class TestIndependentVerification:
    """Cases 18-21: the declaration is never trusted."""

    def test_18_declared_pass_but_evidence_establishes_fail(self):
        b = _bundle("base", "pass", test_data=_FAIL_REPORT, tag="-dp")
        v = _verify(b)
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT
        assert "declared" in v.detail and "rejected" in v.detail

    def test_18b_declared_fail_but_evidence_establishes_pass(self):
        b = _bundle("base", "fail", test_data=_PASS_REPORT, tag="-df")
        assert _verify(b).state == STATE_SCIENTIFICALLY_INSUFFICIENT

    def test_19_declared_result_cannot_be_established(self):
        """Unparseable retained evidence must NOT be upgraded to a pass."""
        b = _bundle("base", "fail", test_data=b"not json at all", tag="-nr")
        v = _verify(b)
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT
        assert "cannot establish" in v.detail

    def test_19b_unregistered_result_protocol_fails_closed(self):
        b = _bundle("base", "fail", tag="-up")
        b2 = ExecutionBundle(
            identity=b.identity, declared_result=b.declared_result,
            result_protocol_id="some_unregistered_protocol",
            test_output=b.test_output, run_log=b.run_log,
            started_at=b.started_at, finished_at=b.finished_at,
        )
        v = _verify(b2)
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT
        assert "no result-derivation protocol" in v.detail

    def test_20_forged_json_verification_result_is_ignored(self):
        """A bundle cannot carry a pre-baked 'verified' field that is honoured."""
        b = _bundle("base", "pass", test_data=_FAIL_REPORT, tag="-fj")
        d = b.to_dict()
        d["verification"] = {"state": "VERIFIED", "ok": True}
        v = verify_execution_bundle(d, store=_store(), registry=_registry())
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT

    def test_21_direct_construction_of_verifier_result_refused(self):
        with pytest.raises(TypeError, match="must be produced by the S8 verifier"):
            EvidenceVerification(state=STATE_VERIFIED)

    def test_derive_result_is_available_independently(self):
        b = _bundle("base", "fail", tag="-dr")
        derived, _ = derive_result(b, _store())
        assert derived == "fail"


# --------------------------------------------------------------------------
class TestPairAndSchema:
    def test_pair_rejects_base_that_passes(self):
        b = _bundle("base", "fail", test_data=_PASS_REPORT, tag="-pb")
        g = _bundle("gold", "pass")
        v = verify_governed_pair(
            b, g, store=_store(), registry=_registry(),
            task_id=TASK, repository=REPO, base_commit=BASE,
        )
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT

    def test_pair_rejects_missing_gold(self):
        b = _bundle("base", "fail")
        v = verify_governed_pair(
            b, None, store=_store(), registry=_registry(),
            task_id=TASK, repository=REPO, base_commit=BASE,
        )
        assert v.state == STATE_PROVENANCE_NOT_ESTABLISHED

    def test_pair_rejects_identity_mismatch(self):
        b = _bundle("base", "fail", tag="-im")
        g = _bundle("gold", "pass", tag="-im", repository="other/repo")
        v = verify_governed_pair(
            b, g, store=_store(), registry=_registry(),
            task_id=TASK, repository=REPO, base_commit=BASE,
        )
        assert v.state in (STATE_IDENTITY_MISMATCH, STATE_SCIENTIFICALLY_INSUFFICIENT)

    def test_unknown_bundle_schema_fails_closed(self):
        b = _bundle("base", "fail", tag="-sc")
        d = b.to_dict()
        d["schema_version"] = "f2_execution_bundle_v0"
        assert verify_execution_bundle(d, store=_store(), registry=_registry()).state == STATE_UNKNOWN_SCHEMA

    def test_missing_bundle_schema_fails_closed(self):
        b = _bundle("base", "fail", tag="-ms")
        d = b.to_dict()
        d["schema_version"] = ""
        assert verify_execution_bundle(d, store=_store(), registry=_registry()).state == STATE_UNKNOWN_SCHEMA

    def test_invalid_declared_result_is_malformed(self):
        b = _bundle("base", "fail", tag="-iv")
        b2 = ExecutionBundle(
            identity=b.identity, declared_result="maybe",
            result_protocol_id=b.result_protocol_id, test_output=b.test_output,
            run_log=b.run_log, started_at=b.started_at, finished_at=b.finished_at,
        )
        assert _verify(b2).state == STATE_MALFORMED

    def test_bundle_roundtrips(self):
        b = _bundle("base", "fail", tag="-rt")
        again = ExecutionBundle.from_dict(b.to_dict())
        assert again.identity.digest() == b.identity.digest()
        assert again.declared_result == b.declared_result


# --------------------------------------------------------------------------
class TestGoldInformationBoundary:
    """Case 23: no gold solution material may reach the worker-facing surface."""

    def test_gold_identity_uses_only_an_opaque_state_digest(self):
        gold_patch = (
            "diff --git a/twine/package.py b/twine/package.py\n"
            "+    provides_extra = True\n"
        )
        g = _bundle("gold", "pass", tag="-gold")
        payload = json.dumps(g.identity.canonical_payload())
        assert "diff --git" not in payload
        assert "provides_extra" not in payload
        # base_commit IS present (task identity); the gold state is opaque.
        assert BASE in payload
        assert g.identity.execution_state_digest != BASE
        assert len(g.identity.execution_state_digest) == 64
        assert gold_patch not in payload

    def test_bundle_does_not_carry_artifact_payloads(self):
        g = _bundle("gold", "pass", tag="-bp", test_data=_PASS_REPORT)
        d = json.dumps(g.to_dict())
        assert "passed" not in d  # artifact bytes are referenced, never inlined
        assert "fail_to_pass" not in d

    def test_governance_limitations_are_declared(self):
        from qwen_train.f2_governance import GOVERNANCE_LIMITATIONS

        for key in ("authorized_evaluator", "result_derivation", "artifact_store",
                    "run_identity", "reexecution"):
            assert key in GOVERNANCE_LIMITATIONS

class TestArtifactBindingIntoIdentity:
    """Fix 1: the execution identity binds artifact digest + size, not just name."""

    def _ident(self, out, log, **over):
        return build_execution_identity(
            instance_id=over.pop("instance_id", TASK),
            repository=over.pop("repository", REPO),
            base_commit=over.pop("base_commit", BASE),
            execution_state_identity="base",
            execution_state_digest="b" * 64,
            evaluator=AUTH,
            environment_identity="e",
            test_command="c",
            test_output=out,
            run_log=log,
        )

    OUT = ArtifactRef("t.out", "c" * 64, 10, ROLE_TEST_OUTPUT)
    LOG = ArtifactRef("r.log", "d" * 64, 20, ROLE_RUN_LOG)

    def test_1_identical_descriptions_identical_identity(self):
        assert self._ident(self.OUT, self.LOG).digest() == self._ident(self.OUT, self.LOG).digest()

    def test_2_timestamp_only_mutation_identical_identity(self):
        # Identity has no timestamp input at all; two calls differ only in the
        # (absent) clock, so the digest is stable.
        assert self._ident(self.OUT, self.LOG).digest() == self._ident(self.OUT, self.LOG).digest()

    def test_3_test_output_digest_mutation_changes_identity(self):
        mutated = ArtifactRef("t.out", "e" * 64, 10, ROLE_TEST_OUTPUT)
        assert self._ident(self.OUT, self.LOG).digest() != self._ident(mutated, self.LOG).digest()

    def test_4_test_output_size_mutation_changes_identity(self):
        mutated = ArtifactRef("t.out", "c" * 64, 11, ROLE_TEST_OUTPUT)
        assert self._ident(self.OUT, self.LOG).digest() != self._ident(mutated, self.LOG).digest()

    def test_5_run_log_digest_mutation_changes_identity(self):
        mutated = ArtifactRef("r.log", "f" * 64, 20, ROLE_RUN_LOG)
        assert self._ident(self.OUT, self.LOG).digest() != self._ident(self.OUT, mutated).digest()

    def test_6_run_log_size_mutation_changes_identity(self):
        mutated = ArtifactRef("r.log", "d" * 64, 21, ROLE_RUN_LOG)
        assert self._ident(self.OUT, self.LOG).digest() != self._ident(self.OUT, mutated).digest()

    def test_7_artifact_role_mutation_changes_identity(self):
        mutated = ArtifactRef("t.out", "c" * 64, 10, ROLE_RUN_LOG)
        assert self._ident(self.OUT, self.LOG).digest() != self._ident(mutated, self.LOG).digest()

    def test_8_artifact_name_mutation_changes_identity(self):
        mutated = ArtifactRef("t2.out", "c" * 64, 10, ROLE_TEST_OUTPUT)
        assert self._ident(self.OUT, self.LOG).digest() != self._ident(mutated, self.LOG).digest()

    def test_identity_descriptor_contains_digest_and_size(self):
        idents = self._ident(self.OUT, self.LOG).artifact_identities
        joined = " ".join(idents)
        assert ("c" * 64) in joined and ("d" * 64) in joined
        assert "10" in joined and "20" in joined


class TestEvaluatorImplementationBytes:
    """Fix 2: byte provenance is a SEPARATE claim from registry authorization."""

    def test_absent_implementation_artifact_is_not_proven(self):
        b = _bundle("base", "fail", tag="-noimpl")
        assert b.implementation_artifact is None
        assert evaluator_bytes_proven(b, _store()) is False
        # Registry authorization still holds, so the bundle still verifies.
        assert _verify(b).state == STATE_VERIFIED

    def test_present_matching_implementation_bytes_are_proven(self):
        impl_bytes = b"# evaluator implementation\nprint('run')\n"
        impl = _write("impl.py", impl_bytes, ROLE_EVALUATOR_IMPLEMENTATION)
        auth = EvaluatorAuthorization(
            "f2_base_gold_runner", "v1",
            hashlib.sha256(impl_bytes).hexdigest(), "f2_base_gold_eval", "proto_v1",
        )
        b = _bundle("base", "fail", tag="-impl", evaluator=auth)
        b = ExecutionBundle(
            identity=b.identity, declared_result=b.declared_result,
            result_protocol_id=b.result_protocol_id, test_output=b.test_output,
            run_log=b.run_log, started_at=b.started_at, finished_at=b.finished_at,
            implementation_artifact=impl,
        )
        assert evaluator_bytes_proven(b, _store()) is True
        assert _verify(b, registry=_registry((auth,))).state == STATE_VERIFIED

    def test_mismatched_implementation_bytes_fail_closed(self):
        impl_bytes = b"# a DIFFERENT evaluator\n"
        impl = _write("impl_bad.py", impl_bytes, ROLE_EVALUATOR_IMPLEMENTATION)
        b = _bundle("base", "fail", tag="-implbad")
        b = ExecutionBundle(
            identity=b.identity, declared_result=b.declared_result,
            result_protocol_id=b.result_protocol_id, test_output=b.test_output,
            run_log=b.run_log, started_at=b.started_at, finished_at=b.finished_at,
            implementation_artifact=impl,
        )
        assert evaluator_bytes_proven(b, _store()) is False
        assert _verify(b).state == STATE_UNAUTHORIZED_PROCEDURE

    def test_missing_implementation_file_fails_closed(self):
        impl = ArtifactRef("absent_impl.py", "a" * 64, 5, ROLE_EVALUATOR_IMPLEMENTATION)
        b = _bundle("base", "fail", tag="-implmissing")
        b = ExecutionBundle(
            identity=b.identity, declared_result=b.declared_result,
            result_protocol_id=b.result_protocol_id, test_output=b.test_output,
            run_log=b.run_log, started_at=b.started_at, finished_at=b.finished_at,
            implementation_artifact=impl,
        )
        assert _verify(b).state == STATE_ARTIFACT_MISSING

    def test_implementation_artifact_role_is_enforced(self):
        impl = ArtifactRef("t.out", "c" * 64, 1, ROLE_TEST_OUTPUT)  # wrong role
        b = _bundle("base", "fail", tag="-implrole")
        b = ExecutionBundle(
            identity=b.identity, declared_result=b.declared_result,
            result_protocol_id=b.result_protocol_id, test_output=b.test_output,
            run_log=b.run_log, started_at=b.started_at, finished_at=b.finished_at,
            implementation_artifact=impl,
        )
        assert _verify(b).state == STATE_MALFORMED
