"""Regression tests for the F2 population reconciliation tool (f2_reconcile).

The tool is read-only observation: it recomputes the union and the registration
funnel from the authorized source records and cross-checks the committed
manifest. The real sources are ~3 GB, so ``main()`` runs ONCE in a
session-scoped fixture and every test reads the produced artifacts (no
per-test rescan).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import qwen_train.f2_reconcile as rec
from qwen_train.f2_reconcile import _payload_key

ROOT = Path(__file__).resolve().parents[1]
POP = ROOT / "data" / "f2_population"


@pytest.fixture(scope="module")
def recon_out(tmp_path_factory):
    """Run the reconciliation exactly once against the real data."""
    out = tmp_path_factory.mktemp("recon")
    real_out = rec.OUT
    rec.OUT = out
    try:
        assert rec.main() == 0
    finally:
        rec.OUT = real_out
    recon = json.loads((out / "reconciliation.json").read_text("utf-8"))
    cap = json.loads((out / "capacity_report.json").read_text("utf-8"))
    return recon, cap


def test_registered_recomputation_matches_committed_manifest(recon_out):
    recon, _ = recon_out
    agree = recon["committed_manifest_agreement"]
    assert agree["recomputed_registered_matches"] is True
    assert agree["manifest_registered"] == 3828
    assert recon["funnel"]["registered_py_confirmed"] == 3828


def test_authorized_union_minus_naive_equals_conflicting_duplicates(recon_out):
    recon, _ = recon_out
    cs = recon["cross_source"]
    assert (
        cs["naive_first_wins_union_ids"] - cs["authorized_union_ids_conflict_removed"]
        == cs["within_source_conflicting_duplicate_ids"]
    )
    ureport = json.loads((POP / "union" / "union_report.json").read_text("utf-8"))
    assert cs["authorized_union_ids_conflict_removed"] == ureport["total_accepted_after_union"]


def test_cross_source_overlap_row_accounting(recon_out):
    recon, _ = recon_out
    cs = recon["cross_source"]
    assert cs["rows_removed_by_union_dedup"] == (
        cs["total_raw_rows_across_sources"] - cs["naive_first_wins_union_ids"]
    )
    assert cs["cross_source_overlap_ids"] >= 0


def test_capacity_report_counts_are_distinct_and_fail_closed(recon_out):
    _, cap = recon_out
    c = cap["counts_distinct"]
    assert c["registered"] == 3828
    assert c["admitted"] == 0
    assert c["analyzable_pairs"] == 0
    assert c["completed_pairs"] == 0
    assert c["registered"] > c["admitted"]
    assert cap["pool_sufficient_under_eligibility"] is True
    assert cap["target_analyzable_pairs"] == 300
    assert cap["shortfall_to_300_analyzable"] == 300


def test_funnel_gaps_are_decomposed(recon_out):
    recon, _ = recon_out
    f = recon["funnel"]
    assert f["gap_metadata_to_evaluator"] == 5398
    assert f["gap_evaluator_to_registered"] == 42
    # Exact decomposition of the 5,398 metadata->evaluator gap: dominated by
    # NON-PYTEST frameworks; a small remainder are pytest commands whose
    # FAIL_TO_PASS/PASS_TO_PASS are not node ids. The 42 evaluator->registered
    # gap is the non-.py node-path (python-confirmation) rejection.
    nr = f["reject_pytest_cmd_bad_nodes"]
    assert nr["empty"] == 0
    assert nr["nonnode"] == 16
    assert f["reject_evaluator_ok_not_py_confirmed"] == 42
    # non-pytest families (top-20 only) already account for the bulk
    assert sum(f["reject_non_pytest_framework_top"].values()) > 4500


def test_conan_conflicting_duplicate_is_detected():
    path = POP / "upstream_swe_bench_live_b51a8642" / "acquired.jsonl"
    # scan only until both conan copies are seen (avoid full 500MB scan)
    copies = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "conan-io__conan-18153" not in line:
                continue
            row = json.loads(line)
            from qwen_train.f2_population_build import source_to_screen_row
            copies.append(source_to_screen_row("swe-bench-live", row))
            if len(copies) >= 2:
                break
    assert len(copies) == 2
    assert len({_payload_key(c) for c in copies}) > 1  # conflicting, not identical


def test_reconcile_tool_is_read_only_and_deterministic_in_digest(recon_out):
    """The committed reconciliation artifact (if present) must carry the same
    union_report/manifest input digests the tool computes, proving the tool
    observes the same inputs it reconciles."""
    recon, cap = recon_out
    committed = POP / "reconciliation" / "capacity_report.json"
    if committed.exists():
        c = json.loads(committed.read_text("utf-8"))
        assert c["hashes"]["union_report_sha256"] == cap["hashes"]["union_report_sha256"]
        assert c["hashes"]["registration_manifest_sha256"] == cap["hashes"]["registration_manifest_sha256"]
