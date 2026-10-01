"""F2 rediscovery classification tests — LEARNING_EXPERIMENT_STATE §10.3 item 5.

The rule under test is F0 §6 verbatim (`docs/EXPERIMENT_J.md:101-109`):

    step.timestamp < delivery_timestamp  ->  pre-delivery rediscovery

and §10.2 invariant H: the verdict must be a classification from recorded
timestamps/trace evidence, carrying enough evidence to show whether the relevant
filesystem action happened before or after delivery.

These tests do NOT re-define the rule; they pin F0's predicate, the strict
inequality, the recorded-precision behaviour, and the fail-closed paths.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qwen_train.f2_rediscovery import (  # noqa: E402
    REDISCOVERY_RULE,
    RediscoveryError,
    classify_rediscovery,
    parse_step_timestamp,
    step_is_edit,
)

# Derive every recorded timestamp from this base so the ISO strings and the
# float DELIVERY can never drift apart. DELIVERY is mid-second on purpose: that
# is the realistic case against a second-resolution step record.
BASE = 1_700_000_000.0
DELIVERY = BASE + 0.5


def _iso(offset_s: float) -> str:
    """A recorded (whole-second) ATIF step timestamp `offset_s` from BASE."""
    return datetime.fromtimestamp(BASE + offset_s, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


PRE_DELIVERY_STEP = _iso(-1.0)     # one whole second before delivery
POST_DELIVERY_STEP = _iso(+1.0)    # one whole second after delivery


def _step(ts: str, function_name: str = "filesystem.patch", step_id: int = 1) -> dict:
    return {
        "record_type": "step",
        "run_id": "traj-abc",
        "step_id": step_id,
        "timestamp": ts,
        "tool_calls": [
            {"tool_call_id": "c1", "function_name": function_name, "arguments": {}}
        ],
    }


class TestRuleIsF0Verbatim:
    def test_rule_string_is_f0_wording(self):
        assert REDISCOVERY_RULE == "step.timestamp < delivery_timestamp"


class TestStrictInequality:
    def test_edit_before_delivery_is_rediscovery(self):
        """F0 §6 case 1: edit strictly earlier -> rediscovery."""
        r = classify_rediscovery([_step(PRE_DELIVERY_STEP)], DELIVERY)
        # 2026-09-30T12:00:00Z parses to a value earlier than DELIVERY.
        assert r["rediscovery"] is True
        assert r["classification"] == "pre_delivery_rediscovery"
        assert r["excluded_from_causal_analysis"] is True

    def test_edit_after_delivery_is_not_rediscovery(self):
        """F0 §6 negative case: edit later -> not rediscovery."""
        r = classify_rediscovery([_step(POST_DELIVERY_STEP)], DELIVERY)
        assert r["rediscovery"] is False
        assert r["classification"] == "post_delivery_first_edit"
        assert r["excluded_from_causal_analysis"] is False

    def test_exact_timestamp_equality_is_not_rediscovery(self):
        """F0 says STRICTLY earlier; equality must NOT be rediscovery."""
        steps = [{"record_type": "step", "step_id": 1, "timestamp": DELIVERY,
                  "tool_calls": [{"function_name": "filesystem.patch"}]}]
        r = classify_rediscovery(steps, DELIVERY)
        assert r["rediscovery"] is False
        assert r["delta_seconds"] == 0.0

    def test_one_microsecond_earlier_is_rediscovery(self):
        steps = [{"record_type": "step", "step_id": 1, "timestamp": DELIVERY - 1e-6,
                  "tool_calls": [{"function_name": "filesystem.patch"}]}]
        assert classify_rediscovery(steps, DELIVERY)["rediscovery"] is True

    def test_earliest_edit_decides_not_latest(self):
        """The FIRST edit governs: a pre-delivery edit poisons the run even if a
        later post-delivery edit also occurred."""
        steps = [
            _step(PRE_DELIVERY_STEP, step_id=1),
            _step(POST_DELIVERY_STEP, step_id=2),
        ]
        r = classify_rediscovery(steps, DELIVERY)
        assert r["rediscovery"] is True
        assert r["step_id"] == 1
        assert r["edit_candidates"] == 2


class TestNoEditIsNotRediscovery:
    def test_no_steps_is_not_rediscovery(self):
        r = classify_rediscovery([], DELIVERY)
        assert r["rediscovery"] is False
        assert r["edit_observed"] is False
        assert r["excluded_from_causal_analysis"] is False

    def test_read_only_steps_are_not_edits(self):
        steps = [_step(PRE_DELIVERY_STEP, function_name="filesystem.read")]
        r = classify_rediscovery(steps, DELIVERY)
        assert r["edit_observed"] is False
        assert r["rediscovery"] is False

    def test_non_filesystem_tool_is_not_an_edit(self):
        steps = [_step(PRE_DELIVERY_STEP, function_name="web_search")]
        assert classify_rediscovery(steps, DELIVERY)["edit_observed"] is False


class TestEditDetection:
    @pytest.mark.parametrize("name", [
        "patch", "edit", "update", "modify",
        "write", "write_file", "create", "create_file",
        "filesystem.patch", "filesystem.write",
    ])
    def test_recognised_mutations(self, name):
        assert step_is_edit(_step("2026-09-30T12:00:00Z", function_name=name)) is True

    def test_filesystem_tool_with_action_argument(self):
        step = {"record_type": "step", "step_id": 1, "timestamp": DELIVERY,
                "tool_calls": [{"function_name": "filesystem",
                                "arguments": {"action": "write"}}]}
        assert step_is_edit(step) is True

    def test_unknown_filesystem_subaction_counts_as_edit(self):
        """Permissive by design: an unknown fs mutation can only bias toward
        rediscovery=True, which excludes a run — the conservative direction."""
        step = {"record_type": "step", "step_id": 1, "timestamp": DELIVERY,
                "tool_calls": [{"function_name": "filesystem.something_new"}]}
        assert step_is_edit(step) is True

    def test_summary_record_is_not_a_step(self):
        assert step_is_edit({"record_type": "summary",
                             "tool_calls": [{"function_name": "patch"}]}) is False

    def test_non_dict_is_not_a_step(self):
        assert step_is_edit("nope") is False


class TestTimestampParsing:
    def test_iso_z_form(self):
        assert parse_step_timestamp("1970-01-01T00:00:11Z") == 11.0

    def test_numeric_epoch(self):
        assert parse_step_timestamp(123.5) == 123.5

    def test_numeric_string(self):
        assert parse_step_timestamp("123.5") == 123.5

    def test_offset_form(self):
        assert parse_step_timestamp("1970-01-01T01:00:00+01:00") == 0.0

    @pytest.mark.parametrize("bad", [None, "", "   ", "not-a-time", True, False, {}])
    def test_malformed_fails_closed(self, bad):
        with pytest.raises(RediscoveryError):
            parse_step_timestamp(bad)

    def test_malformed_step_timestamp_fails_the_whole_verdict(self):
        """A malformed trace must fail loudly, never read as 'not rediscovery'."""
        steps = [{"record_type": "step", "step_id": 1, "timestamp": "garbage",
                  "tool_calls": [{"function_name": "filesystem.patch"}]}]
        with pytest.raises(RediscoveryError):
            classify_rediscovery(steps, DELIVERY)

    @pytest.mark.parametrize("bad", [None, "x", True, False])
    def test_non_numeric_delivery_timestamp_fails_closed(self, bad):
        with pytest.raises(RediscoveryError):
            classify_rediscovery([], bad)


class TestEvidenceRecording:
    """§10.2 invariant H: record enough to show before-vs-after."""

    def test_evidence_fields_present(self):
        steps = [_step(PRE_DELIVERY_STEP, step_id=7)]
        r = classify_rediscovery(steps, DELIVERY)
        for field in ("delivery_timestamp", "step_timestamp", "step_id",
                      "step_run_id", "delta_seconds", "rule",
                      "step_timestamp_resolution_s", "boundary_ambiguous",
                      "evidence_source", "classification"):
            assert field in r, f"missing evidence field: {field}"
        assert r["step_id"] == 7
        assert r["step_run_id"] == "traj-abc"
        assert r["delta_seconds"] < 0

    def test_boundary_ambiguous_flagged_within_one_step_period(self):
        """Recorded second-resolution truncation is surfaced, not hidden."""
        delivery = 1_700_000_000.5
        steps = [{"record_type": "step", "step_id": 1,
                  "timestamp": delivery - 0.2,  # inside the 1s window
                  "tool_calls": [{"function_name": "filesystem.patch"}]}]
        r = classify_rediscovery(steps, delivery)
        assert r["boundary_ambiguous"] is True
        assert r["step_timestamp_resolution_s"] == 1.0

    def test_not_ambiguous_when_far_apart(self):
        steps = [_step(PRE_DELIVERY_STEP)]
        assert classify_rediscovery(steps, DELIVERY)["boundary_ambiguous"] is False

    def test_evidence_source_declared(self):
        assert classify_rediscovery([], DELIVERY)["evidence_source"] == \
            "trajectory_step_records"


class TestNoCausalityClaim:
    def test_verdict_is_classification_not_proof(self):
        """Invariant H: recorded as a classification rule, not proof of causality."""
        r = classify_rediscovery([_step(PRE_DELIVERY_STEP)], DELIVERY)
        blob = " ".join(str(v) for v in r.values()).lower()
        for forbidden in ("proves", "caused by", "because the lesson",
                          "demonstrates causality"):
            assert forbidden not in blob

    def test_exclusion_flag_follows_f0_item_3(self):
        r = classify_rediscovery([_step(PRE_DELIVERY_STEP)], DELIVERY)
        assert r["excluded_from_causal_analysis"] is True
        r2 = classify_rediscovery([_step(POST_DELIVERY_STEP)], DELIVERY)
        assert r2["excluded_from_causal_analysis"] is False