"""Registration digest-integrity tests (f2_task_order_v1 content binding).

Separate file so the digest contract is auditable on its own.  Synthetic rows
only; the module under test has no process-launch capability.

Contract under test: `content_sha256` must change whenever ANY substantive
registered metadata changes while the task identity is held constant, must be
stable across identical inputs, and `generated_at` is documented as the only
excluded field.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_task_plan import (
    order_tasks,
    registration_artifacts,
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


def _arts(rows, *, top_n=30, inputs=None, excluded=None):
    ordered, exc = order_tasks(rows)
    return registration_artifacts(
        ordered,
        exc if excluded is None else excluded,
        inputs=dict(inputs or _INPUTS),
        top_n=top_n,
        generated_at="T",
    )


class TestRegistrationDigestBinding:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("created_at", "2025-12-31T00:00:00"),
            ("repo", "other/repo"),
            ("language", "go"),
            ("test_cmd", "go test ./..."),
            ("source", "swe-rebench-v2"),
        ],
    )
    def test_substantive_ordered_metadata_changes_the_digest(self, field, value):
        iid = "a__b-1"
        base = _arts([_row(iid, "2025-01-01")])
        mutated = _arts([_row(iid, "2025-01-01", **{field: value})])
        assert base["content_sha256"] != mutated["content_sha256"], field
        assert base["first_30"][0]["instance_id"] == iid
        assert mutated["first_30"][0]["instance_id"] == iid

    def test_test_cmd_travels_only_as_a_digest(self):
        a = _arts([_row("a__b-1", "2025-01-01", test_cmd="pytest -q")])
        b = _arts([_row("a__b-1", "2025-01-01", test_cmd="pytest -x")])
        assert a["first_30"][0]["test_cmd_sha256"] != b["first_30"][0]["test_cmd_sha256"]
        assert a["content_sha256"] != b["content_sha256"]
        assert "test_cmd" not in a["first_30"][0]

    def test_pinned_input_digest_changes_the_digest(self):
        base = _arts([_row("a__b-1", "2025-01-01")])
        other = _arts([_row("a__b-1", "2025-01-01")],
                      inputs={"union_report_sha256": "b" * 64})
        assert base["content_sha256"] != other["content_sha256"]

    def test_top_n_changes_the_digest(self):
        base = _arts([_row("a__b-1", "2025-01-01")], top_n=30)
        other = _arts([_row("a__b-1", "2025-01-01")], top_n=31)
        assert base["content_sha256"] != other["content_sha256"]

    def test_ordering_rule_changes_the_digest(self, monkeypatch):
        import qwen_train.f2_task_plan as tp

        rows = [_row("a__b-1", "2025-01-01")]
        # Order OUTSIDE the patch: order_tasks validates its rule against the
        # same module constant, so the patch must only affect the content dict.
        ordered, excluded = order_tasks(rows)
        base = registration_artifacts(
            ordered, excluded, inputs=dict(_INPUTS), generated_at="T"
        )
        monkeypatch.setattr(tp, "ORDERING_RULE_ID", "created_at_asc_instance_id_asc_v1")
        mutated = registration_artifacts(
            ordered, excluded, inputs=dict(_INPUTS), generated_at="T"
        )
        assert base["content_sha256"] != mutated["content_sha256"]

    def test_exclusions_change_the_digest(self):
        rows = [_row("a__b-1", "2025-01-01")]
        base = _arts(rows)
        excluded = [
            {"instance_id": "x__y-1", "reason": "missing_created_at", "created_at": None}
        ]
        with_exc = _arts(rows, excluded=excluded)
        assert base["counts"]["excluded"] == 0
        assert with_exc["counts"]["excluded"] == 1
        assert base["content_sha256"] != with_exc["content_sha256"]

    def test_identical_inputs_reproduce_every_digest(self):
        rows = [_row("a__b-1", "2025-01-01"), _row("c__d-2", "2025-01-02")]
        a = _arts(rows)
        b = _arts(list(reversed(rows)))
        assert a["content_sha256"] == b["content_sha256"]
        for key in (
            "first_30_sha256",
            "full_ordered_metadata_sha256",
            "source_manifest_sha256",
            "ordering_rule_manifest_sha256",
            "excluded_sha256",
        ):
            assert a[key] == b[key], key

    def test_generated_at_is_excluded_from_the_content_digest(self):
        rows = [_row("a__b-1", "2025-01-01")]
        o, e = order_tasks(rows)
        a = registration_artifacts(o, e, inputs=dict(_INPUTS), generated_at="2000-01-01T00:00:00Z")
        b = registration_artifacts(o, e, inputs=dict(_INPUTS), generated_at="2030-01-01T00:00:00Z")
        assert a["generated_at"] != b["generated_at"]
        assert a["content_sha256"] == b["content_sha256"]
