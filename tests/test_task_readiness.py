"""Tests for the governed per-task readiness mechanism.

Engineering mechanism only: it validates/binds a task's declared
`relevant_file_set` and evaluates R1-R8 fail-closed. No F2 scientific values are
shipped here.
"""
from __future__ import annotations

import json

import pytest

from runtime_v2.services.task_readiness import (
    READINESS_CONDITIONS,
    ReadinessEvidence,
    TaskReadiness,
    TaskReadinessError,
    canonical_relevant_file_set,
    compute_relevant_file_set_hash,
    endpoint_measurable,
    evaluate_readiness,
)


class TestRelevantFileSet:
    def test_sorted_and_normalized(self):
        assert canonical_relevant_file_set(
            ["b/x.py", "a\\y.py", "a/y.py"]
        ) == ("a/y.py", "b/x.py")

    def test_deterministic_hash_and_order_insensitive(self):
        h1 = compute_relevant_file_set_hash(["b.py", "a.py"])
        h2 = compute_relevant_file_set_hash(["a.py", "b.py"])
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_changes_when_set_changes(self):
        assert compute_relevant_file_set_hash(["a.py"]) != compute_relevant_file_set_hash(["a.py", "b.py"])

    @pytest.mark.parametrize("bad", [
        [],
        [""],
        ["/etc/passwd"],
        ["C:/Windows/system32"],
        ["../secret.py"],
        ["a/../../b.py"],
        ["a b.py"],
        ["a;rm -rf.py"],
    ])
    def test_rejects_unsafe_or_empty(self, bad):
        with pytest.raises(TaskReadinessError):
            canonical_relevant_file_set(bad)


class TestTaskReadiness:
    def _tr(self, **over):
        base = dict(
            task_id="dbt-databricks-935",
            base_commit="abc123",
            relevant_file_set=("src/x.py",),
            fail_to_pass=("tests/test_x.py::test_a",),
            pass_to_pass=("tests/test_x.py::test_b",),
        )
        base.update(over)
        return TaskReadiness(**base)

    def test_round_trip(self):
        tr = self._tr()
        back = TaskReadiness.from_dict(tr.to_dict())
        assert back == tr
        assert back.relevant_file_set_hash == tr.relevant_file_set_hash

    def test_requires_task_and_commit(self):
        with pytest.raises(TaskReadinessError):
            TaskReadiness(task_id="", base_commit="abc", relevant_file_set=("a.py",))
        with pytest.raises(TaskReadinessError):
            TaskReadiness(task_id="t", base_commit="", relevant_file_set=("a.py",))

    def test_empty_file_set_rejected(self):
        with pytest.raises(TaskReadinessError):
            TaskReadiness(task_id="t", base_commit="c", relevant_file_set=())

    def test_from_dict_rejects_wrong_schema(self):
        d = self._tr().to_dict()
        d["schema_version"] = "other/9"
        with pytest.raises(TaskReadinessError):
            TaskReadiness.from_dict(d)

    def test_from_dict_rejects_tampered_hash(self):
        d = self._tr().to_dict()
        d["relevant_file_set_hash"] = "0" * 64
        with pytest.raises(TaskReadinessError):
            TaskReadiness.from_dict(d)

    def test_from_dict_rejects_non_mapping(self):
        with pytest.raises(TaskReadinessError):
            TaskReadiness.from_dict(None)  # type: ignore[arg-type]

    def test_load_rejects_missing_file(self, tmp_path):
        with pytest.raises(TaskReadinessError):
            TaskReadiness.load(tmp_path / "nope.json")

    def test_load_rejects_corrupt_file(self, tmp_path):
        p = tmp_path / "tr.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(TaskReadinessError):
            TaskReadiness.load(p)

    def test_load_round_trip(self, tmp_path):
        tr = self._tr()
        p = tmp_path / "tr.json"
        p.write_text(json.dumps(tr.to_dict()), encoding="utf-8")
        assert TaskReadiness.load(p) == tr


class TestReadinessGate:
    def test_all_true_is_ready(self):
        ev = ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS})
        v = evaluate_readiness(ev)
        assert v.ready is True
        assert v.unmet == ()

    def test_unknown_is_not_ready(self):
        """None = NOT ESTABLISHED -> fail closed, never a pass."""
        ev = ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS})
        ev = ReadinessEvidence(**{**{c: True for c in READINESS_CONDITIONS}, "R8_endpoint_measurable": None})
        v = evaluate_readiness(ev)
        assert v.ready is False
        assert "R8_endpoint_measurable" in v.unmet

    def test_false_is_not_ready(self):
        ev = ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS})
        ev = ReadinessEvidence(**{**{c: True for c in READINESS_CONDITIONS}, "R1_task_valid": False})
        v = evaluate_readiness(ev)
        assert v.ready is False
        assert "R1_task_valid" in v.unmet

    def test_empty_evidence_not_ready(self):
        v = evaluate_readiness(ReadinessEvidence())
        assert v.ready is False
        assert set(v.unmet) == set(READINESS_CONDITIONS)

    def test_rejects_wrong_type(self):
        with pytest.raises(TaskReadinessError):
            evaluate_readiness("nope")  # type: ignore[arg-type]


class TestEndpointMeasurable:
    def test_none_is_not_established(self):
        assert endpoint_measurable(None) is None

    def test_valid_declaration_is_true(self):
        tr = TaskReadiness(task_id="t", base_commit="c", relevant_file_set=("a.py",))
        assert endpoint_measurable(tr) is True

    def test_invalid_declaration_is_false(self, monkeypatch):
        tr = TaskReadiness(task_id="t", base_commit="c", relevant_file_set=("a.py",))
        # Simulate a frozen object whose set was corrupted after construction.
        object.__setattr__(tr, "relevant_file_set", ())
        assert endpoint_measurable(tr) is False
