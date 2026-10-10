"""Tests for the reproducible F2 population funnel report.

The report exists so that a population number quoted anywhere can be re-derived
from the build artifacts instead of being believed, so the tests here pin the
*derivation* (including its fail-closed checks), not the current totals: the
totals change whenever the screen changes and must never be hard-coded.
"""
from __future__ import annotations

import json

import pytest

from qwen_train.f2_readiness import FROZEN_MIN_PAIRS
from qwen_train.f2_population_report import (
    NO_LEDGER_BASIS,
    RULE_S8,
    ReportError,
    diff_reports,
    funnel_counts,
    load_union_report,
    main,
)


def _row(iid, rules, source="swe-rebench-v2", cls="CLEAN"):
    return {
        "instance_id": iid,
        "source": source,
        "failing_rules": list(rules),
        "contamination_class": cls,
    }


def _record(*, raw=10, exclusions=1, accepted=9, rejected=None, prescreen=None):
    rejected = list(rejected if rejected is not None else [_row("a", [RULE_S8])])
    return {
        "total_raw_rows": raw,
        "exclusions": exclusions,
        "total_accepted_after_union": accepted,
        "pre_screen_exclusions": prescreen or [{"instance_id": "x", "reason": "dup"}],
        "screen_rejections": rejected,
    }


def _prov(row_count, distinct=None, key="swe-rebench-v2"):
    return {
        "source_key": key,
        "repo_id": "o/r",
        "revision": "0" * 40,
        "row_count": row_count,
        "distinct_instance_ids": distinct if distinct is not None else row_count,
        "duplicate_instance_ids": [],
        "normalised_sha256": "a" * 64,
    }


class TestFunnelCounts:
    def test_admitted_is_accepted_minus_reported_rejections(self):
        out = funnel_counts(
            _record(accepted=9, rejected=[_row("a", [RULE_S8]), _row("b", ["S4_pass_to_pass"])]),
            provenance=[_prov(10)],
        )
        stage = {s["name"]: s["count"] for s in out["stages"]}
        assert stage["admitted_tasks"] == 7

    def test_metadata_eligible_is_admitted_plus_only_s8_rows(self):
        out = funnel_counts(
            _record(
                accepted=9,
                rejected=[
                    _row("a", [RULE_S8]),
                    _row("b", [RULE_S8]),
                    _row("c", [RULE_S8, "S4_pass_to_pass"]),
                ],
            ),
            provenance=[_prov(10)],
        )
        stage = {s["name"]: s["count"] for s in out["stages"]}
        assert stage["admitted_tasks"] == 6
        assert stage["eligible_tasks"] == 6 + 2

    def test_evidence_verified_counts_rows_without_an_s8_failure(self):
        out = funnel_counts(
            _record(
                accepted=9,
                rejected=[
                    _row("a", [RULE_S8]),
                    _row("b", ["S10_contamination_provenance"]),
                ],
            ),
            provenance=[_prov(10)],
        )
        stage = {s["name"]: s["count"] for s in out["stages"]}
        assert stage["tasks_with_complete_evidence"] == 7 + 1

    def test_funnel_is_monotone_non_increasing_after_the_source_stage(self):
        out = funnel_counts(_record(), provenance=[_prov(10)])
        counts = [s["count"] for s in out["stages"]]
        assert counts[0] >= counts[1] >= counts[2] >= counts[3] >= counts[4] >= counts[5]
        assert counts[5] >= counts[6]

    def test_paired_stages_are_zero_and_record_why(self):
        out = funnel_counts(_record(), provenance=[_prov(10)])
        by_name = {s["name"]: s for s in out["stages"]}
        for name in (
            "authorized_paired_units",
            "started_units",
            "completed_units",
            "complete_valid_pairs",
            "analyzable_pairs",
        ):
            assert by_name[name]["count"] == 0
            assert by_name[name]["basis"] == NO_LEDGER_BASIS
        assert by_name["remaining_shortfall"]["count"] == FROZEN_MIN_PAIRS

    def test_per_rule_counts_come_from_the_rejected_rows(self):
        out = funnel_counts(
            _record(
                accepted=9,
                rejected=[
                    _row("a", [RULE_S8]),
                    _row("b", [RULE_S8, "S4_pass_to_pass"]),
                    _row("c", ["S4_pass_to_pass", "S10_contamination_provenance"]),
                ],
            ),
            provenance=[_prov(10)],
        )
        assert out["failing_rules"][RULE_S8] == 2
        assert out["failing_rules"]["S4_pass_to_pass"] == 2
        assert out["failing_rules"]["S10_contamination_provenance"] == 1

    def test_per_rule_counts_are_split_by_source(self):
        out = funnel_counts(
            _record(
                accepted=9,
                rejected=[
                    _row("a", [RULE_S8], source="swe-bench-live"),
                    _row("b", [RULE_S8, "S4_pass_to_pass"], source="swe-rebench-v2"),
                    _row("c", ["S4_pass_to_pass"], source="swe-rebench-v2"),
                ],
            ),
            provenance=[_prov(10)],
        )
        assert out["failing_rules_by_source"] == {
            "swe-bench-live": {RULE_S8: 1},
            "swe-rebench-v2": {RULE_S8: 1, "S4_pass_to_pass": 2},
        }


