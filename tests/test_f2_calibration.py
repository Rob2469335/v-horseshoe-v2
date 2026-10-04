"""No-lesson X/C0 calibration contract (R13).

The calibration is the cheapest high-value measurement available before
confirmatory collection, because the confirmatory design's feasibility depends
on two quantities only it can produce: the X-arm base rate at k = 12 (F1 reached
the endpoint in 10/10 valid no-lesson observations, so saturation is a live risk)
and the empirical discordance that sets required N.

This suite proves the contract that makes such a measurement trustworthy:

* a lesson-bearing arm is REFUSED (calibration must be lesson-free);
* an identity-less or duplicated record is refused;
* records measured by different endpoint specifications are refused;
* invalid records must state a reason (no silent missing data);
* reruns are permitted only for a predefined infrastructure cause;
* the summary computes every quantity the confirmatory plan depends on, and
  flags endpoint SATURATION explicitly rather than leaving it to be noticed.

The records here are labelled synthetic fixtures used to check arithmetic. They
are NOT experimental observations and no arm is executed.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_calibration import (
    CALIBRATION_ARMS,
    INFRASTRUCTURE_RERUN_CAUSES,
    CalibrationError,
    CalibrationRecord,
    summarize_calibration,
)

EP = "e" * 64


def _rec(**kw):
    base = dict(
        instance_id="pypa__twine-1066",
        arm="X",
        rollout_id="r-1",
        endpoint_hash=EP,
        horizon_steps=12,
        endpoint_observed=True,
        first_edit_step=4,
        valid=True,
    )
    base.update(kw)
    return CalibrationRecord(**base)


class TestLessonFreeContract:
    @pytest.mark.parametrize("arm", ["T", "Y", "Z", ""])
    def test_lesson_bearing_arm_is_refused(self, arm):
        with pytest.raises(CalibrationError, match="not calibration"):
            _rec(arm=arm)

    @pytest.mark.parametrize("arm", CALIBRATION_ARMS)
    def test_permitted_arms_are_accepted(self, arm):
        assert _rec(arm=arm).arm == arm


class TestIdentityAndIntegrity:
    def test_instance_id_required(self):
        with pytest.raises(CalibrationError, match="instance_id"):
            _rec(instance_id="")

    def test_rollout_id_required(self):
        with pytest.raises(CalibrationError, match="rollout_id"):
            _rec(rollout_id="")

    def test_endpoint_hash_required(self):
        with pytest.raises(CalibrationError, match="endpoint specification hash"):
            _rec(endpoint_hash="")

    def test_record_is_content_hashed(self):
        h = _rec().to_dict()["record_hash"]
        assert len(h) == 64
        assert h == _rec().to_dict()["record_hash"]

    def test_hash_changes_when_anything_changes(self):
        assert _rec().to_dict()["record_hash"] != _rec(first_edit_step=5).to_dict()[
            "record_hash"
        ]


class TestInvalidAndRerunRules:
    def test_invalid_record_must_state_a_reason(self):
        with pytest.raises(CalibrationError, match="invalid_reason"):
            _rec(valid=False, endpoint_observed=False, first_edit_step=None)

    def test_invalid_record_with_a_reason_is_accepted(self):
        r = _rec(
            valid=False,
            endpoint_observed=False,
            first_edit_step=None,
            invalid_reason="backend_unreachable",
        )
        assert r.valid is False

    def test_outcome_dependent_rerun_is_refused(self):
        with pytest.raises(CalibrationError, match="predefined"):
            _rec(rerun_of="r-0", rerun_cause="endpoint_too_slow")

    def test_predefined_infrastructure_rerun_is_accepted(self):
        for cause in sorted(INFRASTRUCTURE_RERUN_CAUSES):
            r = _rec(rerun_of="r-0", rerun_cause=cause)
            assert r.rerun_cause == cause

    def test_observed_endpoint_requires_a_step(self):
        with pytest.raises(CalibrationError, match="first_edit_step"):
            _rec(endpoint_observed=True, first_edit_step=None)

    def test_unobserved_endpoint_forbids_a_step(self):
        with pytest.raises(CalibrationError, match="must not carry"):
            _rec(endpoint_observed=False, first_edit_step=4)


class TestSummary:
    def test_empty_input_is_refused(self):
        with pytest.raises(CalibrationError, match="no calibration records"):
            summarize_calibration([])

    def test_duplicate_rollout_is_refused(self):
        with pytest.raises(CalibrationError, match="duplicate rollout_id"):
            summarize_calibration([_rec(), _rec(first_edit_step=5)])

    def test_mixed_endpoint_specifications_are_refused(self):
        with pytest.raises(CalibrationError, match="DIFFERENT endpoint"):
            summarize_calibration([_rec(), _rec(rollout_id="r-2", endpoint_hash="f" * 64)])

    def test_endpoint_hash_argument_is_cross_checked(self):
        with pytest.raises(CalibrationError, match="does not match"):
            summarize_calibration([_rec()], endpoint_hash="f" * 64)

    def test_summary_computes_every_required_quantity(self):
        recs = [
            _rec(rollout_id="r1", instance_id="t1", endpoint_observed=True, first_edit_step=3),
            _rec(rollout_id="r2", instance_id="t1", endpoint_observed=True, first_edit_step=5),
            _rec(rollout_id="r3", instance_id="t2", endpoint_observed=False, first_edit_step=None),
            _rec(
                rollout_id="r4",
                instance_id="t3",
                valid=False,
                endpoint_observed=False,
                first_edit_step=None,
                invalid_reason="backend_unreachable",
            ),
        ]
        s = summarize_calibration(recs)
        assert s.n_records == 4
        assert s.n_valid == 3
        assert s.n_invalid == 1
        assert s.endpoint_rate == pytest.approx(2 / 3)
        assert s.censoring_rate == pytest.approx(1 / 3)
        assert s.invalid_rate == pytest.approx(0.25)
        assert s.first_edit_steps == (3, 5)
        assert s.per_task_rates == {"t1": 1.0, "t2": 0.0}
        assert s.task_heterogeneity["n_tasks_observed"] == 2
        assert s.invalid_causes == {"backend_unreachable": 1}

    def test_rerun_causes_are_counted(self):
        recs = [
            _rec(rollout_id="r1", rerun_of="r0", rerun_cause="process_crash"),
            _rec(rollout_id="r2"),
        ]
        s = summarize_calibration(recs)
        assert s.rerun_causes == {"process_crash": 1}

    def test_saturation_is_flagged_explicitly(self):
        """The decision-relevant output: F1's 10/10 pattern must be detectable."""
        recs = [
            _rec(rollout_id=f"s{i}", endpoint_observed=True, first_edit_step=4)
            for i in range(6)
        ]
        s = summarize_calibration(recs)
        assert s.saturation_flag is True
        assert "no headroom" in s.saturation_detail

    def test_non_saturated_endpoint_does_not_flag(self):
        recs = [
            _rec(rollout_id=f"n{i}", endpoint_observed=(i < 2), first_edit_step=(4 if i < 2 else None))
            if i < 2
            else _rec(
                rollout_id=f"n{i}",
                endpoint_observed=False,
                first_edit_step=None,
            )
            for i in range(6)
        ]
        s = summarize_calibration(recs)
        assert s.saturation_flag is False
        assert "headroom exists" in s.saturation_detail

    def test_too_few_valid_records_declines_to_judge_saturation(self):
        s = summarize_calibration([_rec(rollout_id="a"), _rec(rollout_id="b")])
        assert s.saturation_flag is None
        assert "insufficient valid observations" in s.saturation_detail

    def test_summary_is_json_serialisable(self):
        import json

        s = summarize_calibration([_rec()])
        assert json.loads(json.dumps(s.to_dict()))["n_records"] == 1