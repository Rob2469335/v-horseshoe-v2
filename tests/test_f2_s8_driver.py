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
        disk_floor_bytes=10**9,
        per_task_disk_bytes=10**6,
        batch_limit=50,
    )
    base.update(over)
    return DriverLimits(**base)


def _run(tmp_path, *, pool, classes, build, credited=0, limits=None, run_batch=None, check=lambda: None, free=lambda: 10**18, cleanup=None, refresh=None):
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
        free_bytes=free,
        refresh_terminal=refresh,
        cleanup_after_batch=cleanup,
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

def test_disk_low_halts_before_batch(tmp_path):
    pool = ["c"]
    classes = {"c": "OBSERVED_COMPATIBLE"}
    build = {"c": _built(tmp_path, "c")}
    result, calls = _run(
        tmp_path, pool=pool, classes=classes, build=build,
        limits=_limits(disk_floor_bytes=10**12, per_task_disk_bytes=10**9),
        free=lambda: 0,
    )
    assert result.stop_reason == "DISK_LOW"
    assert calls == []


def test_disk_above_threshold_proceeds(tmp_path):
    pool = ["c"]
    classes = {"c": "OBSERVED_COMPATIBLE"}
    build = {"c": _built(tmp_path, "c")}
    result, calls = _run(
        tmp_path, pool=pool, classes=classes, build=build,
        limits=_limits(max_executions=1, disk_floor_bytes=1, per_task_disk_bytes=1),
        free=lambda: 10**18,
    )
    assert calls == [["c"]]
    assert result.stop_reason == "CAPS_EXHAUSTED"

def _cleanup_env(tmp_path):
    import hashlib

    from qwen_train.f2_s8_factory import REQUIRED_EVIDENCE_ROLES, AppendOnlyLedger

    task = "acme__widget-1"
    root = tmp_path / "ws"
    ws = root / task
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "junit.xml").write_text("<testsuite/>", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    role_files = {}
    artifacts = {}
    for role in REQUIRED_EVIDENCE_ROLES:
        p = evidence_root / task / f"{role}.bin"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"payload-{role}", encoding="utf-8")
        role_files[role] = p
        artifacts[role] = {
            "name": p.name,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "size_bytes": p.stat().st_size,
        }
    ledger = tmp_path / "ledger.jsonl"
    AppendOnlyLedger(ledger).append(
        {
            "task_id": task,
            "outcome": "base_fail_gold_pass",
            "outcome_class": "scientific",
            "admitted": True,
            "evidence": dict(artifacts["gold_report"]),
            "evidence_bundle": {"artifacts": artifacts},
        }
    )
    manifest = tmp_path / "deletion.jsonl"
    return task, root, ws, evidence_root, role_files, ledger, manifest


def test_cleanup_after_verified_batch_removes_workspace(tmp_path):
    from qwen_train.f2_s8_cleanup import delete_workspace
    task, root, ws, evidence_root, role_files, ledger, manifest = _cleanup_env(tmp_path)

    def cleanup(batch, outcome):
        delete_workspace(
            task_id=task,
            workspace=ws,
            allowed_roots=[root],
            ledger_paths=[ledger],
            evidence_root=evidence_root,
            evidence_files=dict(role_files),
            manifest_path=manifest,
            allow_delete=True,
        )

    result, calls = _run(
        tmp_path, pool=[task], classes={task: "OBSERVED_COMPATIBLE"},
        build={task: {"status": "built", "workspace": str(ws)}},
        limits=_limits(max_executions=1, disk_floor_bytes=1, per_task_disk_bytes=1),
        cleanup=cleanup,
    )
    assert calls == [[task]]
    assert not ws.exists()


def test_cleanup_missing_evidence_keeps_workspace(tmp_path):
    from qwen_train.f2_s8_cleanup import delete_workspace
    task, root, ws, evidence_root, role_files, ledger, manifest = _cleanup_env(tmp_path)
    role_files["gold_report"].write_text("tampered", encoding="utf-8")

    def cleanup(batch, outcome):
        delete_workspace(
            task_id=task,
            workspace=ws,
            allowed_roots=[root],
            ledger_paths=[ledger],
            evidence_root=evidence_root,
            evidence_files=dict(role_files),
            manifest_path=manifest,
            allow_delete=True,
        )

    result, _ = _run(
        tmp_path, pool=[task], classes={task: "OBSERVED_COMPATIBLE"},
        build={task: {"status": "built", "workspace": str(ws)}},
        limits=_limits(max_executions=1, disk_floor_bytes=1, per_task_disk_bytes=1),
        cleanup=cleanup,
    )
    assert ws.exists()
    assert any("CLEANUP_WARNING" in line for line in result.log)

