"""Unit tests for deterministic feasibility sampling and Wilson score interval calculations."""

import pytest

from qwen_train.f2_s8_sampling import (
    SAMPLING_SCHEMA,
    deterministic_sample_tasks,
    filter_unknown_population,
    sample_manifest,
    sample_manifest_digest,
    wilson_score_interval,
)


def test_wilson_score_interval_bounds():
    # 0/50: point estimate is 0.0, lower bound is 0.0, upper bound ~0.0713
    lower, point, upper = wilson_score_interval(0, 50, confidence=0.95)
    assert lower == 0.0
    assert point == 0.0
    assert 0.06 < upper < 0.08

    # 50/50: point estimate is 1.0, lower bound ~0.9287, upper bound is 1.0
    lower, point, upper = wilson_score_interval(50, 50, confidence=0.95)
    assert 0.92 < lower < 0.94
    assert point == 1.0
    assert upper == 1.0

    # 25/50: point estimate is 0.50, symmetric around 0.50
    lower, point, upper = wilson_score_interval(25, 50, confidence=0.95)
    assert 0.36 < lower < 0.38
    assert point == 0.50
    assert 0.62 < upper < 0.64
    assert abs((point - lower) - (upper - point)) < 1e-6


def test_wilson_score_interval_confidence_levels():
    # As confidence increases, the interval widens
    l90, _, u90 = wilson_score_interval(25, 50, confidence=0.90)
    l95, _, u95 = wilson_score_interval(25, 50, confidence=0.95)
    l99, _, u99 = wilson_score_interval(25, 50, confidence=0.99)

    assert l99 < l95 < l90
    assert u90 < u95 < u99


def test_wilson_score_interval_invalid_inputs():
    with pytest.raises(ValueError, match="total must be a positive integer"):
        wilson_score_interval(0, 0)

    with pytest.raises(ValueError, match="total must be a positive integer"):
        wilson_score_interval(0, -10)

    with pytest.raises(ValueError, match="successes must be an integer between 0 and total"):
        wilson_score_interval(-1, 50)

    with pytest.raises(ValueError, match="successes must be an integer between 0 and total"):
        wilson_score_interval(51, 50)

    with pytest.raises(ValueError, match="confidence must be between 0.0 and 1.0"):
        wilson_score_interval(10, 50, confidence=0.0)

    with pytest.raises(ValueError, match="confidence must be between 0.0 and 1.0"):
        wilson_score_interval(10, 50, confidence=1.0)


def test_deterministic_sample_tasks_repeatability():
    tasks = [f"task__{i:04d}" for i in range(200)]
    s1 = deterministic_sample_tasks(tasks, sample_size=50, seed="my_fixed_seed_2026")
    s2 = deterministic_sample_tasks(tasks, sample_size=50, seed="my_fixed_seed_2026")

    assert len(s1) == 50
    assert s1 == s2
    assert len(set(s1)) == 50
    assert s1 == sorted(s1)


def test_deterministic_sample_tasks_order_invariance():
    tasks = [f"task__{i:04d}" for i in range(200)]
    reversed_tasks = list(reversed(tasks))
    import random
    shuffled = list(tasks)
    random.Random(42).shuffle(shuffled)

    s_orig = deterministic_sample_tasks(tasks, sample_size=50, seed=12345)
    s_rev = deterministic_sample_tasks(reversed_tasks, sample_size=50, seed=12345)
    s_shuf = deterministic_sample_tasks(shuffled, sample_size=50, seed=12345)

    assert s_orig == s_rev == s_shuf


def test_deterministic_sample_tasks_distinct_seeds_differ():
    tasks = [f"task__{i:04d}" for i in range(200)]
    s1 = deterministic_sample_tasks(tasks, sample_size=50, seed=1)
    s2 = deterministic_sample_tasks(tasks, sample_size=50, seed=2)

    assert s1 != s2


def test_deterministic_sample_tasks_smaller_population():
    tasks = [f"task__{i:02d}" for i in range(20)]
    sampled = deterministic_sample_tasks(tasks, sample_size=50, seed=1)
    assert sampled == sorted(tasks)


def test_sample_manifest_and_digest():
    tasks = [f"task__{i:02d}" for i in range(10)]
    manifest = sample_manifest(tasks, seed="s8_test_seed", population_sha256="abc" * 21 + "a", sample_size=10)
    assert manifest["schema"] == SAMPLING_SCHEMA
    assert manifest["seed"] == "s8_test_seed"
    assert manifest["task_count"] == 10
    assert manifest["task_ids"] == sorted(tasks)

    digest1 = sample_manifest_digest(manifest)
    digest2 = sample_manifest_digest(manifest)
    assert digest1 == digest2
    assert len(digest1) == 64


def test_filter_unknown_population():
    reg = ["t1", "t2", "t3", "t4", "t5", "t6"]
    compat = {
        "t1": "UNKNOWN",
        "t2": "OBSERVED_COMPATIBLE",
        "t3": "UNKNOWN",
        "t4": "OBSERVED_INCOMPATIBLE",
        "t5": "UNKNOWN",
        "t6": "UNKNOWN",
    }
    exclusions = ["t3"]
    terminals = ["t5"]

    filtered = filter_unknown_population(
        reg, compat, exclusion_ids=exclusions, terminal_ids=terminals
    )
    # t1: eligible (UNKNOWN, not excluded, not terminal)
    # t2: excluded (not UNKNOWN)
    # t3: excluded (in exclusions)
    # t4: excluded (not UNKNOWN)
    # t5: excluded (in terminals)
    # t6: eligible (UNKNOWN, not excluded, not terminal)
    assert filtered == ["t1", "t6"]
