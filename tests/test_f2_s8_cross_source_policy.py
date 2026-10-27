"""F-9: cross-source duplicate priority for the three case-only pairs.

Each pair is the SAME upstream task registered twice, once per population
source. The members share ``repo``, ``pull_number``, ``base_commit`` and
``created_at`` and differ only in the letter case of ``instance_id``, so on a
case-insensitive volume they address identical input, patch and workspace paths.

Owner authorization (2026-10-19) applies the established
``conflicting_cross_source_priority_selected`` rule: ``swe-bench-live`` (the
first entry in ``POPULATION_SOURCES``) stays selectable and the
``swe-rebench-v2`` copy is excluded.

These tests pin that disposition and the union semantics that stop a policy
file from silently re-admitting an excluded duplicate.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train.f2_s8_materialise import find_case_collisions  # noqa: E402
from qwen_train.f2_s8_precheck import (  # noqa: E402
    DEFAULT_POLICY,
    PrecheckError,
    _policy,
    select_candidates,
)

#: (live spelling that must stay selectable, rebench copy that must be excluded)
PAIRS = [
    ("Kozea__WeasyPrint-2494", "kozea__weasyprint-2494"),
    ("QuantEcon__QuantEcon.py-769", "quantecon__quantecon.py-769"),
    ("Pyomo__pyomo-3588", "pyomo__pyomo-3588"),
]


@pytest.mark.parametrize("live,rebench", PAIRS)
def test_rebench_variant_is_excluded(live: str, rebench: str) -> None:
    excluded = DEFAULT_POLICY["excluded_tasks"]
    assert rebench in excluded
    assert live not in excluded


@pytest.mark.parametrize("live,rebench", PAIRS)
def test_live_variant_is_not_parked(live: str, rebench: str) -> None:
    parked = DEFAULT_POLICY["parked_tasks"]
    assert live not in parked
    assert rebench not in parked


@pytest.mark.parametrize("live,rebench", PAIRS)
def test_exactly_one_member_of_each_pair_is_filtered(
    live: str, rebench: str
) -> None:
    """Neither both nor neither: the task survives under exactly one spelling."""
    resolved = _policy(None)
    filtered = set(resolved["excluded_tasks"]) | set(resolved["parked_tasks"])
    assert (live in filtered) + (rebench in filtered) == 1


def test_case_keys_of_the_pairs_are_identical() -> None:
    for live, rebench in PAIRS:
        assert live.lower() == rebench.lower()
        assert live != rebench


def test_excluded_list_has_no_case_only_collision_of_its_own() -> None:
    """The exclusion list must not itself be ambiguous."""
    assert find_case_collisions(DEFAULT_POLICY["excluded_tasks"]) == {}


def test_pre_existing_exclusion_is_not_removed() -> None:
    """F-9 is additive: the earlier pinned exclusion must survive."""
    assert "networkx__networkx-8207" in DEFAULT_POLICY["excluded_tasks"]


def test_policy_file_cannot_remove_a_pinned_exclusion() -> None:
    """Union semantics: a caller may add, never subtract, a code-pinned id."""
    rebench = PAIRS[0][1]
    resolved = _policy({"excluded_tasks": [], "parked_tasks": []})
    assert rebench in resolved["excluded_tasks"]

    with pytest.raises(PrecheckError):
        # max_attempts stays frozen regardless of the caller
        _policy({"max_attempts": 9})


@pytest.mark.parametrize("live,rebench", PAIRS)
def test_selection_keeps_only_the_live_spelling(live: str, rebench: str) -> None:
    """Only BUILT, unrecorded tasks are eligible -- and the duplicate is refused."""
    resolved = _policy(None)
    selected, excluded = select_candidates(
        registered=[live, rebench],
        build_manifest={live: {"status": "built"}, rebench: {"status": "built"}},
        recorded=set(),
        terminal=set(),
        attempts={},
        policy=resolved,
        requested=None,
    )
    assert selected == [live]
    assert rebench in {str(x["task_id"]) for x in excluded}
    assert {x["task_id"] for x in excluded} == {rebench}
