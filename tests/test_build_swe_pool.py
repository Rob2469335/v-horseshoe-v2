"""Tests for build_swe_pool.py — pure logic only (no network, no subprocess).

Covers:
  - select_candidates: language filter + sort
  - classify_skip_reason: interpreter mapping gate
  - assign_splits: whole-repo holdout (never split inside a repo)
  - build_summary: counts + reason/repo breakdown
  - probe_capture reason codes: REASON_* constants exist and are non-empty strings

These are revert-proof: each test will fail if the function it covers is removed
or its key invariant is broken.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "qwen_train" / "build_swe_pool.py"
_spec = importlib.util.spec_from_file_location("build_swe_pool", _MOD)
bsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsp)


# ---------------------------------------------------------------------------
# select_candidates
# ---------------------------------------------------------------------------


def _row(instance_id: str, language: str, lines: int = 10) -> dict:
    return {
        "instance_id": instance_id,
        "repo": f"org/{instance_id}",
        "language": language,
        "meta": {"num_modified_lines": lines},
        "install_config": {"base_image_name": "python_base_311"},
    }


def test_select_candidates_filters_language():
    rows = [_row("py-1", "python"), _row("rb-1", "ruby"), _row("py-2", "python")]
    result = bsp.select_candidates(rows, language="python")
    assert len(result) == 2
    assert all(r["language"] == "python" for r in result)


def test_select_candidates_sorts_by_lines_ascending():
    rows = [_row("big", "python", lines=100), _row("small", "python", lines=5)]
    result = bsp.select_candidates(rows, language="python", prefer_small=True)
    assert result[0]["instance_id"] == "small"
    assert result[1]["instance_id"] == "big"


def test_select_candidates_no_sort_preserves_order():
    rows = [_row("big", "python", lines=100), _row("small", "python", lines=5)]
    result = bsp.select_candidates(rows, language="python", prefer_small=False)
    assert result[0]["instance_id"] == "big"


def test_select_candidates_empty_rows():
    assert bsp.select_candidates([], language="python") == []


# ---------------------------------------------------------------------------
# classify_skip_reason
# ---------------------------------------------------------------------------


def _row_with_image(image: str) -> dict:
    return {
        "instance_id": "test-1",
        "language": "python",
        "install_config": {"base_image_name": image},
    }


def test_classify_skip_reason_known_image_returns_none():
    row = _row_with_image("python_base_311")
    assert bsp.classify_skip_reason(row) is None


def test_classify_skip_reason_unknown_image_returns_interpreter_missing():
    row = _row_with_image("weird-docker-image")
    assert bsp.classify_skip_reason(row) == bsp.REASON_INTERPRETER_MISSING


def test_classify_skip_reason_missing_image_returns_interpreter_missing():
    row = {"instance_id": "x", "language": "python", "install_config": {}}
    assert bsp.classify_skip_reason(row) == bsp.REASON_INTERPRETER_MISSING


def test_classify_skip_reason_310_312_313_pass():
    for ver in ("310", "312", "313"):
        row = _row_with_image(f"python_base_{ver}")
        assert bsp.classify_skip_reason(row) is None, f"python_base_{ver} should not be skipped"


# ---------------------------------------------------------------------------
# assign_splits — whole-repo holdout invariant
# ---------------------------------------------------------------------------


def _usable(instance_id: str, repo: str) -> dict:
    return {"instance_id": instance_id, "repo": repo, "usable": True}


def test_assign_splits_explicit_eval_repos():
    records = [
        _usable("a-1", "org/alpha"),
        _usable("a-2", "org/alpha"),
        _usable("b-1", "org/beta"),
    ]
    result = bsp.assign_splits(records, eval_repos=["org/alpha"])
    alpha = [r for r in result if r["repo"] == "org/alpha"]
    beta = [r for r in result if r["repo"] == "org/beta"]
    assert all(r["split"] == "eval" for r in alpha), "alpha should be eval"
    assert all(r["split"] == "train" for r in beta), "beta should be train"


def test_assign_splits_never_splits_inside_repo():
    # All records for the same repo must get the same split
    records = [_usable(f"r-{i}", "org/shared") for i in range(5)]
    result = bsp.assign_splits(records, eval_repos=["org/shared"])
    splits = {r["split"] for r in result}
    assert len(splits) == 1, "all records from one repo must share the same split"


def test_assign_splits_default_holds_out_largest_repos():
    # 5 repos, one dominant — it should be held out by default
    records = (
        [_usable(f"big-{i}", "org/bigone") for i in range(10)]
        + [_usable(f"sm-{i}", f"org/small{i}") for i in range(3)]
    )
    result = bsp.assign_splits(records, eval_repos=None)
    # org/bigone has 10 tasks → should be in eval
    bigone = [r for r in result if r["repo"] == "org/bigone"]
    assert all(r["split"] == "eval" for r in bigone), "largest repo should be eval"


def test_assign_splits_empty_returns_empty():
    assert bsp.assign_splits([], eval_repos=None) == []


# ---------------------------------------------------------------------------
# build_summary
# ---------------------------------------------------------------------------


def _result(usable: bool, repo: str = "org/x", reason: str | None = None) -> dict:
    return {
        "instance_id": "x",
        "repo": repo,
        "usable": usable,
        "reason": reason,
    }


def test_build_summary_counts_correctly():
    results = [
        _result(True, "org/a"),
        _result(True, "org/b"),
        _result(False, reason="interpreter_missing"),
        _result(False, reason="install_failed"),
    ]
    summary = bsp.build_summary(4, results)
    assert summary["probed"] == 4
    assert summary["usable"] == 2
    assert summary["rejected"] == 2


def test_build_summary_by_reason():
    results = [
        _result(False, reason="interpreter_missing"),
        _result(False, reason="interpreter_missing"),
        _result(False, reason="install_failed"),
    ]
    summary = bsp.build_summary(3, results)
    assert summary["by_reason"]["interpreter_missing"] == 2
    assert summary["by_reason"]["install_failed"] == 1


def test_build_summary_by_repo():
    results = [
        _result(True, repo="org/alpha"),
        _result(True, repo="org/alpha"),
        _result(True, repo="org/beta"),
    ]
    summary = bsp.build_summary(3, results)
    assert summary["by_repo"]["org/alpha"] == 2
    assert summary["by_repo"]["org/beta"] == 1


def test_build_summary_empty():
    summary = bsp.build_summary(0, [])
    assert summary["probed"] == 0
    assert summary["usable"] == 0
    assert summary["rejected"] == 0


# ---------------------------------------------------------------------------
# Reason constants: must exist and be non-empty strings (revert-proof)
# ---------------------------------------------------------------------------


def test_reason_constants_are_strings():
    for name in (
        "REASON_INTERPRETER_MISSING",
        "REASON_INSTALL_FAILED",
        "REASON_PATCH_FAILED",
        "REASON_F2P_DID_NOT_FAIL",
        "REASON_ENV_ERROR",
        "REASON_GOLD_DID_NOT_PASS",
        "REASON_ERROR",
        "REASON_NOT_PYTHON",
    ):
        val = getattr(bsp, name, None)
        assert isinstance(val, str) and val, f"{name} must be a non-empty string"


# ---------------------------------------------------------------------------
# atomic_write_jsonl integration (write + read-back roundtrip, no subprocess)
# ---------------------------------------------------------------------------


def test_atomic_write_jsonl_roundtrip(tmp_path):
    # atomic_write_jsonl(path, rows) expects dicts, not pre-serialized strings
    out = tmp_path / "test_pool.jsonl"
    rows = [{"id": i} for i in range(3)]
    bsp.atomic_write_jsonl(out, rows)
    written = out.read_text(encoding="utf-8").splitlines()
    assert len(written) == 3
    assert json.loads(written[0])["id"] == 0
    assert json.loads(written[2])["id"] == 2


def test_atomic_write_json_roundtrip(tmp_path):
    out = tmp_path / "summary.json"
    data = {"probed": 5, "usable": 3}
    bsp.atomic_write_json(out, data)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["probed"] == 5
    assert loaded["usable"] == 3
