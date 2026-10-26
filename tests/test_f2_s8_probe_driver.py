"""Dry-run probe driver: fail-closed, fake builder, no network/build."""

from __future__ import annotations

import pytest

from qwen_train.f2_s8_probe_driver import (
    CleanupClassificationUnrecognized,
    ProbeHalt,
    ProbeLimits,
    cleanup_probe_task,
    run_probe,
    select_probe_batch,
)

FREE = lambda: 10**18  # noqa: E731 - test-only fixture


def _limits(max_tasks: int, **over) -> ProbeLimits:
    base = dict(
        max_tasks=max_tasks,
        disk_floor_bytes=1,
        per_task_disk_bytes=1,
    )
    base.update(over)
    return ProbeLimits(**base)


def test_probe_refuses_without_authorization():
    calls = []

    def builder(batch):
        calls.append(batch)
        return {}

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10),
        authorize=lambda: (_ for _ in ()).throw(ProbeHalt("PROBE_NOT_AUTHORIZED")),
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_NOT_AUTHORIZED"
    assert calls == []


def test_probe_batches_never_exceed_50():
    seen = []

    def builder(batch):
        seen.append(len(batch))
        return {tid: {"classification": "UNKNOWN"} for tid in batch}

    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(60)],
        limits=_limits(60, batch_limit=50),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert seen == [50, 10]
    assert result.stop_reason == "PROBE_COMPLETE"
    assert result.attempted == 60


def test_probe_counts_classification_and_built_workspace(tmp_path):
    ws = tmp_path / "u1"
    ws.mkdir()

    def builder(batch):
        return {
            "u1": {"classification": "OBSERVED_COMPATIBLE", "workspace": str(ws)},
            "u2": {"classification": "OBSERVED_INCOMPATIBLE"},
            "u3": {"classification": "UNKNOWN"},
        }

    result = run_probe(
        unknown_task_ids=["u1", "u2", "u3"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert (result.compatible, result.incompatible, result.unknown, result.built) == (
        1, 1, 1, 1
    )


def test_probe_halts_on_infra_rate():
    def builder(batch):
        return {
            tid: {
                "classification": "UNKNOWN",
                "failure": "builder_failure" if i < 2 else "",
            }
            for i, tid in enumerate(batch)
        }

    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(10)],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_INFRA_FAILURE_RATE"
    assert result.infrastructure_invalid == 2


def test_probe_caps_exhausted():
    def builder(batch):
        return {tid: {"classification": "UNKNOWN"} for tid in batch}

    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(10)],
        limits=_limits(3),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_CAPS_EXHAUSTED"
    assert result.attempted == 3


def test_probe_disk_low_halts_before_builder():
    calls = []

    def builder(batch):
        calls.append(batch)
        return {}

    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(50)],
        limits=_limits(50, disk_floor_bytes=10**12, per_task_disk_bytes=10**9),
        authorize=lambda: None,
        builder=builder,
        free_bytes=lambda: 0,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_DISK_LOW"
    assert calls == []


def test_select_probe_batch_is_unknown_only_and_bounded():
    assert select_probe_batch(["u1", "u2"], limit=1) == ["u1"]


