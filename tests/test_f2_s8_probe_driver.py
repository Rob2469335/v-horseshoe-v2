"""Dry-run probe driver: fail-closed, fake builder, no network/build."""

from __future__ import annotations

from qwen_train.f2_s8_probe_driver import (
    ProbeHalt,
    ProbeLimits,
    run_probe,
    select_probe_batch,
)


def test_probe_refuses_without_authorization():
    calls = []

    def builder(batch):
        calls.append(batch)
        return {}

    result = run_probe(
        unknown_task_ids=["u1"],
        limits=ProbeLimits(max_tasks=10),
        authorize=lambda: (_ for _ in ()).throw(ProbeHalt("PROBE_NOT_AUTHORIZED")),
        builder=builder,
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
        limits=ProbeLimits(max_tasks=60, batch_limit=50),
        authorize=lambda: None,
        builder=builder,
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
        limits=ProbeLimits(max_tasks=10),
        authorize=lambda: None,
        builder=builder,
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
        limits=ProbeLimits(max_tasks=10),
        authorize=lambda: None,
        builder=builder,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_INFRA_FAILURE_RATE"
    assert result.infrastructure_invalid == 2


def test_probe_caps_exhausted():
    def builder(batch):
        return {tid: {"classification": "UNKNOWN"} for tid in batch}

    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(10)],
        limits=ProbeLimits(max_tasks=3),
        authorize=lambda: None,
        builder=builder,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_CAPS_EXHAUSTED"
    assert result.attempted == 3


def test_select_probe_batch_is_unknown_only_and_bounded():
    assert select_probe_batch(["u1", "u2"], limit=1) == ["u1"]
