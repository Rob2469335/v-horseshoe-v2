"""Authorized F2 protocol (``f2_experiment_j_v1``) — adversarial suite.

Proves the F2 scientific protocol is provenance-first and independently regraded:
the producer declaration is never authoritative, the primary endpoint is
reconstructed from retained raw behavioral evidence by the FROZEN detector, and
every mismatch fails closed.

No network, no subprocess, no model call, no task execution, no real evidence.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import replace
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
)
from qwen_train.f2_governance import (
    ROLE_EVALUATOR_IMPLEMENTATION,
    EvaluatorAuthorization,
    EvaluatorRegistry,
    RetentionPolicy,
    TrustedArtifactStore,
)
from qwen_train.f2_protocol import (
    F2_CONTROL_ARM,
    F2_PROTOCOL_ID,
    F2_TREATMENT_ARM,
    F2Bundle,
    F2DeliveryEvidence,
    derive_f2_result,
    regrade_f2,
    regrade_f2_pair,
    verify_f2_clean_room,
)
from runtime_v2.services.task_readiness import compute_relevant_file_set_hash

TASK = "pypa__twine-1066"
REPO = "pypa/twine"
BASE = "4a1fc064a7899872ee845df6a8810bb51a6845ac"
DELIVERY_TS = "2026-10-04T00:00:00Z"
RFS = ["twine/package.py"]
RFS_HASH = compute_relevant_file_set_hash(tuple(RFS))
OPS = tuple(sorted(["write", "patch", "edit", "create"]))
LESSON = "LESSON: prefer pathlib over os.path here.\n"

_ROOT = Path(tempfile.mkdtemp(prefix="f2_protocol_fixture_"))
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)
_seq = {"n": 0}


def _write(name: str, data: bytes, role: str) -> ArtifactRef:
    _seq["n"] += 1
    n = f"{_seq['n']:03d}_{name}"
    (_ROOT / n).write_bytes(data)
    return ArtifactRef(n, hashlib.sha256(data).hexdigest(), len(data), role)


def _store() -> TrustedArtifactStore:
    return TrustedArtifactStore(_ROOT, RetentionPolicy("f2_retention", 365, True))


_IMPL_BYTES = b"# f2_experiment_j_v1 evaluator\n"
_IMPL = _write("impl.py", _IMPL_BYTES, ROLE_EVALUATOR_IMPLEMENTATION)
_AUTH = EvaluatorAuthorization(
    evaluator_id="f2_experiment_j_runner",
    version="v1",
    implementation_digest=hashlib.sha256(_IMPL_BYTES).hexdigest(),
    procedure_id="f2_experiment_j_eval",
    protocol_version=F2_PROTOCOL_ID,
)


def _registry(auth=_AUTH) -> EvaluatorRegistry:
    return EvaluatorRegistry([auth])


def _report(passed: bool) -> ArtifactRef:
    status = "passed" if passed else "failed"
    return _write("report.json", json.dumps({"fail_to_pass": {"tests/t.py::a": status}}).encode(), ROLE_RUN_LOG)


def _steps(*entries) -> ArtifactRef:
    return _write("behavior.json", json.dumps(list(entries)).encode(), ROLE_TEST_OUTPUT)


def _step(n, ts, path, op="patch", fn="filesystem"):
    return {"step_id": n, "timestamp": ts, "function_name": fn, "operation": op, "path": path}


_POST = _step(4, "2026-10-04T00:00:05Z", "twine/package.py")
_PRE = _step(2, "2026-10-03T23:59:59Z", "twine/package.py")


def _treatment(arm: str) -> tuple[ArtifactRef, ArtifactRef | None, str]:
    """(treatment artifact, lesson block artifact, lesson_block_hash) for an arm."""
    if arm == F2_TREATMENT_ARM:
        text = f"# treatment artifact\n{LESSON}tail\n"
        return (
            _write("treatment_T.txt", text.encode(), ROLE_TEST_OUTPUT),
            _write("lesson_block.txt", LESSON.encode(), ROLE_EVALUATOR_IMPLEMENTATION),
            hashlib.sha256(LESSON.encode()).hexdigest(),
        )
    text = "# treatment artifact\ntail\n"  # L removed
    return _write("treatment_X.txt", text.encode(), ROLE_TEST_OUTPUT), None, ""


def _bundle(
    arm=F2_TREATMENT_ARM,
    *,
    behavior=None,
    report=None,
    declared_endpoint=True,
    declared_step=4,
    declared_success=True,
    delivery_ts=DELIVERY_TS,
    rfs_hash=RFS_HASH,
    protocol_id=F2_PROTOCOL_ID,
    auth=_AUTH,
    implementation=None,
    treatment=None,
    lesson_block=None,
    lesson_hash=None,
):
    ta, lba, lh = treatment if treatment is not None else _treatment(arm)
    if lesson_block is not None:
        lba = lesson_block
    if lesson_hash is not None:
        lh = lesson_hash
    if arm == F2_CONTROL_ARM:
        lh = ""
        lba = None
    beh = behavior if behavior is not None else _steps(_PRE, _POST)
    rep = report if report is not None else _report(True)
    delivery = F2DeliveryEvidence(
        arm=arm,
        lesson_block_hash=lh,
        final_prompt_hash="f" * 64,
        delivery_timestamp=delivery_ts,
        treatment_artifact_hash=ta.digest,
    )
    return F2Bundle(
        instance_id=TASK,
        repository=REPO,
        base_commit=BASE,
        relevant_file_set_hash=rfs_hash,
        horizon_k=12,
        qualifying_operations=OPS,
        delivery=delivery,
        behavioral_artifact=beh,
        task_outcome_artifact=rep,
        treatment_artifact=ta,
        evaluator=auth,
        implementation_artifact=implementation or _IMPL,
        declared_endpoint=declared_endpoint,
        declared_first_edit_step=declared_step,
        declared_task_success=declared_success,
        lesson_block_artifact=lba,
        protocol_id=protocol_id,
    )


def _regrade(b, **kw):
    kw.setdefault("store", _store())
    kw.setdefault("registry", _registry())
    kw.setdefault("relevant_file_set", RFS)
    return regrade_f2(b, **kw)


# --------------------------------------------------------------------------
class TestHappyPath:
    def test_valid_T_bundle_regrades_verified(self):
        v = _regrade(_bundle(F2_TREATMENT_ARM))
        assert v.state == STATE_VERIFIED, v.detail

    def test_valid_X_bundle_regrades_verified(self):
        v = _regrade(_bundle(F2_CONTROL_ARM, declared_endpoint=True))
        assert v.state == STATE_VERIFIED, v.detail

    def test_endpoint_reconstructed_from_evidence(self):
        res, why = derive_f2_result(_bundle(F2_TREATMENT_ARM), store=_store(), relevant_file_set=RFS)
        assert res is not None, why
        assert res.endpoint is True and res.first_edit_step == 4
        assert res.first_edit_path == "twine/package.py"
        assert res.task_success is True

    def test_primary_and_secondary_are_distinct(self):
        res, _ = derive_f2_result(_bundle(F2_TREATMENT_ARM), store=_store(), relevant_file_set=RFS)
        assert res.endpoint is True and res.task_success is True
        assert "endpoint" in res.canonical_payload() and "task_success" in res.canonical_payload()

    def test_pair_regrades_and_clean_room_passes(self):
        t = _bundle(F2_TREATMENT_ARM)
        x = _bundle(F2_CONTROL_ARM)
        v, results = regrade_f2_pair(
            t, x, store=_store(), registry=_registry(), relevant_file_set=RFS
        )
        assert v.state == STATE_VERIFIED, v.detail
        assert set(results) == {F2_TREATMENT_ARM, F2_CONTROL_ARM}


# --------------------------------------------------------------------------
class TestDeclarationNeverTrusted:
    """The producer declaration is an input; disagreement fails closed."""

    def test_changed_endpoint_declaration_rejected(self):
        # Evidence establishes an endpoint; producer says none.
        v = _regrade(_bundle(F2_TREATMENT_ARM, declared_endpoint=False, declared_step=None))
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT
        assert "endpoint" in v.detail

    def test_changed_first_edit_step_rejected(self):
        v = _regrade(_bundle(F2_TREATMENT_ARM, declared_step=3))
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT
        assert "first_edit_step" in v.detail

    def test_changed_task_success_declaration_rejected(self):
        v = _regrade(_bundle(F2_TREATMENT_ARM, declared_success=False))
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT
        assert "task_success" in v.detail

    def test_producer_claim_of_endpoint_without_evidence_rejected(self):
        # No post-delivery qualifying edit at all; producer claims one.
        v = _regrade(_bundle(F2_TREATMENT_ARM, behavior=_steps(_PRE)))
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT


# --------------------------------------------------------------------------
class TestEndpointSemantics:
    """The endpoint is reconstructed by the frozen detector, not the producer."""

    def test_pre_delivery_edit_does_not_count(self):
        # Only a pre-delivery qualifying edit exists -> the event stream cannot
        # establish an endpoint, so derivation FAILS CLOSED rather than counting
        # the pre-delivery edit or inventing a censored run.
        res, why = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=_steps(_PRE)),
            store=_store(),
            relevant_file_set=RFS,
        )
        assert res is None
        assert "strictly after the delivery" in why

    def test_irrelevant_file_edit_is_not_the_endpoint(self):
        beh = _steps(_PRE, _step(4, "2026-10-04T00:00:05Z", "src/other.py"))
        res, _ = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=beh), store=_store(), relevant_file_set=RFS
        )
        assert res is not None and res.endpoint is False and res.censored is True

    def test_disallowed_operation_is_not_the_endpoint(self):
        beh = _steps(_PRE, _step(4, "2026-10-04T00:00:05Z", "twine/package.py", op="read"))
        res, _ = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=beh), store=_store(), relevant_file_set=RFS
        )
        assert res is not None and res.endpoint is False

    def test_step_beyond_k_is_censored(self):
        beh = _steps(_PRE, _step(13, "2026-10-04T00:00:05Z", "twine/package.py"))
        res, _ = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=beh), store=_store(), relevant_file_set=RFS
        )
        assert res is not None and res.endpoint is False and res.censored is True

    def test_reordered_events_still_yield_the_earliest_qualifying_edit(self):
        late = _step(9, "2026-10-04T00:00:09Z", "twine/package.py")
        early = _step(4, "2026-10-04T00:00:05Z", "twine/package.py")
        beh = _steps(late, early)  # out of order in the file
        res, _ = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=beh), store=_store(), relevant_file_set=RFS
        )
        assert res is not None and res.first_edit_step == 4

    def test_first_qualifying_edit_wins(self):
        beh = _steps(
            _PRE,
            _step(7, "2026-10-04T00:00:07Z", "twine/package.py"),
            _step(4, "2026-10-04T00:00:05Z", "twine/package.py"),
        )
        res, _ = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=beh), store=_store(), relevant_file_set=RFS
        )
        assert res.first_edit_step == 4

    def test_changed_path_is_reflected_in_result(self):
        beh = _steps(_PRE, _step(4, "2026-10-04T00:00:05Z", "twine/package.py", op="create"))
        res, _ = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=beh), store=_store(), relevant_file_set=RFS
        )
        assert res.first_edit_operation == "create"

    def test_changed_delivery_timestamp_can_censor(self):
        # Delivery moved AFTER the only qualifying edit -> censored.
        v = _regrade(
            _bundle(
                F2_TREATMENT_ARM,
                behavior=_steps(_PRE, _POST),
                delivery_ts="2026-10-04T00:00:06Z",
                declared_endpoint=False,
                declared_step=None,
            )
        )
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT  # no post-delivery step


# --------------------------------------------------------------------------
class TestEvidenceFailClosed:
    def test_missing_behavioral_evidence_fails_closed(self):
        b = _bundle(F2_TREATMENT_ARM)
        (_ROOT / b.behavioral_artifact.name).unlink()
        v = _regrade(b)
        assert v.state in (STATE_ARTIFACT_MISSING, STATE_SCIENTIFICALLY_INSUFFICIENT)

    def test_behavioral_evidence_not_json_fails_closed(self):
        bad = _write("bad.json", b"not json", ROLE_TEST_OUTPUT)
        v = _regrade(_bundle(F2_TREATMENT_ARM, behavior=bad))
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT

    def test_missing_task_outcome_evidence_fails_closed(self):
        b = _bundle(F2_TREATMENT_ARM)
        (_ROOT / b.task_outcome_artifact.name).unlink()
        assert _regrade(b).state in (STATE_ARTIFACT_MISSING, STATE_SCIENTIFICALLY_INSUFFICIENT)

    def test_changed_artifact_digest_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        (_ROOT / b.behavioral_artifact.name).write_bytes(b"[]")
        assert _regrade(b).state == STATE_ARTIFACT_DIGEST_MISMATCH

    def test_changed_artifact_size_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        bogus = ArtifactRef(
            b.behavioral_artifact.name, b.behavioral_artifact.digest,
            int(b.behavioral_artifact.size_bytes) + 1, ROLE_TEST_OUTPUT,
        )
        assert _regrade(replace(b, behavioral_artifact=bogus)).state == STATE_ARTIFACT_DIGEST_MISMATCH

    def test_path_traversal_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        bad = ArtifactRef("../escape.json", "a" * 64, 1, ROLE_TEST_OUTPUT)
        assert _regrade(replace(b, behavioral_artifact=bad)).state == STATE_ARTIFACT_ESCAPES_ROOT

    def test_absolute_path_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        bad = ArtifactRef(str(_ROOT / "x"), "a" * 64, 1, ROLE_TEST_OUTPUT)
        assert _regrade(replace(b, behavioral_artifact=bad)).state == STATE_ARTIFACT_ESCAPES_ROOT

    def test_symlink_escape_rejected_if_supported(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.json"
        outside.write_bytes(b"[]")
        try:
            os.symlink(outside, root / "link.json")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this host")
        store = TrustedArtifactStore(root, RetentionPolicy("p", 1, True))
        b = _bundle(F2_TREATMENT_ARM)
        bad = ArtifactRef("link.json", hashlib.sha256(b"[]").hexdigest(), 2, ROLE_TEST_OUTPUT)
        assert _regrade(replace(b, behavioral_artifact=bad), store=store).state == STATE_ARTIFACT_ESCAPES_ROOT


# --------------------------------------------------------------------------
class TestEvaluatorProvenance:
    def test_missing_evaluator_implementation_fails_closed(self):
        b = _bundle(F2_TREATMENT_ARM)
        assert _regrade(replace(b, implementation_artifact=None)).state == STATE_PROVENANCE_NOT_ESTABLISHED

    def test_wrong_implementation_digest_fails_closed(self):
        impl_bytes = b"# a DIFFERENT evaluator\n"
        wrong = _write("impl_wrong.py", impl_bytes, ROLE_EVALUATOR_IMPLEMENTATION)
        assert _regrade(replace(_bundle(F2_TREATMENT_ARM), implementation_artifact=wrong)).state == STATE_UNAUTHORIZED_PROCEDURE

    def test_wrong_protocol_fails_closed(self):
        v = _regrade(_bundle(F2_TREATMENT_ARM, protocol_id="json_test_report_v1"))
        assert v.state == STATE_UNKNOWN_SCHEMA

    def test_evaluator_not_authorized_for_protocol_fails_closed(self):
        other = EvaluatorAuthorization(
            "f2_experiment_j_runner", "v1",
            hashlib.sha256(_IMPL_BYTES).hexdigest(), "f2_experiment_j_eval", "some_other_protocol",
        )
        v = _regrade(_bundle(F2_TREATMENT_ARM, auth=other), registry=_registry(other))
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE

    def test_empty_registry_fails_closed(self):
        v = _regrade(_bundle(F2_TREATMENT_ARM), registry=EvaluatorRegistry(()))
        assert v.state == STATE_UNAUTHORIZED_PROCEDURE


# --------------------------------------------------------------------------
class TestArmAndIdentity:
    def test_wrong_arm_fails_closed(self):
        v = _regrade(_bundle(F2_TREATMENT_ARM), expected_arm=F2_CONTROL_ARM)
        assert v.state == STATE_IDENTITY_MISMATCH

    def test_changed_relevant_file_set_digest_fails_closed(self):
        v = _regrade(_bundle(F2_TREATMENT_ARM, rfs_hash="0" * 64))
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT

    def test_tx_identity_mismatch_fails_closed(self):
        t = _bundle(F2_TREATMENT_ARM)
        x = replace(_bundle(F2_CONTROL_ARM), repository="other/repo")
        v, _ = regrade_f2_pair(t, x, store=_store(), registry=_registry(), relevant_file_set=RFS)
        assert v.state in (STATE_IDENTITY_MISMATCH, STATE_SCIENTIFICALLY_INSUFFICIENT)


# --------------------------------------------------------------------------
class TestCleanRoom:
    def test_valid_pair_is_clean(self):
        ok, why = verify_f2_clean_room(
            _bundle(F2_TREATMENT_ARM), _bundle(F2_CONTROL_ARM), store=_store()
        )
        assert ok, why

    def test_changed_lesson_hash_fails_clean_room(self):
        t = _bundle(F2_TREATMENT_ARM, lesson_hash="9" * 64)
        ok, why = verify_f2_clean_room(t, _bundle(F2_CONTROL_ARM), store=_store())
        assert not ok

    def test_unauthorized_artifact_difference_fails_clean_room(self):
        t = _bundle(F2_TREATMENT_ARM)
        # X differs by more than the lesson block.
        x_art = _write("treatment_X_tampered.txt", b"# something else entirely\n", ROLE_TEST_OUTPUT)
        x = replace(_bundle(F2_CONTROL_ARM), treatment_artifact=x_art)
        ok, why = verify_f2_clean_room(t, x, store=_store())
        assert not ok and "removal" in why

    def test_x_carrying_a_lesson_hash_is_impossible_by_construction(self):
        """A control arm must never carry a lesson hash; the type prevents it."""
        with pytest.raises(ValueError, match="must NOT carry a lesson_block_hash"):
            F2DeliveryEvidence(
                arm=F2_CONTROL_ARM,
                lesson_block_hash="a" * 64,
                final_prompt_hash="f" * 64,
                delivery_timestamp=DELIVERY_TS,
                treatment_artifact_hash="b" * 64,
            )


# --------------------------------------------------------------------------
class TestGoldBoundary:
    def test_gold_material_in_bundle_rejected(self):
        d = _bundle(F2_TREATMENT_ARM).to_dict()
        d["gold_patch"] = "diff --git a/x b/x\n+secret"
        v = regrade_f2(d, store=_store(), registry=_registry(), relevant_file_set=RFS)
        assert v.state == STATE_MALFORMED and "gold" in v.detail

    def test_nested_gold_material_rejected(self):
        d = _bundle(F2_TREATMENT_ARM).to_dict()
        d["delivery"]["reference_commit"] = "f" * 40
        v = regrade_f2(d, store=_store(), registry=_registry(), relevant_file_set=RFS)
        assert v.state == STATE_MALFORMED

    def test_result_contains_no_gold_fields(self):
        res, _ = derive_f2_result(_bundle(F2_TREATMENT_ARM), store=_store(), relevant_file_set=RFS)
        blob = json.dumps(res.to_dict())
        for leaked in ("gold_patch", "gold_diff", "diff --git", "reference_commit"):
            assert leaked not in blob


# --------------------------------------------------------------------------
class TestDeterminism:
    def test_identical_evidence_yields_identical_result(self):
        b = _bundle(F2_TREATMENT_ARM)
        a, _ = derive_f2_result(b, store=_store(), relevant_file_set=RFS)
        c, _ = derive_f2_result(b, store=_store(), relevant_file_set=RFS)
        assert a.digest() == c.digest()

    def test_timestamp_only_mutation_does_not_change_result(self):
        b1 = _bundle(F2_TREATMENT_ARM)
        b2 = replace(b1, started_at="2030-01-01T00:00:00Z", finished_at="2030-01-01T00:01:00Z")
        r1, _ = derive_f2_result(b1, store=_store(), relevant_file_set=RFS)
        r2, _ = derive_f2_result(b2, store=_store(), relevant_file_set=RFS)
        assert r1.digest() == r2.digest()

    def test_verification_is_repeatable(self):
        b = _bundle(F2_TREATMENT_ARM)
        assert _regrade(b).state == _regrade(b).state == STATE_VERIFIED

# --------------------------------------------------------------------------
# Remaining adversarial cases
# --------------------------------------------------------------------------
class TestEndpointBoundaries:
    def _derive(self, steps):
        res, _ = derive_f2_result(
            _bundle(F2_TREATMENT_ARM, behavior=_steps(_PRE, *steps)),
            store=_store(),
            relevant_file_set=RFS,
        )
        return res

    def test_step_1_is_an_endpoint(self):
        r = self._derive([_step(1, "2026-10-04T00:00:01Z", "twine/package.py")])
        assert r.endpoint is True and r.first_edit_step == 1

    def test_step_12_is_an_endpoint(self):
        r = self._derive([_step(12, "2026-10-04T00:00:12Z", "twine/package.py")])
        assert r.endpoint is True and r.first_edit_step == 12

    def test_step_13_is_censored(self):
        r = self._derive([_step(13, "2026-10-04T00:00:13Z", "twine/package.py")])
        assert r.endpoint is False and r.censored is True

    def test_no_qualifying_event_is_censored(self):
        r = self._derive([_step(5, "2026-10-04T00:00:05Z", "src/other.py")])
        assert r.endpoint is False and r.censored is True

    def test_duplicate_events_yield_the_first_step(self):
        dup = _step(4, "2026-10-04T00:00:05Z", "twine/package.py")
        r = self._derive([dup, dict(dup), _step(6, "2026-10-04T00:00:06Z", "twine/package.py")])
        assert r.first_edit_step == 4


class TestMoreArtifactAdversarial:
    def test_unc_path_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        bad = ArtifactRef(r"\\server\share\f.json", "a" * 64, 1, ROLE_TEST_OUTPUT)
        assert _regrade(replace(b, behavioral_artifact=bad)).state == STATE_ARTIFACT_ESCAPES_ROOT

    def test_task_outcome_evidence_mutation_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        (_ROOT / b.task_outcome_artifact.name).write_bytes(b'{"fail_to_pass":{"t::a":"failed"}}')
        assert _regrade(b).state == STATE_ARTIFACT_DIGEST_MISMATCH

    def test_treatment_artifact_mutation_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        (_ROOT / b.treatment_artifact.name).write_bytes(b"# tampered\n")
        assert _regrade(b).state == STATE_ARTIFACT_DIGEST_MISMATCH

    def test_implementation_role_mutation_rejected(self):
        b = _bundle(F2_TREATMENT_ARM)
        bad_impl = ArtifactRef(
            b.implementation_artifact.name, b.implementation_artifact.digest,
            b.implementation_artifact.size_bytes, ROLE_TEST_OUTPUT,
        )
        assert _regrade(replace(b, implementation_artifact=bad_impl)).state == STATE_MALFORMED

    def test_treatment_missing_lesson_block_fails_clean_room(self):
        t = _bundle(F2_TREATMENT_ARM)
        # T treatment text without the lesson block -> removal cannot match X.
        plain = _write("plain.txt", b"# treatment artifact\ntail\n", ROLE_TEST_OUTPUT)
        t = replace(t, treatment_artifact=plain)
        ok, why = verify_f2_clean_room(t, _bundle(F2_CONTROL_ARM), store=_store())
        assert not ok

    def test_behavioral_record_missing_field_fails_closed(self):
        bad = _write("bfield.json", json.dumps([{"step_id": 4}]).encode(), ROLE_TEST_OUTPUT)
        v = _regrade(_bundle(F2_TREATMENT_ARM, behavior=bad))
        assert v.state == STATE_SCIENTIFICALLY_INSUFFICIENT


# --------------------------------------------------------------------------
# Admission (readiness + protocol + clean room)
# --------------------------------------------------------------------------
class TestAdmission:
    def _ready(self, **over):
        from runtime_v2.services.task_readiness import READINESS_CONDITIONS, ReadinessEvidence

        d = {c: True for c in READINESS_CONDITIONS}
        d.update(over)
        return ReadinessEvidence(**d)

    def _task(self, **over):
        from runtime_v2.services.task_readiness import TaskReadiness

        kw = dict(task_id=TASK, base_commit=BASE, relevant_file_set=tuple(RFS))
        kw.update(over)
        return TaskReadiness(**kw)

    def _admit(self, **over):
        from qwen_train.f2_admission import admit_f2_task

        kw = dict(
            readiness=self._task(),
            readiness_evidence=self._ready(),
            store=_store(),
            registry=_registry(),
            relevant_file_set=RFS,
            t_bundle=_bundle(F2_TREATMENT_ARM),
            x_bundle=_bundle(F2_CONTROL_ARM),
        )
        kw.update(over)
        return admit_f2_task(**kw)

    def test_ready_and_valid_pair_is_admissible(self):
        a = self._admit()
        assert a.admissible is True, a.detail
        assert set(a.results) == {F2_TREATMENT_ARM, F2_CONTROL_ARM}

    def test_missing_readiness_is_not_admissible(self):
        assert self._admit(readiness=None).admissible is False

    def test_missing_readiness_evidence_is_not_admissible(self):
        assert self._admit(readiness_evidence=None).admissible is False

    def test_unmet_readiness_condition_blocks_admission(self):
        a = self._admit(readiness_evidence=self._ready(R5_evidence_provenance=None))
        assert a.admissible is False and "R1-R8" in a.detail

    def test_readiness_task_id_mismatch_blocks_admission(self):
        a = self._admit(readiness=self._task(task_id="other__task-1"))
        assert a.admissible is False and "task_id" in a.detail

    def test_readiness_base_commit_mismatch_blocks_admission(self):
        a = self._admit(readiness=self._task(base_commit="f" * 40))
        assert a.admissible is False and "base_commit" in a.detail

    def test_readiness_relevant_file_set_mismatch_blocks_admission(self):
        a = self._admit(readiness=self._task(relevant_file_set=("other/file.py",)))
        assert a.admissible is False and "relevant_file_set" in a.detail

    def test_protocol_failure_blocks_admission(self):
        lying = _bundle(F2_TREATMENT_ARM, declared_step=3)
        a = self._admit(t_bundle=lying)
        assert a.admissible is False and a.protocol_state == STATE_SCIENTIFICALLY_INSUFFICIENT

    def test_tx_task_mismatch_blocks_admission(self):
        x = replace(_bundle(F2_CONTROL_ARM), instance_id="other__task-1")
        a = self._admit(x_bundle=x)
        assert a.admissible is False

    def test_gold_leak_blocks_admission(self):
        d = _bundle(F2_TREATMENT_ARM).to_dict()
        d["gold_diff"] = "diff --git a/x b/x"
        a = self._admit(t_bundle=d)
        assert a.admissible is False
