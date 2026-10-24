"""Driver rule tests with a fake runner: screening, caps, halts, no out-of-pool."""

from __future__ import annotations

from qwen_train.f2_s8_driver import (
    DriverHalt,
    DriverLimits,
    run_driver,
    select_batch,
)


def _built(tmp_path, task_id: str) -> dict:
    ws = tmp_path / task_id
    ws.mkdir(exist_ok=True)
    return {"status": "built", "workspace": str(ws)}


def _limits(**over):
    base = dict(
        target_credited=300,
        max_executions=2,
        max_total_tasks=100,
        max_retries=5,
        batch_limit=50,
    )
    base.update(over)
    return DriverLimits(**base)


def _run(tmp_path, *, pool, classes, build, credited=0, limits=None, run_batch=None, check=lambda: None):
    calls = []

    def fake_batch(batch):
        calls.append(list(batch))
        return {"attempted": len(batch), "infrastructure_invalid": 0}

    result = run_driver(
        limits=limits or _limits(),
        pool_task_ids=pool,
        classes=classes,
        build_manifest=build,
        check_grant=check,
        credited_count=lambda: credited,
        run_batch=run_batch or fake_batch,
        log=lambda _: None,
    )
    return result, calls


def test_unknown_incompatible_unbuilt_refused(tmp_path):
    pool = ["c", "u", "i", "b"]
    classes = {"c": "OBSERVED_COMPATIBLE", "u": "UNKNOWN",
               "i": "OBSERVED_INCOMPATIBLE", "b": "OBSERVED_COMPATIBLE"}
    build = {"c": _built(tmp_path, "c"), "u": _built(tmp_path, "u"),
             "i": _built(tmp_path, "i")}
    assert select_batch(pool, classes, build) == ["c"]


def test_never_more_than_50_and_never_out_of_pool(tmp_path):
    pool = [f"t{i}" for i in range(60)]
    classes = {t: "OBSERVED_COMPATIBLE" for t in pool}
    build = {t: _built(tmp_path, t) for t in pool}
    seen = []

    def fake(batch):
        assert len(batch) <= 50
        assert set(batch) <= set(pool)
        seen.append(len(batch))
        return {"attempted": len(batch), "infrastructure_invalid": 0}

    result, _ = _run(
        tmp_path, pool=pool, classes=classes, build=build,
        limits=_limits(max_executions=1, max_total_tasks=100), run_batch=fake,
    )
    assert seen == [50]
    assert result.stop_reason == "CAPS_EXHAUSTED"


def test_stops_at_300_without_running(tmp_path):
    pool = ["c"]
    classes = {"c": "OBSERVED_COMPATIBLE"}
    build = {"c": _built(tmp_path, "c")}
    result, calls = _run(tmp_path, pool=pool, classes=classes, build=build, credited=300)
    assert result.stop_reason == "TARGET_REACHED"
    assert calls == []


def test_infra_failure_rate_halts(tmp_path):
    pool = [f"t{i}" for i in range(10)]
    classes = {t: "OBSERVED_COMPATIBLE" for t in pool}
    build = {t: _built(tmp_path, t) for t in pool}
    result, _ = _run(
        tmp_path, pool=pool, classes=classes, build=build,
        run_batch=lambda batch: {"attempted": 10, "infrastructure_invalid": 2},
    )
    assert result.stop_reason == "INFRA_FAILURE_RATE"


def test_revocation_between_batches_halts(tmp_path):
    pool = [f"t{i}" for i in range(4)]
    classes = {t: "OBSERVED_COMPATIBLE" for t in pool}
    build = {t: _built(tmp_path, t) for t in pool}
    state = {"calls": 0}

    def check():
        state["calls"] += 1
        if state["calls"] == 2:
            raise DriverHalt("REVOKED", "between batches")

    result, calls = _run(
        tmp_path, pool=pool, classes=classes, build=build,
        limits=_limits(max_total_tasks=4, max_executions=4), check=check,
    )
    assert result.stop_reason == "REVOKED"
    assert len(calls) == 1


def test_no_built_workspace_halts(tmp_path):
    pool = ["c"]
    classes = {"c": "OBSERVED_COMPATIBLE"}
    result, calls = _run(tmp_path, pool=pool, classes=classes, build={})
    assert result.stop_reason == "NO_ELIGIBLE_BUILT_TASK"
    assert calls == []


def test_stuck_claim_never_retried(tmp_path):
    pool = ["c"]
    classes = {"c": "OBSERVED_COMPATIBLE"}
    build = {"c": _built(tmp_path, "c")}
    calls = []

    def stuck(batch):
        calls.append(list(batch))
        return {"halt": "STUCK_CLAIM", "detail": "owner required"}

    result, _ = _run(
        tmp_path, pool=pool, classes=classes, build=build, run_batch=stuck
    )
    assert result.stop_reason == "STUCK_CLAIM"
    assert len(calls) == 1