class TestFailClosed:
    def test_raw_minus_exclusions_must_equal_accepted(self):
        with pytest.raises(ReportError, match="exclusions"):
            funnel_counts(_record(raw=10, exclusions=1, accepted=8))

    def test_provenance_must_agree_with_the_report_row_count(self):
        with pytest.raises(ReportError, match="row counts"):
            funnel_counts(_record(), provenance=[_prov(11)])

    def test_more_rejections_than_accepted_rows_is_refused(self):
        with pytest.raises(ReportError, match="rejected rows"):
            funnel_counts(
                _record(accepted=1, rejected=[_row("a", [RULE_S8]), _row("b", [RULE_S8])]),
                provenance=[_prov(10)],
            )

    def test_a_row_that_is_neither_admitted_nor_rejected_is_refused(self):
        with pytest.raises(ReportError, match="neither admitted nor carry a failing rule"):
            funnel_counts(
                _record(rejected=[{"instance_id": "a", "source": "x", "failing_rules": []}]),
                provenance=[_prov(10)],
            )

    def test_missing_report_file_is_refused(self, tmp_path):
        with pytest.raises(ReportError, match="not found"):
            load_union_report(tmp_path / "nope.json")

    def test_report_without_the_required_keys_is_refused(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text("{}", encoding="utf-8")
        with pytest.raises(ReportError, match="missing"):
            load_union_report(p)


class TestDiff:
    def test_newly_eligible_ids_are_reported_by_name(self):
        before = _record(
            raw=5,
            exclusions=1,
            accepted=4,
            rejected=[
                _row("keep", [RULE_S8]),
                _row("recover", ["S6_relevant_file_set", "S7_source_not_test", RULE_S8]),
                _row("p2p", ["S4_pass_to_pass", RULE_S8]),
                _row("old", ["S10_contamination_provenance", RULE_S8]),
            ],
        )
        after = _record(
            raw=5,
            exclusions=1,
            accepted=4,
            rejected=[
                _row("keep", [RULE_S8]),
                _row("recover", [RULE_S8]),
                _row("p2p", ["S4_pass_to_pass", RULE_S8]),
                _row("old", ["S10_contamination_provenance", RULE_S8]),
            ],
        )
        d = diff_reports(before, after)
        assert d["newly_eligible"] == ["recover"]
        assert d["newly_eligible_count"] == 1
        assert d["newly_rejected"] == []
        assert d["metadata_eligible_before"] == 1
        assert d["metadata_eligible_after"] == 2

    def test_identities_present_in_only_one_report_are_not_read_as_rule_changes(self):
        before = _record(accepted=3, rejected=[_row("gone", [RULE_S8])])
        after = _record(accepted=3, rejected=[_row("added", [RULE_S8])])
        d = diff_reports(before, after)
        assert d["only_in_before"] == ["gone"]
        assert d["only_in_after"] == ["added"]
        assert d["newly_eligible_count"] == 0
        assert d["newly_rejected_count"] == 0

    def test_admitted_rows_are_carried_into_the_comparison(self):
        before = _record(accepted=5, rejected=[_row("a", [RULE_S8])] * 5)
        after = _record(accepted=5, rejected=[_row("a", [RULE_S8])])
        d = diff_reports(before, after)
        assert d["admitted_before"] == 0
        assert d["admitted_after"] == 4


class TestCli:
    def _write(self, tmp_path, record, provenance):
        report = tmp_path / "union_report.json"
        report.write_text(json.dumps(record), encoding="utf-8")
        prov_path = tmp_path / "PROVENANCE.json"
        prov_path.write_text(
            json.dumps(provenance[0] if isinstance(provenance, list) else provenance),
            encoding="utf-8",
        )
        return report, prov_path

    def test_json_output_is_machine_readable(self, tmp_path, capsys):
        report, prov = self._write(tmp_path, _record(), [_prov(10)])
        rc = main(["--report", str(report), "--provenance", str(prov), "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["schema"] == "f2_population_report_v1"
        assert len(out["stages"]) == 12
        assert out["checks"] == {
            "raw_equals_accepted_plus_exclusions": True,
            "provenance_row_count_matches_report": True,
        }

    def test_against_prints_the_delta(self, tmp_path, capsys):
        before = tmp_path / "before.json"
        before.write_text(
            json.dumps(
                _record(
                    accepted=9,
                    rejected=[
                        _row("recover", ["S6_relevant_file_set", "S7_source_not_test", RULE_S8])
                    ],
                )
            ),
            encoding="utf-8",
        )
        report, prov = self._write(
            tmp_path, _record(accepted=9, rejected=[_row("recover", [RULE_S8])]), [_prov(10)]
        )
        rc = main(["--report", str(report), "--provenance", str(prov), "--against", str(before)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "metadata-eligible 8 -> 9 (+1, -0)" in out

    def test_inconsistent_artifact_exits_non_zero(self, tmp_path, capsys):
        report, prov = self._write(tmp_path, _record(raw=10, exclusions=1, accepted=8), [_prov(10)])
        rc = main(["--report", str(report), "--provenance", str(prov), "--json"])
        assert rc == 2
        assert "ERROR" in capsys.readouterr().err
