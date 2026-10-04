"""Per-task frozen behavioral endpoint (F0 section 5; R7 / R11).

Proves the properties the F2 confirmatory endpoint depends on:

* the relevant file set is a parameter, not the F1 pilot constant;
* it is canonicalized, hashed and hash-verified (fail closed on mismatch);
* the horizon travels with the specification and is bounded to F0's [8, 12];
* a treatment-derived or evidence-free endpoint is REFUSED;
* detection is objective: action-based, no filesystem acceptance or repair
  correctness required (F1-OP-004-CLARIFICATION);
* an edit past the horizon is not an endpoint, and no later step can become one.

No F2 arm, model, backend or Qdrant is started.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_endpoint import (
    DEFAULT_HORIZON_STEPS,
    DISALLOWED_DERIVATION_SOURCES,
    EndpointError,
    FrozenEndpoint,
    assert_treatment_independent,
    endpoint_payload_hash,
    freeze_endpoint,
    qualifying_first_edit,
)


def _spec(paths=("src/module.py",), **kw) -> FrozenEndpoint:
    return freeze_endpoint(
        task_id=kw.pop("task_id", "repo__proj-1"),
        relevant_file_set=paths,
        derivation_evidence=kw.pop("derivation_evidence", ("f2p:tests/test_a.py::test_x",)),
        **kw,
    )


def _tc(step, operation, path, fn="filesystem"):
    return {
        "function_name": fn,
        "arguments": {"operation": operation, "path": path},
        "extra": {"step_id": step},
    }


class TestFreezeAndHash:
    def test_set_is_canonicalized_and_hashed(self):
        spec = _spec(paths=("src/b.py", "src/a.py"))
        assert spec.relevant_file_set == ("src/a.py", "src/b.py")
        assert len(spec.relevant_file_set_hash) == 64

    def test_order_does_not_change_the_hash(self):
        a = _spec(paths=("src/a.py", "src/b.py"))
        b = _spec(paths=("src/b.py", "src/a.py"))
        assert a.relevant_file_set_hash == b.relevant_file_set_hash

    def test_hash_mismatch_fails_closed(self):
        spec = _spec()
        with pytest.raises(EndpointError, match="hash"):
            FrozenEndpoint(
                task_id=spec.task_id,
                relevant_file_set=spec.relevant_file_set,
                relevant_file_set_hash="0" * 64,
                horizon_steps=spec.horizon_steps,
                derivation_evidence=spec.derivation_evidence,
            )

    def test_non_canonical_stored_set_is_refused(self):
        spec = _spec(paths=("src/a.py", "src/b.py"))
        with pytest.raises(EndpointError, match="canonical"):
            FrozenEndpoint(
                task_id=spec.task_id,
                relevant_file_set=("src/b.py", "src/a.py"),  # unsorted on purpose
                relevant_file_set_hash=spec.relevant_file_set_hash,
                horizon_steps=spec.horizon_steps,
                derivation_evidence=spec.derivation_evidence,
            )

    def test_unsafe_path_is_refused(self):
        with pytest.raises(EndpointError):
            _spec(paths=("../../etc/passwd",))

    def test_empty_set_is_refused(self):
        with pytest.raises(EndpointError):
            _spec(paths=())

    def test_task_id_is_required(self):
        with pytest.raises(EndpointError, match="task_id"):
            freeze_endpoint(task_id="", relevant_file_set=("a.py",))


class TestHorizon:
    def test_default_horizon_is_twelve(self):
        assert DEFAULT_HORIZON_STEPS == 12
        assert _spec().horizon_steps == 12

    @pytest.mark.parametrize("bad", [0, 7, 13, 100])
    def test_horizon_outside_f0_range_is_refused(self, bad):
        with pytest.raises(EndpointError, match="horizon_steps"):
            _spec(horizon_steps=bad)

    @pytest.mark.parametrize("ok", [8, 10, 12])
    def test_horizon_inside_f0_range_is_accepted(self, ok):
        assert _spec(horizon_steps=ok).horizon_steps == ok


class TestTreatmentIndependence:
    @pytest.mark.parametrize("source", sorted(DISALLOWED_DERIVATION_SOURCES))
    def test_treatment_derived_source_is_refused(self, source):
        with pytest.raises(EndpointError, match="not admissible"):
            _spec(derivation_source=source)

    def test_endpoint_without_evidence_fails_the_independence_assertion(self):
        spec = freeze_endpoint(
            task_id="t", relevant_file_set=("src/a.py",)
        )
        with pytest.raises(EndpointError, match="derivation evidence"):
            assert_treatment_independent(spec)

    def test_endpoint_with_evidence_passes(self):
        assert_treatment_independent(_spec()) is None

    def test_payload_hash_covers_horizon_and_derivation(self):
        a = _spec(horizon_steps=8)
        b = _spec(horizon_steps=12)
        # Same file set, different frozen horizon => different specification.
        assert a.relevant_file_set_hash == b.relevant_file_set_hash
        assert endpoint_payload_hash(a) != endpoint_payload_hash(b)


class TestDetection:
    def test_qualifying_edit_is_found(self):
        spec = _spec(paths=("src/module.py",))
        hit = qualifying_first_edit([_tc(4, "patch", "src/module.py")], spec)
        assert hit is not None
        assert hit["step_id"] == 4
        assert hit["relevant_file"] == "src/module.py"

    @pytest.mark.parametrize("op", ["write", "patch", "edit", "create"])
    def test_all_four_qualifying_operations(self, op):
        spec = _spec(paths=("src/module.py",))
        assert qualifying_first_edit([_tc(2, op, "src/module.py")], spec) is not None

    @pytest.mark.parametrize("op", ["read", "grep", "list", "move", ""])
    def test_non_qualifying_operations_are_ignored(self, op):
        spec = _spec(paths=("src/module.py",))
        assert qualifying_first_edit([_tc(2, op, "src/module.py")], spec) is None

    def test_non_filesystem_tool_is_ignored(self):
        spec = _spec(paths=("src/module.py",))
        assert qualifying_first_edit([_tc(2, "patch", "src/module.py", fn="shell")], spec) is None

    def test_edit_outside_the_frozen_set_is_ignored(self):
        """R11: the F1 set must not leak into another task's measurement."""
        spec = _spec(paths=("src/other.py",))
        assert qualifying_first_edit([_tc(2, "patch", "swarm_os/lib/paths.py")], spec) is None
        assert qualifying_first_edit([_tc(2, "patch", "src/other.py")], spec) is not None

    def test_first_qualifying_edit_wins(self):
        spec = _spec(paths=("src/module.py",))
        tcs = [_tc(7, "patch", "src/module.py"), _tc(3, "edit", "src/module.py")]
        assert qualifying_first_edit(tcs, spec)["step_id"] == 3

    def test_edit_beyond_horizon_is_not_an_endpoint(self):
        spec = _spec(paths=("src/module.py",), horizon_steps=12)
        assert qualifying_first_edit([_tc(13, "patch", "src/module.py")], spec) is None

    def test_no_later_step_can_become_an_endpoint_past_horizon(self):
        spec = _spec(paths=("src/module.py",), horizon_steps=12)
        tcs = [_tc(13, "patch", "src/module.py"), _tc(14, "edit", "src/module.py")]
        assert qualifying_first_edit(tcs, spec) is None

    def test_absolute_windows_workspace_path_matches(self):
        spec = _spec(paths=("src/module.py",))
        hit = qualifying_first_edit(
            [_tc(3, "patch", r"C:\ws\instance\repo\src\module.py")], spec
        )
        assert hit is not None and hit["step_id"] == 3

    def test_empty_trajectory_returns_none(self):
        assert qualifying_first_edit([], _spec()) is None

    def test_rejected_patch_still_qualifies(self):
        """F1-OP-004-CLARIFICATION: the ACTION is the endpoint."""
        spec = _spec(paths=("src/module.py",))
        tc = _tc(4, "patch", "src/module.py")
        tc["result"] = {"ok": False, "error": "read-before-write guard"}
        assert qualifying_first_edit([tc], spec) is not None

    def test_spec_roundtrips_through_dict(self):
        spec = _spec(paths=("src/module.py", "src/other.py"))
        again = FrozenEndpoint.from_dict(spec.to_dict())
        assert again == spec
        assert endpoint_payload_hash(again) == endpoint_payload_hash(spec)

    def test_from_dict_rejects_empty_payload(self):
        with pytest.raises(EndpointError):
            FrozenEndpoint.from_dict({})