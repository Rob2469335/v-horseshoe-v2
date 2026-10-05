"""F2 execution-prerequisite readiness checker (F2-IMPL-AUTH-020).

Proves the checker (a) fails closed and names every blocker when evidence is
absent, (b) validates supplied evidence well-formedness, (c) never infers
no-egress from configuration, and (d) turns READY only when ALL prerequisites
are supplied. Uses explicit test-scoped fixtures; no real evidence.
"""
from __future__ import annotations

import hashlib

from qwen_train.f2_arm_primitives import render_block_from_records
from qwen_train.f2_readiness import (
    FROZEN_MIN_PAIRS,
    READINESS_ITEMS,
    evaluate_f2_readiness,
)
from runtime_v2.services.f2_freeze import LessonEntry, freeze_artifact

RULE = "Prefer pathlib over os.path in twine/package.py."
_HEX = "a" * 64


def _t_manifest():
    lessons = (
        LessonEntry(
            lesson_id="L1",
            lesson_hash=hashlib.sha256(RULE.encode()).hexdigest(),
            position=1,
            rule_text=RULE,
        ),
    )
    return freeze_artifact(
        rendered_artifact=render_block_from_records(lessons),
        arm="T",
        ordered_lessons=lessons,
        lesson_l_id="L1",
        lesson_l_hash=lessons[0].lesson_hash,
        freeze_timestamp=1.0,
    )


def _x_manifest():
    return freeze_artifact(
        rendered_artifact="# tool schema only (no lesson block)",
        arm="X",
        freeze_timestamp=1.0,
    )


def _all_env(tmp_path=None):
    env = {
        "SWARM_DISTILLER_MODEL": "qwen3.5-4b-distiller",
        "SWARM_DISTILLER_WEIGHTS_DIGEST": _HEX,
        "SWARM_F2_EVALUATOR_ID": "f2_experiment_j_v1",
        "SWARM_F2_EVALUATOR_VERSION": "1.0.0",
        "SWARM_F2_EVALUATOR_PROCEDURE": "f2_experiment_j_eval",
        "SWARM_F2_ARTIFACT_ROOT": "C:\\trusted\\f2_store",
        "SWARM_F2_ARTIFACT_RETENTION_DAYS": "365",
        "SWARM_RECEIPT_KEY": "test-scoped-receipt-key",
    }
    if tmp_path is not None:
        impl = tmp_path / "evaluator_impl.py"
        impl.write_bytes(b"# evaluator impl (test-scoped)\n")
        env["SWARM_F2_EVALUATOR_IMPL"] = str(impl)
    return env


def _all_supplied():
    return {
        "population": {"admitted": FROZEN_MIN_PAIRS},
        "base_artifacts": {"verified": True},
        "gold_artifacts": {"verified": True},
        "t_manifest": _t_manifest(),
        "x_manifest": _x_manifest(),
        "c0_manifest": _t_manifest(),  # any verify_manifest-valid artifact exercises the check
        "clean_room": {"isolated": True},
        "no_egress": {
            "https": "denied",
            "http": "denied",
            "tcp": "denied",
            "udp": "denied",
            "ipv6": "denied",
            "proxy": "denied",
            "loopback": "ok",
        },
        "authorizations": {"q10": True, "q12": True, "q13": True},
    }


class TestFailClosed:
    def test_empty_environment_is_not_ready(self):
        rep = evaluate_f2_readiness(env={}, supplied={})
        assert rep.ready is False
        assert len(rep.blockers) == len(READINESS_ITEMS) - 3  # 3 code-presence checks pass
        for c in rep.checks:
            if not c.satisfied:
                assert c.failure_behavior  # every blocker states its failure behavior

    def test_code_presence_is_satisfied_locally(self):
        rep = evaluate_f2_readiness(env={}, supplied={})
        by = {c.item: c for c in rep.checks}
        assert by["delivery_instrumentation"].satisfied
        assert by["regrade_bundle_verification"].satisfied
        assert by["statistical_analysis"].satisfied

    def test_operator_only_items_are_classified(self):
        rep = evaluate_f2_readiness(env={}, supplied={})
        by = {c.item: c for c in rep.checks}
        assert by["receipt_key"].status == "OPERATOR ACTION REQUIRED"
        assert by["q9_no_egress"].status == "OPERATOR ACTION REQUIRED"


class TestValidation:
    def test_bad_weights_digest_rejected(self):
        env = _all_env()
        env["SWARM_DISTILLER_WEIGHTS_DIGEST"] = "not-a-digest"
        by = {c.item: c for c in evaluate_f2_readiness(env=env, supplied={}).checks}
        assert by["weights_digest"].satisfied is False

    def test_relative_store_root_rejected(self):
        env = _all_env()
        env["SWARM_F2_ARTIFACT_ROOT"] = "relative/store"
        by = {c.item: c for c in evaluate_f2_readiness(env=env, supplied={}).checks}
        assert by["trusted_artifact_store"].satisfied is False

    def test_missing_evaluator_impl_rejected(self):
        env = _all_env()  # no impl path
        by = {c.item: c for c in evaluate_f2_readiness(env=env, supplied={}).checks}
        assert by["evaluator_implementation_digest"].satisfied is False

    def test_population_shortfall_blocks(self):
        s = _all_supplied()
        s["population"] = {"admitted": FROZEN_MIN_PAIRS - 1}
        by = {c.item: c for c in evaluate_f2_readiness(env=_all_env(), supplied=s).checks}
        assert by["protected_population"].satisfied is False

    def test_no_egress_config_is_not_evidence(self):
        s = _all_supplied()
        del s["no_egress"]
        by = {c.item: c for c in evaluate_f2_readiness(env=_all_env(), supplied=s).checks}
        assert by["q9_no_egress"].satisfied is False

    def test_partial_no_egress_probe_rejected(self):
        s = _all_supplied()
        s["no_egress"]["udp"] = "unknown"
        by = {c.item: c for c in evaluate_f2_readiness(env=_all_env(), supplied=s).checks}
        assert by["q9_no_egress"].satisfied is False

    def test_missing_authorization_blocks(self):
        s = _all_supplied()
        s["authorizations"] = {"q10": True, "q12": False, "q13": True}
        rep = evaluate_f2_readiness(env=_all_env(), supplied=s)
        by = {c.item: c for c in rep.checks}
        assert by["q10_q12_q13_authorization"].satisfied is False
        assert rep.ready is False


class TestFullySupplied:
    def test_all_prerequisites_supplied_is_ready(self, tmp_path):
        rep = evaluate_f2_readiness(env=_all_env(tmp_path), supplied=_all_supplied())
        assert rep.ready is True, rep.blockers
        assert rep.blockers == ()

    def test_report_never_contains_the_receipt_secret(self, tmp_path):
        rep = evaluate_f2_readiness(env=_all_env(tmp_path), supplied=_all_supplied())
        blob = str(rep.to_dict())
        assert "test-scoped-receipt-key" not in blob
