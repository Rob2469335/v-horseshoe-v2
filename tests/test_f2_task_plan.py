"""Tests for the deterministic pre-registration ordering (f2_task_plan).

Synthetic rows only.  Nothing here acquires, clones, installs, or executes a
task; the module under test has no process-launch capability at all.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_task_plan import (
    ORDERING_RULE_ID,
    SCHEMA_VERSION,
    PlanError,
    order_tasks,
    plan_for_position,
    registration_artifacts,
    static_os_plausibility,
)

_INPUTS = {"union_report_sha256": "a" * 64}


def _row(iid, created, **over):
    row = {
        "instance_id": iid,
        "created_at": created,
        "source": "swe-bench-live",
        "language": "python",
        "repo": "o/a",
        "test_cmd": "pytest -q",
    }
    row.update(over)
    return row


class TestOrderingRule:
    def test_created_at_descending_with_instance_id_tiebreak(self):
        rows = [
            _row("b__2", "2025-01-01T00:00:00"),
            _row("a__1", "2025-01-01T00:00:00"),
            _row("z__9", "2026-01-01T00:00:00"),
            _row("m__5", "2024-01-01T00:00:00"),
        ]
        ordered, excluded = order_tasks(rows)
        assert [r["instance_id"] for r in ordered] == ["z__9", "a__1", "b__2", "m__5"]
        assert excluded == []

    def test_is_order_independent_of_input_order(self):
        rows = [_row("a", "2025-01-01"), _row("b", "2025-01-02"), _row("c", "2025-01-03")]
        fwd, _ = order_tasks(rows)
        rev, _ = order_tasks(list(reversed(rows)))
        assert fwd == rev

    @pytest.mark.parametrize(
        "created,reason",
        [
            (None, "missing_created_at"),
            ("", "missing_created_at"),
            ("   ", "missing_created_at"),
            ("not-a-date", "invalid_created_at"),
            ("2025-13-45", "invalid_created_at"),
        ],
    )
    def test_missing_or_invalid_created_at_is_reported_not_invented(
        self, created, reason
    ):
        ordered, excluded = order_tasks([_row("a", created), _row("b", "2025-01-01")])
        assert [r["instance_id"] for r in ordered] == ["b"]
        assert excluded == [
            {"instance_id": "a", "reason": reason, "created_at": created}
        ]

    def test_missing_instance_id_is_reported(self):
        ordered, excluded = order_tasks([{"instance_id": "", "created_at": "2025-01-01"}])
        assert ordered == []
        assert excluded[0]["reason"] == "missing_instance_id"

    def test_unknown_rule_is_refused(self):
        with pytest.raises(PlanError, match="unknown ordering rule"):
            order_tasks([_row("a", "2025-01-01")], rule="random")

    def test_rule_identifier_is_stable(self):
        assert ORDERING_RULE_ID == "created_at_desc_instance_id_asc_v1"
        assert SCHEMA_VERSION == "f2_task_order_v1"


class TestRegistrationArtifacts:
    def test_content_digest_is_deterministic(self):
        rows = [_row("a", "2025-01-01"), _row("b", "2025-01-02")]
        o1, e1 = order_tasks(rows)
        o2, e2 = order_tasks(list(reversed(rows)))
        a1 = registration_artifacts(o1, e1, inputs=dict(_INPUTS), generated_at="T0")
        a2 = registration_artifacts(o2, e2, inputs=dict(_INPUTS), generated_at="T1")
        assert a1["content_sha256"] == a2["content_sha256"]
        assert a1["first_30_sha256"] == a2["first_30_sha256"]
        assert a1["generated_at"] != a2["generated_at"]  # documented nondeterminism

    def test_content_digest_changes_when_inputs_change(self):
        o, e = order_tasks([_row("a", "2025-01-01")])
        base = registration_artifacts(o, e, inputs=dict(_INPUTS), generated_at="T")
        other = registration_artifacts(
            o, e, inputs={"union_report_sha256": "b" * 64}, generated_at="T"
        )
        assert base["content_sha256"] != other["content_sha256"]

    def test_content_digest_changes_when_a_row_changes(self):
        o1, e1 = order_tasks([_row("a", "2025-01-01", language="python")])
        o2, e2 = order_tasks([_row("a", "2025-01-01", language="go")])
        a1 = registration_artifacts(o1, e1, inputs=dict(_INPUTS), generated_at="T")
        a2 = registration_artifacts(o2, e2, inputs=dict(_INPUTS), generated_at="T")
        assert a1["content_sha256"] != a2["content_sha256"]

    def test_first_30_is_the_head_of_the_full_list(self):
        rows = [_row(f"t-{i:03d}", f"2025-01-{(i % 27) + 1:02d}") for i in range(50)]
        o, e = order_tasks(rows)
        arts = registration_artifacts(o, e, inputs=dict(_INPUTS), top_n=30)
        assert len(arts["first_30"]) == 30
        assert [r["instance_id"] for r in arts["first_30"]] == [
            r["instance_id"] for r in arts["full_ordered_metadata"][:30]
        ]
        assert arts["counts"] == {"ordered": 50, "excluded": 0, "first_30": 30}

    def test_duplicate_identity_in_the_ordered_list_is_refused(self):
        o, e = order_tasks([_row("a", "2025-01-01"), _row("a", "2025-01-02")])
        # order_tasks does not deduplicate; the artifact layer must refuse it.
        with pytest.raises(PlanError, match="duplicate"):
            registration_artifacts(o, e, inputs=dict(_INPUTS))

    def test_top_n_must_be_positive(self):
        o, e = order_tasks([_row("a", "2025-01-01")])
        with pytest.raises(PlanError, match="top_n"):
            registration_artifacts(o, e, inputs=dict(_INPUTS), top_n=0)


class TestPlanOnlyInterface:
    def test_plan_shows_metadata_and_hashes_only(self):
        o, _ = order_tasks([_row("a", "2025-01-01", test_cmd="pytest -q")])
        plan = plan_for_position(o, 1, inputs=dict(_INPUTS))
        assert plan["position"] == 1
        assert plan["task_id"] == "a"
        assert plan["ordering_rule"] == ORDERING_RULE_ID
        assert len(plan["plan_sha256"]) == 64
        assert plan["pinned_inputs"] == _INPUTS

    def test_plan_declares_what_it_did_not_do(self):
        o, _ = order_tasks([_row("a", "2025-01-01")])
        plan = plan_for_position(o, 1)
        for op in (
            "clone_repository",
            "download_task_code",
            "install_dependencies",
            "run_task_command",
            "launch_vm",
            "run_tests",
            "produce_treatment_or_control_evidence",
            "mark_admitted",
            "mark_analyzable",
        ):
            assert op in plan["operations_not_performed"]
        assert plan["admission_state"] == "not_established"
        assert plan["execution_state"] == "not_established"
        assert "test_cmd" not in plan  # only its digest travels

    @pytest.mark.parametrize("n", [0, -1, 2, 99])
    def test_positions_outside_the_list_are_refused(self, n):
        o, _ = order_tasks([_row("a", "2025-01-01")])
        with pytest.raises(PlanError, match="outside"):
            plan_for_position(o, n)

    def test_plan_is_deterministic_for_the_same_position(self):
        o, _ = order_tasks([_row("a", "2025-01-01", language="python")])
        p1 = plan_for_position(o, 1, inputs=dict(_INPUTS))
        p2 = plan_for_position(o, 1, inputs=dict(_INPUTS))
        assert p1 == p2


class TestStaticOsPlausibility:
    @pytest.mark.parametrize(
        "cmd,label",
        [
            ("pytest -q", "both_plausible"),
            ("npm test && npm run lint", "both_plausible"),
            ("go test ./...", "both_plausible"),
            ("bash run.sh", "linux_plausible"),
            ("make test", "linux_plausible"),
            ("apt-get install -y x", "linux_plausible"),
            ("powershell -File t.ps1", "windows_plausible"),
            ("cmd /c dir", "windows_plausible"),
            ("bash x && powershell y", "ambiguous"),
            ("", "not_classifiable"),
            (None, "not_classifiable"),
        ],
    )
    def test_labels(self, cmd, label):
        assert static_os_plausibility(cmd) == label

    def test_label_is_a_string_inspection_not_an_execution(self):
        """A label is produced for a command that would be destructive if run."""
        assert static_os_plausibility("cmd /c del /q C:\\*") == "windows_plausible"
