"""F2-S8 materialisation: both population sources, registration order."""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train.f2_s8_materialise import (  # noqa: E402
    MATERIALISE_SCHEMA,
    POPULATION_SOURCES,
    materialise_batch,
)


def test_both_population_sources_are_declared():
    """The campaign stalled because only swe-bench-live was ever read."""
    assert set(POPULATION_SOURCES) == {"swe-bench-live", "swe-rebench-v2"}


def _registration(tmp_path, ids):
    path = tmp_path / "registration.json"
    path.write_text(json.dumps([{"instance_id": i} for i in ids]), encoding="utf-8")
    return path


def test_selection_is_registration_order_and_records_skip_reasons(tmp_path):
    reg = _registration(tmp_path, ["a", "b", "c"])
    out = tmp_path / "out"
    manifest = materialise_batch(
        registration_path=reg, population_root=tmp_path / "empty",
        out_dir=out, attempted={"a"}, limit=10,
    )
    assert manifest["schema"] == MATERIALISE_SCHEMA
    assert manifest["selected"] == 0
    assert manifest["skip_reasons"]["already_attempted"] == 1
    assert manifest["skip_reasons"]["absent_from_local_snapshot"] == 2


def test_an_empty_population_root_yields_no_tasks(tmp_path):
    reg = _registration(tmp_path, ["a"])
    manifest = materialise_batch(
        registration_path=reg, population_root=tmp_path / "nope",
        out_dir=tmp_path / "out", limit=5,
    )
    assert manifest["selected"] == 0


def test_policy_exclusions_and_parking_are_separate_reasons(tmp_path):
    reg = _registration(tmp_path, ["x", "y"])
    manifest = materialise_batch(
        registration_path=reg, population_root=tmp_path / "empty",
        out_dir=tmp_path / "out", excluded={"x"}, parked={"y"}, limit=5,
    )
    assert manifest["skip_reasons"].get("policy_excluded") == 1
    assert manifest["skip_reasons"].get("policy_parked") == 1
