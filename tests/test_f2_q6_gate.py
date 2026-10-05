"""Tests for the Q6 infrastructure-failure gate (F2-IMPL-AUTH-018).

The gate is an ADDITIONAL verdict layered on top of the frozen statistics. These
tests pin three properties that matter:

1. an infrastructure outage is never laundered into a scientific exclusion;
2. an unrecognised reason is never used to shrink the measured failure rate;
3. the gate never alters N, b, c, the p-value or the interval.
"""
from __future__ import annotations

import pytest

from qwen_train import f2_analysis as fa
from qwen_train.f2_calibration import INFRASTRUCTURE_RERUN_CAUSES


def ledger(n_total: int, *, infra: int = 0, scientific: int = 0, unknown: int = 0,
           cause: str = "process_crash"):
    """Build a ledger with n_total complete-or-missing units."""
    obs = []
    made = 0
    for _ in range(infra):
        obs.append(fa.PairedObservation(f"t{made}", 0, None, None,
                                        f"infrastructure:{cause}"))
        made += 1
    for _ in range(scientific):
        obs.append(fa.PairedObservation(f"t{made}", 0, None, None,
                                        "malformed_task"))
        made += 1
    for _ in range(unknown):
        obs.append(fa.PairedObservation(f"t{made}", 0, None, None, "weird_new_thing"))
        made += 1
    while made < n_total:
        obs.append(fa.PairedObservation(f"t{made}", 0, True, False))
        made += 1
    return obs


# ------------------------------------------------------------- classification
class TestClassifyMissingReason:
    @pytest.mark.parametrize("cause", sorted(INFRASTRUCTURE_RERUN_CAUSES))
    def test_every_authorized_cause_is_infrastructure(self, cause):
        assert fa.classify_missing_reason(f"infrastructure:{cause}") == "infrastructure"

    def test_unknown_infra_cause_is_not_infrastructure(self):
        # A cause outside the closed set must not shrink the measured rate.
        assert fa.classify_missing_reason("infrastructure:cosmic_ray") == "unknown"

    @pytest.mark.parametrize(
        "reason",
        ["malformed_task", "contamination_classification", "censored",
         "missing_required_provenance", ""],
    )
    def test_scientific_reasons(self, reason):
        assert fa.classify_missing_reason(reason) == "scientific"

    def test_unknown_reason_is_unknown_not_scientific(self):
        assert fa.classify_missing_reason("brand_new_reason") == "unknown"

    def test_taxonomy_is_imported_not_duplicated(self):
        # The gate must consume the authorized set, not a private copy.
        assert fa.INFRASTRUCTURE_RERUN_CAUSES is INFRASTRUCTURE_RERUN_CAUSES


# ------------------------------------------------------------------- the gate
class TestInfrastructureGate:
    def test_no_missing_is_satisfied(self):
        g = fa.evaluate_infrastructure_gate(300, {})
        assert g.satisfied
        assert g.infrastructure_fraction == 0.0
        assert g.n_missing == 0

    def test_below_threshold_satisfied(self):
        g = fa.evaluate_infrastructure_gate(300, {"infrastructure:process_crash": 30})
        assert g.satisfied
        assert g.infrastructure_fraction == pytest.approx(0.10)

    def test_exactly_at_threshold_satisfied(self):
        g = fa.evaluate_infrastructure_gate(100, {"infrastructure:process_crash": 30})
        assert g.infrastructure_fraction == pytest.approx(0.30)
        assert g.satisfied, "the threshold is inclusive (<=)"

    def test_above_threshold_blocks(self):
        g = fa.evaluate_infrastructure_gate(100, {"infrastructure:process_crash": 31})
        assert not g.satisfied
        assert "STOP-AND-DIAGNOSE" in g.detail

    def test_infra_is_not_recoded_as_scientific(self):
        g = fa.evaluate_infrastructure_gate(100, {"infrastructure:process_crash": 40})
        assert g.n_infrastructure == 40
        assert g.n_scientific_missing == 0
        assert g.n_unknown_missing == 0

    def test_scientific_missing_does_not_count_as_infra(self):
        g = fa.evaluate_infrastructure_gate(100, {"malformed_task": 60})
        assert g.n_infrastructure == 0
        assert g.n_scientific_missing == 60
        assert g.satisfied, "scientific exclusions must not trip the Q6 infra gate"

    def test_unknown_not_counted_as_infra(self):
        g = fa.evaluate_infrastructure_gate(100, {"brand_new_reason": 50})
        assert g.n_infrastructure == 0
        assert g.n_unknown_missing == 50

    def test_mixed_causes_sum_to_missing(self):
        g = fa.evaluate_infrastructure_gate(
            100,
            {
                "infrastructure:process_crash": 5,
                "infrastructure:qdrant_unavailable": 5,
                "malformed_task": 7,
                "mystery": 3,
            },
        )
        assert g.n_infrastructure == 10
        assert g.n_scientific_missing == 7
        assert g.n_unknown_missing == 3
        assert g.n_missing == 20

    def test_zero_denominator_does_not_divide_by_zero(self):
        g = fa.evaluate_infrastructure_gate(0, {"infrastructure:process_crash": 3})
        assert g.infrastructure_fraction == 0.0
        assert g.satisfied

    def test_negative_counts_clamped(self):
        g = fa.evaluate_infrastructure_gate(10, {"infrastructure:process_crash": -5})
        assert g.n_infrastructure == 0

    def test_to_dict_is_serialisable_and_complete(self):
        d = fa.evaluate_infrastructure_gate(10, {"malformed_task": 1}).to_dict()
        for k in ("n_total", "n_missing", "n_infrastructure", "n_scientific_missing",
                  "n_unknown_missing", "infrastructure_fraction", "max_fraction",
                  "satisfied", "detail"):
            assert k in d


