"""Case-insensitive registration collisions must fail closed (DEFECT D).

The materialised layout is keyed by instance id — ``inputs/<id>.json``,
``patches/<id>.gold_patch``, ``patches/<id>.test_patch`` and the workspace
``C:\\f2_pilot\\<id>``.  ``F2_EVIDENCE`` is on a case-insensitive volume
(``fsutil file queryCaseSensitiveInfo`` -> "Case sensitive attribute ... is
disabled"), so two ids that differ only in case address the SAME paths and the
second write would silently destroy the first task's input.

These tests pin the fail-closed contract.  They deliberately do NOT assert which
variant survives: choosing that is a registration policy decision and the code
must never make it.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train.f2_s8_materialise import (  # noqa: E402
    MaterialiseError,
    case_key,
    find_case_collisions,
    materialise_batch,
)

#: A real colliding pair from the registration, positions 244/245.
PAIR = ("Kozea__WeasyPrint-2494", "kozea__weasyprint-2494")


def _registration(tmp_path: pathlib.Path, ids):
    path = tmp_path / "registration.json"
    path.write_text(json.dumps([{"instance_id": i} for i in ids]), encoding="utf-8")
    return path


def _fake_population(ids):
    return {
        i: {
            "source": "swe-bench-live",
            "row": {
                "instance_id": i,
                "patch": "diff --git a b\n",
                "test_patch": "diff --git c d\n",
                "base_commit": "0" * 40,
                "repo": "org/name",
            },
        }
        for i in ids
    }


@pytest.fixture
def population(monkeypatch):
    """Install a fake population snapshot so no parquet or dataset is touched."""

    def install(ids):
        monkeypatch.setattr(
            "qwen_train.f2_s8_materialise.load_population_rows",
            lambda root: _fake_population(ids),
        )
        monkeypatch.setattr(
            "qwen_train.f2_s8_materialise.source_to_screen_row",
            lambda source, row: {
                "test_cmd": "pytest tests/test_thing.py",
                "fail_to_pass": ["tests/test_thing.py::t"],
                "pass_to_pass": [],
            },
        )
        monkeypatch.setattr(
            "qwen_train.f2_s8_materialise.eligible",
            lambda cmd, f2p, p2p: True,
        )

    return install


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_case_key_folds_case() -> None:
    assert case_key("Kozea__WeasyPrint-2494") == case_key("kozea__weasyprint-2494")
    assert case_key("Pyomo__pyomo-3588") != case_key("Pyomo__pyomo-3589")


def test_find_case_collisions_reports_only_multi_member_groups() -> None:
    found = find_case_collisions(["a__b-1", "c__d-2", "A__B-1"])
    assert found == {"a__b-1": ["a__b-1", "A__B-1"]}
    assert find_case_collisions(["a__b-1", "c__d-2"]) == {}


def test_find_case_collisions_is_diagnostic_only() -> None:
    """It reports the group verbatim; it never returns a single survivor."""
    found = find_case_collisions(PAIR)
    assert list(found.values()) == [list(PAIR)]


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_collision_inside_one_batch_refuses_and_keeps_the_first_input(
    tmp_path: pathlib.Path, population
) -> None:
    population(PAIR)
    out = tmp_path / "out"

    with pytest.raises(MaterialiseError) as excinfo:
        materialise_batch(
            registration_path=_registration(tmp_path, PAIR),
            population_root=tmp_path / "pop",
            out_dir=out,
            limit=10,
        )

    message = str(excinfo.value)
    assert PAIR[0] in message and PAIR[1] in message
    assert "case-insensitive collision" in message
    # The first input was written; the second was refused BEFORE it could
    # overwrite it. Only one input file can exist for this colliding key.
    inputs = sorted((out / "inputs").glob("*.json"))
    assert len(inputs) == 1, f"expected the first input to survive, got {inputs}"
    assert inputs[0].stem == PAIR[0]
    # No frozen input manifest may be emitted for a colliding batch: the task
    # list must not become authoritative while the collision is unresolved.
    assert not (out / "inputs_manifest.json").exists(), (
        "a colliding batch must not emit a frozen input manifest"
    )


def test_collision_with_an_attempted_task_refuses_before_any_write(
    tmp_path: pathlib.Path, population
) -> None:
    population([PAIR[0]])
    out = tmp_path / "out"

    with pytest.raises(MaterialiseError, match="already-attempted"):
        materialise_batch(
            registration_path=_registration(tmp_path, [PAIR[0]]),
            population_root=tmp_path / "pop",
            out_dir=out,
            attempted={PAIR[1]},
            limit=10,
        )

    assert list((out / "inputs").glob("*.json")) == []


def test_attempted_collision_is_checked_before_selection(
    tmp_path: pathlib.Path, population
) -> None:
    """Even a task that would fail the evaluator filter cannot slip past."""
    population([PAIR[0]])
    out = tmp_path / "out"

    with pytest.raises(MaterialiseError):
        materialise_batch(
            registration_path=_registration(tmp_path, [PAIR[0]]),
            population_root=tmp_path / "pop",
            out_dir=out,
            attempted={PAIR[1]},
            limit=10,
        )
    assert not (out / "inputs").exists() or list((out / "inputs").glob("*.json")) == []


# ---------------------------------------------------------------------------
# Regression: distinct ids still materialise
# ---------------------------------------------------------------------------


def test_distinct_ids_are_unaffected(tmp_path: pathlib.Path, population) -> None:
    ids = ["acme__widget-1", "acme__gadget-2"]
    population(ids)
    out = tmp_path / "out"

    manifest = materialise_batch(
        registration_path=_registration(tmp_path, ids),
        population_root=tmp_path / "pop",
        out_dir=out,
        limit=10,
    )

    assert manifest["selected"] == 2
    assert sorted(p.stem for p in (out / "inputs").glob("*.json")) == sorted(ids)
    assert {s["task_id"] for s in manifest["tasks"]} == set(ids)


def test_exact_duplicate_is_also_treated_as_a_collision(
    tmp_path: pathlib.Path, population
) -> None:
    """An identical id twice is the same physical overwrite, so refuse it too."""
    ids = ["acme__widget-1", "acme__widget-1"]
    population(["acme__widget-1"])

    with pytest.raises(MaterialiseError):
        materialise_batch(
            registration_path=_registration(tmp_path, ids),
            population_root=tmp_path / "pop",
            out_dir=tmp_path / "out",
            limit=10,
        )