def test_second_batch_excludes_first_batch_tasks(tmp_path):
    tasks = [f"t{i}" for i in range(4)]
    classes = {t: "OBSERVED_COMPATIBLE" for t in tasks}
    build = {t: _built(tmp_path, t) for t in tasks}
    done = set()
    seen = []

    def run_batch(batch):
        seen.append(list(batch))
        done.update(batch)
        return {
            "attempted": len(batch),
            "infrastructure_invalid": 0,
            "task_results": {
                t: {"outcome": "base_fail_gold_pass", "outcome_class": "scientific"}
                for t in batch
            },
        }

    result, _ = _run(
        tmp_path, pool=tasks, classes=classes, build=build,
        limits=_limits(max_executions=2, max_total_tasks=4, batch_limit=2),
        run_batch=run_batch, refresh=lambda: set(done),
    )
    assert len(seen) == 2
    assert not set(seen[0]) & set(seen[1])
    assert set(seen[0]) | set(seen[1]) == set(tasks)


def test_infrastructure_failure_remains_retryable(tmp_path):
    tasks = [f"t{i}" for i in range(10)]
    classes = {t: "OBSERVED_COMPATIBLE" for t in tasks}
    build = {t: _built(tmp_path, t) for t in tasks}
    done = set()
    seen = []

    def run_batch(batch):
        seen.append(list(batch))
        done.update(t for t in batch if t != "t0")
        return {
            "attempted": len(batch),
            "infrastructure_invalid": 1 if "t0" in batch else 0,
            "task_results": {
                t: (
                    {"outcome": "infrastructure_failure", "outcome_class": "infrastructure"}
                    if t == "t0"
                    else {"outcome": "base_fail_gold_pass", "outcome_class": "scientific"}
                )
                for t in batch
            },
        }

    result, _ = _run(
        tmp_path, pool=tasks, classes=classes, build=build,
        limits=_limits(max_executions=2, max_total_tasks=20, batch_limit=10),
        run_batch=run_batch, refresh=lambda: set(done),
    )
    assert seen[0] == tasks
    assert seen[1] == ["t0"]
    assert result.stop_reason == "INFRA_FAILURE_RATE"


def test_recorded_terminal_task_is_not_reselected(tmp_path):
    tasks = ["t0", "t1"]
    classes = {t: "OBSERVED_COMPATIBLE" for t in tasks}
    build = {t: _built(tmp_path, t) for t in tasks}
    seen = []

    def run_batch(batch):
        seen.append(list(batch))
        return {
            "attempted": len(batch),
            "infrastructure_invalid": 0,
            "task_results": {
                t: {"outcome": "base_fail_gold_pass", "outcome_class": "scientific"}
                for t in batch
            },
        }

    result, _ = _run(
        tmp_path, pool=tasks, classes=classes, build=build,
        limits=_limits(max_executions=2, max_total_tasks=2, batch_limit=2),
        run_batch=run_batch, refresh=lambda: {"t0"},
    )
    assert seen == [["t1"]]


def test_scientific_outcome_not_recorded_halts(tmp_path):
    tasks = ["t0"]
    classes = {"t0": "OBSERVED_COMPATIBLE"}
    build = {"t0": _built(tmp_path, "t0")}

    def run_batch(batch):
        return {
            "attempted": 1,
            "infrastructure_invalid": 0,
            "task_results": {
                "t0": {"outcome": "base_fail_gold_pass", "outcome_class": "scientific"}
            },
        }

    result, _ = _run(
        tmp_path, pool=tasks, classes=classes, build=build,
        run_batch=run_batch, refresh=lambda: set(),
    )
    assert result.stop_reason == "LEDGER_DISAGREEMENT"


def test_runner_outcome_outside_batch_halts(tmp_path):
    tasks = ["t0"]
    classes = {"t0": "OBSERVED_COMPATIBLE"}
    build = {"t0": _built(tmp_path, "t0")}

    def run_batch(batch):
        return {
            "attempted": 1,
            "infrastructure_invalid": 0,
            "task_results": {
                "t1": {"outcome": "base_fail_gold_pass", "outcome_class": "scientific"}
            },
        }

    result, _ = _run(
        tmp_path, pool=tasks, classes=classes, build=build,
        run_batch=run_batch, refresh=lambda: set(),
    )
    assert result.stop_reason == "LEDGER_DISAGREEMENT"