def test_cleanup_failure_halts_the_probe():
    """A refused cleanup must stop the run, not log a warning and continue."""

    def builder(batch):
        return {tid: {"classification": "UNKNOWN"} for tid in batch}

    def refused_cleanup(task_id, row):
        raise RuntimeError(f"cleanup refused for {task_id}")

    result = run_probe(
        unknown_task_ids=["u1", "u2"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        cleanup_after_task=refused_cleanup,
        log=lambda _: None,
    )
    assert result.stop_reason == "CLEANUP_FAILED"
    assert "cleanup refused for u1" in result.detail


# ---------------------------------------------------------------------------
# S5: classification contract and fail-closed handling
# ---------------------------------------------------------------------------
def _cleanup_harness(tmp_path, monkeypatch, classification, *, failure=None):
    import qwen_train.f2_s8_cleanup as cleanup

    calls = []
    monkeypatch.setattr(
        cleanup, "cleanup_failed_build", lambda **kw: calls.append(("failed", kw))
    )
    monkeypatch.setattr(
        cleanup, "cleanup_probe_workspace", lambda **kw: calls.append(("probe", kw))
    )
    root = tmp_path / "ws"
    task = "acme__widget-1"
    (root / task).mkdir(parents=True, exist_ok=True)
    row = {"classification": classification}
    if failure is not None:
        row["failure"] = failure
    try:
        cleanup_probe_task(
            task,
            row,
            workspace_root=root,
            output_dir=tmp_path,
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=False,
        )
        outcome = "ok"
    except CleanupClassificationUnrecognized:
        outcome = "refused"
    return calls, outcome


def test_cleanup_classification_contract_maps_to_approved_actions(tmp_path, monkeypatch):
    calls, outcome = _cleanup_harness(tmp_path, monkeypatch, "OBSERVED_COMPATIBLE")
    assert outcome == "ok" and calls == []

    calls, outcome = _cleanup_harness(tmp_path, monkeypatch, "UNKNOWN")
    assert outcome == "ok" and calls == []

    for value in ("BUILD_FAILURE", "OBSERVED_BUILD_FAILURE"):
        calls, outcome = _cleanup_harness(tmp_path, monkeypatch, value)
        assert outcome == "ok"
        assert [kind for kind, _ in calls] == ["failed"]

    calls, outcome = _cleanup_harness(tmp_path, monkeypatch, "OBSERVED_INCOMPATIBLE")
    assert outcome == "ok"
    assert [kind for kind, _ in calls] == ["probe"]


@pytest.mark.parametrize("value", ["SOMETHING_ELSE", "", "  ", None, 123])
def test_cleanup_unrecognized_classification_is_refused_before_any_call(
    tmp_path, monkeypatch, value
):
    calls, outcome = _cleanup_harness(tmp_path, monkeypatch, value)
    assert outcome == "refused"
    assert calls == []


def test_cleanup_compatible_with_failure_flag_is_refused(tmp_path, monkeypatch):
    calls, outcome = _cleanup_harness(
        tmp_path, monkeypatch, "OBSERVED_COMPATIBLE", failure="builder_failure"
    )
    assert outcome == "refused"
    assert calls == []


def test_unrecognized_classification_halts_without_cleanup_callback():
    def builder(batch):
        return {tid: {"classification": "NOT_A_REAL_CLASS"} for tid in batch}

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        cleanup_after_task=None,
        log=lambda _: None,
    )
    assert result.stop_reason == "CLEANUP_CLASSIFICATION_UNRECOGNIZED"


def test_missing_classification_halts_without_cleanup_callback():
    def builder(batch):
        return {tid: {} for tid in batch}

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        cleanup_after_task=None,
        log=lambda _: None,
    )
    assert result.stop_reason == "CLEANUP_CLASSIFICATION_UNRECOGNIZED"


def test_missing_builder_is_a_hard_prerequisite_failure():
    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=None,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_BUILDER_UNAVAILABLE"
    assert result.stop_reason != "PROBE_COMPLETE"


def test_builder_unavailable_is_not_unknown():
    from qwen_train.f2_s8_probe_driver import BuilderUnavailable

    def builder(batch):
        raise BuilderUnavailable("guest builder not provisioned")

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_BUILDER_UNAVAILABLE"
    assert result.unknown == 0


