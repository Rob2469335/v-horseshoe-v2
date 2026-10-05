"""F2 worker -> governed bundle wiring (F2-IMPL-AUTH-017).

Proves the bounded live-path step connects the worker's retained evidence to the
governed F2 bundle, an evaluator authorization, and an independent regrade, using
the REAL interfaces:

    worker evidence -> assemble_worker_bundle -> assemble_f2_bundle
        -> evaluator authorization -> regrade_f2 (independent)

The production evaluator identity/digest and trusted store are OPERATOR inputs;
these tests supply explicitly test-scoped ones (never a production claim) and, in
the fail-closed tests, prove the path refuses to run without them. No network, no
subprocess, no model call, no real evidence.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from qwen_train.f2_arm_worker import (
    F2_ARTIFACT_RETENTION_DAYS_ENV,
    F2_ARTIFACT_ROOT_ENV,
    F2_EVALUATOR_ID_ENV,
    F2_EVALUATOR_IMPL_ENV,
    F2_EVALUATOR_PROCEDURE_ENV,
    F2_EVALUATOR_VERSION_ENV,
    F2_TASK_OUTCOME_ENV,
    _behavioral_records_from_trajectory,
    _load_bundle_governance,
    _task_success_from_report,
    assemble_worker_bundle,
    emit_worker_bundle,
)
from qwen_train.f2_arm_primitives import render_block_from_records
from qwen_train.f2_governance import (
    EvaluatorAuthorization,
    EvaluatorRegistry,
    RetentionPolicy,
    TrustedArtifactStore,
)
from qwen_train.f2_protocol import F2_PROTOCOL_ID, regrade_f2
from runtime_v2.services.f2_freeze import LessonEntry, freeze_artifact
from runtime_v2.services.task_readiness import compute_relevant_file_set_hash

RFS = ["twine/package.py"]
TASK = "pypa__twine-1066"
REPO = "pypa/twine"
BASE = "4a1fc064a7899872ee845df6a8810bb51a6845ac"
RULE = "Prefer pathlib over os.path when joining paths in twine/package.py."
IMPL_BYTES = b"# f2_experiment_j_v1 evaluator (test-scoped)\n"
DELIVERY_TS = "2026-10-04T00:00:00Z"


def _lesson():
    return LessonEntry(
        lesson_id="L1",
        lesson_hash=hashlib.sha256(RULE.encode("utf-8")).hexdigest(),
        position=1,
        rule_text=RULE,
    )


def _manifest():
    lessons = (_lesson(),)
    rendered = render_block_from_records(lessons)
    readiness = {
        "task_id": TASK,
        "base_commit": BASE,
        "relevant_file_set": RFS,
        "relevant_file_set_hash": compute_relevant_file_set_hash(tuple(RFS)),
    }
    return (
        freeze_artifact(
            rendered_artifact=rendered,
            arm="T",
            ordered_lessons=lessons,
            lesson_l_id="L1",
            lesson_l_hash=lessons[0].lesson_hash,
            task_readiness=readiness,
            freeze_timestamp=1.0,
        ),
        rendered,
    )


def _trajectory(delivery_ts=DELIVERY_TS):
    return [
        {
            "record_type": "step",
            "step_id": 2,
            "timestamp": "2026-10-03T23:59:59Z",
            "tool_calls": [
                {"function_name": "filesystem",
                 "arguments": {"operation": "read", "path": "twine/package.py"}}
            ],
        },
        {
            "record_type": "step",
            "step_id": 4,
            "timestamp": "2026-10-04T00:00:05Z",
            "tool_calls": [
                {"function_name": "filesystem",
                 "arguments": {"operation": "patch", "path": "twine/package.py"}}
            ],
        },
    ]


def _auth():
    return EvaluatorAuthorization(
        evaluator_id="f2_test_evaluator",
        version="v1",
        implementation_digest=hashlib.sha256(IMPL_BYTES).hexdigest(),
        procedure_id="f2_experiment_j_eval",
        protocol_version=F2_PROTOCOL_ID,
    )


def _store(root):
    return TrustedArtifactStore(root, RetentionPolicy("f2_retention", 365, True))


def _seam(rendered, arm="T"):
    return {
        "arm": arm,
        "delivered_block": rendered,
        "lesson_block_hash": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "final_prompt_hash": "f" * 64,
        "delivery_timestamp": DELIVERY_TS,
    }


class TestBehavioralProjection:
    def test_step_tool_calls_are_projected(self):
        recs = _behavioral_records_from_trajectory(_trajectory())
        assert [r["step_id"] for r in recs] == [2, 4]
        assert recs[1]["function_name"] == "filesystem"
        assert recs[1]["operation"] == "patch"
        assert recs[1]["path"] == "twine/package.py"
        assert recs[0]["timestamp"] == "2026-10-03T23:59:59Z"

    def test_non_step_records_are_ignored(self):
        recs = _behavioral_records_from_trajectory(
            [{"record_type": "delivery_evidence", "arm": "coder"}]
        )
        assert recs == []


class TestTaskOutcomeDeclaration:
    def test_all_passed_is_success(self):
        assert _task_success_from_report({"fail_to_pass": {"a": "passed"}}) is True

    def test_any_non_passed_is_not_success(self):
        assert _task_success_from_report(
            {"fail_to_pass": {"a": "passed", "b": "failed"}}
        ) is False

    def test_missing_map_fails_closed(self):
        with pytest.raises(Exception):
            _task_success_from_report({})


class TestGovernedBundleChain:
    """worker evidence -> assemble -> independent regrade, over real interfaces."""

    def _assemble(self, root):
        manifest, rendered = _manifest()
        bundle = assemble_worker_bundle(
            store=_store(root),
            evaluator=_auth(),
            implementation_bytes=IMPL_BYTES,
            manifest=manifest,
            task_binding={"repo": REPO},
            seam_delivery=_seam(rendered),
            trajectory_records=_trajectory(),
            task_outcome_report={"fail_to_pass": {"tests/test_package.py::t": "passed"}},
            slug="wire_",
        )
        return bundle

    def test_bundle_regrades_verified(self, tmp_path):
        root = tmp_path / "store"
        root.mkdir()
        bundle = self._assemble(root)
        v = regrade_f2(
            bundle,
            store=_store(root),
            registry=EvaluatorRegistry([_auth()]),
            relevant_file_set=RFS,
            expected_arm="T",
        )
        assert v.ok, v.detail

    def test_endpoint_is_reconstructed_from_the_trajectory(self, tmp_path):
        root = tmp_path / "store"
        root.mkdir()
        bundle = self._assemble(root)
        assert bundle.declared_endpoint is True
        assert bundle.declared_first_edit_step == 4
        assert bundle.instance_id == TASK
        assert bundle.base_commit == BASE
        assert bundle.repository == REPO

    def test_producer_declaration_disagreement_is_rejected(self, tmp_path):
        """A bundle whose declaration disagrees with evidence must not verify."""
        from dataclasses import replace

        root = tmp_path / "store"
        root.mkdir()
        bundle = self._assemble(root)
        liar = replace(bundle, declared_first_edit_step=99)
        v = regrade_f2(
            liar,
            store=_store(root),
            registry=EvaluatorRegistry([_auth()]),
            relevant_file_set=RFS,
            expected_arm="T",
        )
        assert not v.ok

    def test_missing_task_readiness_fails_closed(self, tmp_path):
        manifest, rendered = _manifest()
        no_readiness = freeze_artifact(
            rendered_artifact=rendered,
            arm="T",
            ordered_lessons=manifest.ordered_lessons,
            lesson_l_id="L1",
            lesson_l_hash=manifest.lesson_l_hash,
        )
        with pytest.raises(Exception, match="task_readiness"):
            assemble_worker_bundle(
                store=_store(tmp_path / "s"),
                evaluator=_auth(),
                implementation_bytes=IMPL_BYTES,
                manifest=no_readiness,
                task_binding={"repo": REPO},
                seam_delivery=_seam(rendered),
                trajectory_records=_trajectory(),
                task_outcome_report={"fail_to_pass": {"a": "passed"}},
                slug="nc_",
            )


class TestEmitWorkerBundleEndToEnd:
    """The full emit path: config -> trajectory -> assemble -> regrade -> persist."""

    def _configure(self, tmp_path, monkeypatch, *, impl=True, report=True):
        impl_path = tmp_path / "evaluator_impl.py"
        impl_path.write_bytes(IMPL_BYTES)
        report_path = tmp_path / "task_outcome.json"
        report_path.write_text(
            json.dumps({"fail_to_pass": {"tests/test_package.py::t": "passed"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv(F2_ARTIFACT_ROOT_ENV, str(tmp_path / "store"))
        monkeypatch.setenv(F2_ARTIFACT_RETENTION_DAYS_ENV, "365")
        monkeypatch.setenv(F2_EVALUATOR_ID_ENV, "f2_test_evaluator")
        monkeypatch.setenv(F2_EVALUATOR_VERSION_ENV, "v1")
        monkeypatch.setenv(F2_EVALUATOR_IMPL_ENV, str(impl_path))
        monkeypatch.setenv(F2_EVALUATOR_PROCEDURE_ENV, "f2_experiment_j_eval")
        monkeypatch.setenv(F2_TASK_OUTCOME_ENV, str(report_path))
        monkeypatch.delenv("SWARM_F2_TRAJ_DIR", raising=False)

    def test_emit_persists_a_regraded_bundle(self, tmp_path, monkeypatch):
        self._configure(tmp_path, monkeypatch)
        manifest, rendered = _manifest()
        ws = tmp_path / "ws"
        run_id = "run-abc"
        traj_dir = ws / "data" / "trajectories"
        traj_dir.mkdir(parents=True)
        (traj_dir / f"{run_id}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in _trajectory()), encoding="utf-8"
        )

        result = emit_worker_bundle(
            manifest=manifest,
            task_binding={"repo": REPO},
            p2_evidence={
                "final_prompt_hash": "f" * 64,
                "delivery_timestamp": DELIVERY_TS,
            },
            delivered_actual=rendered,
            workspace_root=ws,
            rollout_id="roll-1",
            trajectory_run_id=run_id,
            system_prompt="",
        )
        assert result["regrade_state"] == "VERIFIED", result
        from pathlib import Path

        assert Path(result["bundle_path"]).is_file()
        assert len(result["bundle_digest"]) == 64

    def test_missing_governance_inputs_fail_closed(self, monkeypatch):
        for env, _ in (
            (F2_ARTIFACT_ROOT_ENV, ""),
            (F2_ARTIFACT_RETENTION_DAYS_ENV, ""),
            (F2_EVALUATOR_ID_ENV, ""),
            (F2_EVALUATOR_VERSION_ENV, ""),
            (F2_EVALUATOR_IMPL_ENV, ""),
            (F2_EVALUATOR_PROCEDURE_ENV, ""),
            (F2_TASK_OUTCOME_ENV, ""),
        ):
            monkeypatch.delenv(env, raising=False)
        with pytest.raises(Exception, match=F2_ARTIFACT_ROOT_ENV):
            _load_bundle_governance()

    def test_non_absolute_artifact_root_fails_closed(self, tmp_path, monkeypatch):
        self._configure(tmp_path, monkeypatch)
        monkeypatch.setenv(F2_ARTIFACT_ROOT_ENV, "relative/store")
        with pytest.raises(Exception, match="absolute"):
            _load_bundle_governance()

    def test_missing_trajectory_fails_closed(self, tmp_path, monkeypatch):
        self._configure(tmp_path, monkeypatch)
        manifest, rendered = _manifest()
        with pytest.raises(Exception, match="trajectory not found"):
            emit_worker_bundle(
                manifest=manifest,
                task_binding={"repo": REPO},
                p2_evidence={"delivery_timestamp": DELIVERY_TS, "final_prompt_hash": "f" * 64},
                delivered_actual=rendered,
                workspace_root=tmp_path / "ws",
                rollout_id="roll-1",
                trajectory_run_id="missing-run",
                system_prompt="",
            )