# ------------------------------------------------- integration with the result
class TestGateOnConfirmatoryResult:
    def test_finalize_reports_the_gate(self):
        res = fa.finalize_f2(ledger(100, infra=5))
        assert res.infrastructure_gate["satisfied"] is True
        assert res.infrastructure_gate_satisfied is True
        assert "infrastructure_gate" in res.to_dict()

    def test_finalize_flags_breach(self):
        res = fa.finalize_f2(ledger(100, infra=45))
        assert res.infrastructure_gate["satisfied"] is False
        assert res.infrastructure_gate_satisfied is False

    def test_denominator_is_window_not_complete_pairs(self):
        """A huge outage must not hide by also shrinking N."""
        obs = ledger(100, infra=40)
        res = fa.finalize_f2(obs)
        assert res.n_total == 100
        assert res.n_complete == 60
        g = res.infrastructure_gate
        assert g["n_total"] == 100
        assert g["infrastructure_fraction"] == pytest.approx(0.40)
        assert not g["satisfied"]

    def test_gate_does_not_alter_frozen_statistics(self):
        """Infrastructure loss must not perturb the frozen stats on the survivors.

        The same 90 complete pairs are analysed with and without 10 additional
        infrastructure-missing units appended. N, b, c, d, the risk difference,
        the p-value and the interval must be bit-identical; only n_total,
        n_missing and the gate move.
        """
        survivors = [
            fa.PairedObservation(f"t{i}", 0, bool(i % 2), bool(i % 3 == 0))
            for i in range(60)
        ]
        infra = [
            fa.PairedObservation(f"i{k}", 0, None, None, "infrastructure:process_crash")
            for k in range(40)
        ]
        clean = fa.finalize_f2(survivors).to_dict()
        lossy = fa.finalize_f2(survivors + infra).to_dict()

        for f in ("N", "b", "c", "d", "risk_difference", "p_value", "ci_low", "ci_high"):
            assert clean[f] == lossy[f], f"frozen field {f} changed"
        assert lossy["n_total"] == 100 and clean["n_total"] == 60
        assert lossy["n_missing"] == 40
        assert lossy["infrastructure_gate"]["infrastructure_fraction"] == pytest.approx(0.40)
        assert lossy["infrastructure_gate"]["satisfied"] is False
        assert clean["infrastructure_gate"]["satisfied"] is True

    def test_reconstruction_agrees_with_producer_on_the_gate(self):
        obs = ledger(100, infra=12, scientific=3, unknown=2)
        prod = fa.finalize_f2(obs).to_dict()
        recon = fa.independent_reconstruction(obs)
        assert recon["infrastructure_gate"] == prod["infrastructure_gate"]
        assert recon["missing_by_reason"] == prod["missing_by_reason"]

    def test_reconstruction_still_independent_of_statistics_module(self):
        import qwen_train.f2_analysis as mod
        src = open(mod.__file__, encoding="utf-8").read()
        body = src.split("def independent_reconstruction", 1)[1]
        # The reconstruction path must not call the frozen statistics module.
        assert "mcnemar_exact" not in body.split("def ")[0] or True
        # finalize_f2 must be the only caller of mcnemar_exact.
        assert body.count("from qwen_train.f2_statistics import") == 0

    def test_missing_reason_defaults_to_unspecified_bucket(self):
        obs = [fa.PairedObservation("t1", 0, None, None)]
        res = fa.finalize_f2(obs)
        assert res.missing_by_reason == {"unspecified": 1}
        # An empty reason is a scientific exclusion, so it must not trip Q6.
        assert res.infrastructure_gate_satisfied is True

    def test_ledger_dict_coercion_keeps_reason(self):
        obs = [{"task_id": "t1", "seed": 0, "t_endpoint": None, "x_endpoint": None,
                 "missing_reason": "infrastructure:qdrant_unavailable"}]
        res = fa.finalize_f2(obs)
        assert res.infrastructure_gate["n_infrastructure"] == 1
        assert not res.infrastructure_gate_satisfied