def test_builder_missing_task_is_a_contract_failure():
    def builder(batch):
        return {}

    result = run_probe(
        unknown_task_ids=["u1", "u2"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"


def test_builder_extra_task_is_a_contract_failure():
    def builder(batch):
        return {tid: {"classification": "UNKNOWN"} for tid in [*batch, "extra"]}

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"


def test_build_cell_task_id_mismatch_is_a_contract_failure():
    from qwen_train.f2_s8_probe_driver import BuildCell

    def builder(batch):
        return {"u1": BuildCell("other", "UNKNOWN")}

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"


def _compat(tmp_path, task_id):
    ws = tmp_path / task_id
    ws.mkdir(exist_ok=True)
    return {"classification": "OBSERVED_COMPATIBLE", "workspace": str(ws)}


def test_target_compatible_stops_before_the_next_batch(tmp_path):
    tasks = ["u1", "u2"]
    calls = []

    def builder(batch):
        calls.append(list(batch))
        return {tid: _compat(tmp_path, tid) for tid in batch}

    result = run_probe(
        unknown_task_ids=tasks,
        limits=_limits(10, batch_limit=1, target_compatible=1),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_TARGET_REACHED"
    assert result.built == 1
    assert calls == [["u1"]]


def test_target_not_reached_runs_all_tasks(tmp_path):
    tasks = ["u1", "u2"]

    def builder(batch):
        return {tid: _compat(tmp_path, tid) for tid in batch}

    result = run_probe(
        unknown_task_ids=tasks,
        limits=_limits(10, target_compatible=5),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_COMPLETE"
    assert result.built == 2


def test_target_does_not_count_compatible_without_a_workspace():
    def builder(batch):
        return {tid: {"classification": "OBSERVED_COMPATIBLE"} for tid in batch}

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=_limits(10, target_compatible=1),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_COMPLETE"
    assert result.compatible == 1
    assert result.built == 0


def test_execution_cap_stops_after_the_cap(tmp_path):
    tasks = ["u1", "u2"]
    calls = []

    def builder(batch):
        calls.append(list(batch))
        return {tid: {"classification": "UNKNOWN"} for tid in batch}

    result = run_probe(
        unknown_task_ids=tasks,
        limits=_limits(10, batch_limit=1, max_executions=1),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_EXECUTION_CAP"
    assert calls == [["u1"]]


def test_negative_stop_limits_are_rejected():
    with pytest.raises(ValueError):
        ProbeLimits(max_tasks=1, target_compatible=-1)
    with pytest.raises(ValueError):
        ProbeLimits(max_tasks=1, max_executions=-1)


def test_dry_run_builder_is_explicitly_unknown():
    from qwen_train.f2_s8_probe_driver import DryRunBuilder

    rows = DryRunBuilder()(["u1"])
    assert rows["u1"].classification == "UNKNOWN"
    assert rows["u1"].workspace is None


def test_probe_halts_on_unrecognized_cleanup_classification(tmp_path, monkeypatch):
    import qwen_train.f2_s8_cleanup as cleanup

    calls = []
    monkeypatch.setattr(
        cleanup, "cleanup_failed_build", lambda **kw: calls.append(("failed", kw))
    )
    monkeypatch.setattr(
        cleanup, "cleanup_probe_workspace", lambda **kw: calls.append(("probe", kw))
    )
    root = tmp_path / "ws"
    task = "acme__widget-1"
    (root / task).mkdir(parents=True, exist_ok=True)

    def builder(batch):
        return {tid: {"classification": "NOT_A_REAL_CLASS"} for tid in batch}

    def cleanup_task(task_id, row):
        cleanup_probe_task(
            task_id,
            row,
            workspace_root=root,
            output_dir=tmp_path,
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=False,
        )

    result = run_probe(
        unknown_task_ids=[task],
        limits=_limits(10),
        authorize=lambda: None,
        builder=builder,
        free_bytes=FREE,
        verify_ledgers=lambda: None,
        cleanup_after_task=cleanup_task,
        log=lambda _: None,
    )
    assert result.stop_reason == "CLEANUP_CLASSIFICATION_UNRECOGNIZED"
    assert result.stop_reason != "PROBE_COMPLETE"
    assert calls == []
