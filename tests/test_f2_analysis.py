"""F2 confirmatory analysis: ledger -> final result, and independent audit.

Proves the final link of the F2 chain (F2-IMPL-AUTH-019):
* ``finalize_f2`` produces the frozen result from the paired ledger;
* ``independent_reconstruction`` re-derives the same numbers WITHOUT calling
  ``f2_statistics`` (a genuine second implementation, not a wrapper);
* missingness is reported, never converted into success/failure.

No network, no services, no real evidence.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_analysis import (
    PairedObservation,
    finalize_f2,
    independent_reconstruction,
)


def _pairs(b, c, both, neither):
    out = []
    out += [(True, False)] * b
    out += [(False, True)] * c
    out += [(True, True)] * both
    out += [(False, False)] * neither
    return out


FIXTURES = [
    (3, 1, 2, 4),
    (6, 14, 0, 0),
    (10, 10, 0, 0),
    (5, 0, 5, 0),
    (0, 3, 7, 0),
    (0, 0, 8, 8),
    (4, 0, 6, 0),
]


class TestFinalize:
    def test_contingency_conventions(self):
        r = finalize_f2(_pairs(3, 1, 2, 4))
        assert (r.n10, r.n01, r.n11, r.n00) == (3, 1, 2, 4)
        assert (r.b, r.c, r.d) == (3, 1, 4)
        assert r.n_complete == 10
        assert r.n_total == 10 and r.n_missing == 0
        assert r.risk_difference == pytest.approx(0.2)
        assert r.direction == "favours_treatment"
        assert r.sid == "two-sided"

    def test_matches_the_authoritative_statistics(self):
        from qwen_train.f2_statistics import mcnemar_exact

        for b, c, both, neither in FIXTURES:
            pairs = _pairs(b, c, both, neither)
            r = finalize_f2(pairs)
            ref = mcnemar_exact([t for t, _ in pairs], [x for _, x in pairs])
            assert r.p_value == pytest.approx(ref.p_value)
            assert r.ci_low == pytest.approx(ref.ci_low)
            assert r.ci_high == pytest.approx(ref.ci_high)
            assert r.risk_difference == pytest.approx(ref.risk_difference)
            assert (r.b, r.c, r.d) == (ref.b, ref.c, ref.b + ref.c)

    def test_r_transformed_oracle(self):
        # b=6, c=14, N=20 -> R binom.test(6,20)$conf.int (0.1189, 0.5428) -> RD
        r = finalize_f2(_pairs(6, 14, 0, 0))
        assert r.ci_low == pytest.approx(2 * 0.1189 - 1.0, abs=1e-3)
        assert r.ci_high == pytest.approx(2 * 0.5428 - 1.0, abs=1e-3)

    def test_to_dict_reports_the_required_fields(self):
        d = finalize_f2(_pairs(2, 1, 1, 4)).to_dict()
        for key in ("N", "n00", "n01", "n10", "n11", "b", "c", "d",
                    "risk_difference", "p_value", "ci_low", "ci_high",
                    "alpha", "confidence"):
            assert key in d


class TestMissingness:
    def test_incomplete_pairs_are_reported_not_dropped_as_outcomes(self):
        obs = _pairs(3, 1, 2, 4) + [
            PairedObservation("t9", 0, None, True, "infrastructure:trajectory_malformed"),
            PairedObservation("t9", 1, True, None, "infrastructure:workspace_preparation_failed"),
            PairedObservation("t9", 2, None, None, "scientific:no_event_stream"),
        ]
        r = finalize_f2(obs)
        assert r.n_total == 13
        assert r.n_complete == 10
        assert r.n_missing == 3
        assert r.missing_by_reason["infrastructure:trajectory_malformed"] == 1
        assert r.missing_by_reason["scientific:no_event_stream"] == 1
        # The complete-pair statistics are UNCHANGED by the missing rows.
        assert (r.b, r.c, r.d) == (3, 1, 4)
        assert r.risk_difference == pytest.approx(0.2)

    def test_missing_is_not_treated_as_failure(self):
        # If missing were coerced to False, b would change; it must not.
        only_missing = [PairedObservation("t", 0, None, True, "x")]
        r = finalize_f2(only_missing)
        assert r.n_complete == 0
        assert r.b == 0 and r.c == 0


class TestIndependentReconstruction:
    def test_matches_finalize_on_all_fixtures(self):
        for b, c, both, neither in FIXTURES:
            pairs = _pairs(b, c, both, neither)
            prod = finalize_f2(pairs)
            ind = independent_reconstruction(pairs)
            assert ind["N"] == prod.n_complete
            assert ind["b"] == prod.b and ind["c"] == prod.c and ind["d"] == prod.d
            assert ind["risk_difference"] == pytest.approx(prod.risk_difference, abs=1e-9)
            assert ind["p_value"] == pytest.approx(prod.p_value, abs=1e-9)
            assert ind["ci_low"] == pytest.approx(prod.ci_low, abs=1e-6)
            assert ind["ci_high"] == pytest.approx(prod.ci_high, abs=1e-6)

    def test_does_not_call_the_production_statistics(self, monkeypatch):
        import qwen_train.f2_statistics as fs

        def boom(*a, **k):  # noqa: ANN001
            raise AssertionError("independent path must not call f2_statistics")

        monkeypatch.setattr(fs, "mcnemar_exact", boom)
        # Independent path still works...
        out = independent_reconstruction(_pairs(5, 0, 5, 0))
        assert out["risk_difference"] == pytest.approx(0.5)
        assert out["ci_low"] == pytest.approx(-0.0218, abs=1e-3)
        # ...and the production path genuinely depends on the frozen function.
        with pytest.raises(AssertionError):
            finalize_f2(_pairs(5, 0, 5, 0))

    def test_zero_complete_pairs_is_conservative(self):
        ind = independent_reconstruction([])
        assert ind["N"] == 0
        assert ind["p_value"] == 1.0
        assert ind["ci_low"] == -1.0 and ind["ci_high"] == 1.0

    def test_coercion_of_tuples_mappings_and_objects(self):
        a = finalize_f2([(True, False), {"t_endpoint": True, "x_endpoint": False}])
        b = finalize_f2([PairedObservation("t", 0, True, False)])
        assert a.to_dict()["risk_difference"] == b.to_dict()["risk_difference"]
