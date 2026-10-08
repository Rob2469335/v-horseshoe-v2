"""Exact paired-binary statistics for Experiment J F2 (R2).

Every number used to plan F2 must be DERIVED by a tested function, not asserted
in prose. This suite pins the behaviour of ``qwen_train.f2_statistics``:

* the exact conditional McNemar p-value, verified against hand-computable cases;
* two-sided vs one-sided behaviour, and that HARM is reportable;
* the paired risk-difference point estimate and its exact conditional interval;
* power and required-N as a function of EXPLICIT assumptions only;
* fail-closed behaviour when assumptions are incoherent (delta > discordance).

Pair fixtures are written as explicit ``(t, x)`` booleans so no reader has to
decode a string. No experiment is executed; every fixture is a labelled
synthetic arithmetic check, not a manufactured observation.
"""
from __future__ import annotations

from math import comb

import pytest

from qwen_train.f2_statistics import (
    binom_cdf,
    binom_sf,
    contingency_table,
    exact_power,
    mcnemar_exact,
    paired_risk_difference,
    required_pairs,
)

# (T, X) per task.
B_FAVOURS_T = (True, False)  # discordant, favours treatment
C_FAVOURS_X = (False, True)  # discordant, favours control
BOTH = (True, True)  # concordant success
NEITHER = (False, False)  # concordant failure


def _seq(*kinds):
    return [k for k in kinds]


class TestContingencyTable:
    def test_counts(self):
        t, x = zip(*_seq(B_FAVOURS_T, B_FAVOURS_T, C_FAVOURS_X, BOTH, NEITHER))
        b, c, both, neither = contingency_table(t, x)
        assert (b, c, both, neither) == (2, 1, 1, 1)

    def test_length_mismatch_fails_closed(self):
        with pytest.raises(ValueError, match="same length"):
            contingency_table([True, False], [True])


class TestExactMcNemar:
    def test_no_discordant_pairs_is_not_significant(self):
        t, x = zip(*_seq(BOTH, BOTH, NEITHER, NEITHER))
        r = mcnemar_exact(t, x)
        assert r.n_discordant == 0
        assert r.p_value == 1.0

    def test_perfect_split_is_significant_at_five_pairs(self):
        t, x = zip(*([B_FAVOURS_T] * 5))
        r = mcnemar_exact(t, x, alpha=0.05, sided="one-sided")
        assert (r.b, r.c) == (5, 0)
        assert r.p_value == pytest.approx(0.03125)
        assert r.p_value < 0.05

    def test_four_perfect_pairs_are_NOT_significant(self):
        """The dispositive fact for a 4-task population."""
        t, x = zip(*([B_FAVOURS_T] * 4))
        r = mcnemar_exact(t, x, alpha=0.05, sided="one-sided")
        assert r.p_value == pytest.approx(0.0625)
        assert r.p_value >= 0.05

    def test_harm_is_detected_and_labelled(self):
        t, x = zip(*([C_FAVOURS_X] * 5))
        r = mcnemar_exact(t, x, alpha=0.05, sided="two-sided")
        assert (r.b, r.c) == (0, 5)
        assert r.risk_difference < 0
        assert r.direction == "favours_control"

    def test_two_sided_is_more_conservative_than_one_sided(self):
        t, x = zip(*([B_FAVOURS_T] * 6))
        one = mcnemar_exact(t, x, alpha=0.05, sided="one-sided")
        two = mcnemar_exact(t, x, alpha=0.05, sided="two-sided")
        assert two.p_value >= one.p_value

    def test_risk_difference_point_estimate(self):
        t, x = zip(*_seq(B_FAVOURS_T, B_FAVOURS_T, C_FAVOURS_X, BOTH, NEITHER))
        r = mcnemar_exact(t, x)
        assert r.risk_difference == pytest.approx((r.b - r.c) / r.n_pairs)


class TestPairedRiskDifference:
    def test_point_and_interval_bracket_the_estimate(self):
        t, x = zip(*([B_FAVOURS_T] * 5 + [NEITHER] * 5))
        est, lo, hi = paired_risk_difference(t, x)
        assert lo <= est <= hi
        assert -1.0 <= lo <= 1.0 and -1.0 <= hi <= 1.0

    def test_perfect_agreement_interval_is_wide(self):
        t, x = zip(*([BOTH] * 6))
        est, lo, hi = paired_risk_difference(t, x)
        assert est == pytest.approx(0.0)
        assert lo < 0.0 < hi  # honest about the uncertainty

    def test_no_discordance_interval_is_not_degenerate(self):
        t, x = zip(*([BOTH] * 8 + [NEITHER] * 8))
        est, lo, hi = paired_risk_difference(t, x)
        assert est == pytest.approx(0.0)
        assert lo < 0.0 < hi


class TestPowerAndSampleSize:
    def test_power_at_zero_discordance_is_alpha(self):
        assert exact_power(0, 0, alpha=0.05) == pytest.approx(0.05)

    def test_more_discordance_needs_MORE_pairs_for_fixed_delta(self):
        """Lower discordance CONCENTRES the directional signal.

        At fixed delta the fraction of discordant pairs favouring T is
        theta = (pi_d + delta) / (2*pi_d), which approaches 0.5 as pi_d grows.
        So a HIGHER discordance is a WEAKER alternative and needs MORE pairs --
        the opposite of the naive intuition.
        """
        low = required_pairs(delta=0.20, discordance=0.30, sided="two-sided")
        high = required_pairs(delta=0.20, discordance=0.70, sided="two-sided")
        assert low.required_pairs is not None and high.required_pairs is not None
        assert high.required_pairs > low.required_pairs

    def test_smaller_delta_needs_more_pairs(self):
        big = required_pairs(delta=0.30, discordance=0.50, sided="two-sided")
        small = required_pairs(delta=0.10, discordance=0.50, sided="two-sided")
        assert big.required_pairs is not None and small.required_pairs is not None
        assert small.required_pairs > big.required_pairs

    def test_two_sided_needs_at_least_as_many_as_one_sided(self):
        one = required_pairs(delta=0.20, discordance=0.50, sided="one-sided")
        two = required_pairs(delta=0.20, discordance=0.50, sided="two-sided")
        assert one.required_pairs is not None and two.required_pairs is not None
        assert two.required_pairs >= one.required_pairs

    def test_plan_records_every_assumption(self):
        plan = required_pairs(delta=0.20, discordance=0.50, sided="two-sided")
        a = plan.assumptions()
        for key in ("delta", "discordance_pi_d", "alpha", "target_power", "sided", "test"):
            assert key in a

    def test_incoherent_delta_exceeds_discordance_fails_closed(self):
        with pytest.raises(ValueError, match="cannot be larger"):
            required_pairs(delta=0.60, discordance=0.30)

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
    def test_invalid_discordance_fails_closed(self, bad):
        with pytest.raises(ValueError):
            required_pairs(delta=0.2, discordance=bad)

    def test_nonpositive_delta_fails_closed(self):
        with pytest.raises(ValueError, match="positive"):
            required_pairs(delta=0.0, discordance=0.5)

    def test_achieved_power_at_the_plan_meets_target(self):
        plan = required_pairs(delta=0.25, discordance=0.50, sided="two-sided")
        assert plan.required_pairs is not None
        m = plan.required_pairs
        n = round(m * plan.discordance)
        b = round((n + m * plan.delta) / 2.0)
        c = n - b
        assert exact_power(b, c, alpha=plan.alpha, sided=plan.sided) >= plan.power


# ---------------------------------------------------------------------------
# F2-IMPL-AUTH-015 - Clopper-Pearson bisection regression
# ---------------------------------------------------------------------------


class TestClopperPearsonBisection:
    """The CP bounds must match the standard exact binomial interval.

    Reference values are R's binom.test(k, n)$conf.int at 95%.
    A prior bisection searched [0, n] instead of [0, 1] and derived a count from
    the probability midpoint, returning impossible bounds (e.g. 2.0 for n=20).
    """

    CASES = [
        (3, 6, 0.1181, 0.8819),
        (6, 6, 0.5407, 1.0000),
        (6, 20, 0.1189, 0.5428),
        (10, 20, 0.2719, 0.7281),
    ]

    def test_bounds_match_the_standard_interval(self):
        from qwen_train.f2_statistics import (
            _clopper_pearson_lower,
            _clopper_pearson_upper,
        )

        for k, n, lo_exp, hi_exp in self.CASES:
            lo = _clopper_pearson_lower(k, n, 0.025)
            hi = _clopper_pearson_upper(k, n, 0.025)
            assert abs(lo - lo_exp) < 0.001, (k, n, lo, lo_exp)
            assert abs(hi - hi_exp) < 0.001, (k, n, hi, hi_exp)

    def test_bounds_are_valid_probabilities(self):
        from qwen_train.f2_statistics import (
            _clopper_pearson_lower,
            _clopper_pearson_upper,
        )

        for n in (6, 20, 300):
            for k in range(1, n):
                lo = _clopper_pearson_lower(k, n, 0.025)
                hi = _clopper_pearson_upper(k, n, 0.025)
                assert 0.0 <= lo <= 1.0, (k, n, lo)
                assert 0.0 <= hi <= 1.0, (k, n, hi)
                assert lo <= hi, (k, n, lo, hi)

    def test_edges_are_clamped(self):
        from qwen_train.f2_statistics import (
            _clopper_pearson_lower,
            _clopper_pearson_upper,
        )

        assert _clopper_pearson_lower(0, 20, 0.025) == 0.0
        assert _clopper_pearson_upper(20, 20, 0.025) == 1.0


# ---------------------------------------------------------------------------
# ONE authoritative confirmatory interval (Clopper-Pearson on the discordant
# direction, transformed to the paired risk difference). F2-IMPL-AUTH-016.
# ---------------------------------------------------------------------------


class TestAuthoritativeConfidenceInterval:
    """`mcnemar_exact` and `paired_risk_difference` must report ONE interval.

    The frozen contract (`F2-CLARIFICATION-004`) fixes the confirmatory interval
    as the Clopper-Pearson exact conditional interval on the discordant
    direction, transformed to the paired risk difference. A prior
    `mcnemar_exact` path reported a Wald interval instead, so an analysis could
    have reported two different "F2 confidence intervals". These tests lock the
    two entry points to the same construction.
    """

    # (b, c, both, neither) fixtures.
    FIXTURES = [
        (6, 14, 0, 0),
        (10, 10, 0, 0),
        (3, 3, 0, 0),
        (5, 0, 5, 0),
        (0, 3, 7, 0),
        (2, 1, 1, 4),
        (0, 0, 10, 0),
    ]

    @staticmethod
    def _seq(b, c, both, neither):
        t = [True] * b + [False] * c + [True] * both + [False] * neither
        x = [False] * b + [True] * c + [True] * both + [False] * neither
        return t, x

    def test_mcnemar_and_paired_risk_difference_agree(self):
        for b, c, both, neither in self.FIXTURES:
            t, x = self._seq(b, c, both, neither)
            r = mcnemar_exact(t, x)
            est, lo, hi = paired_risk_difference(t, x)
            assert (r.b, r.c) == (b, c)
            assert r.risk_difference == pytest.approx(est, abs=1e-12)
            assert r.ci_low == pytest.approx(lo, abs=1e-12)
            assert r.ci_high == pytest.approx(hi, abs=1e-12)

    # R's binom.test(k, n, conf.level=0.95)$conf.int, transformed to the risk
    # difference with m = n (so scale = d/m = 1): RD = 2*p - 1.
    R_ORACLE = [
        # (b, c, both, neither, expected RD_low, expected RD_high)
        (3, 3, 0, 0, 2 * 0.1181 - 1.0, 2 * 0.8819 - 1.0),   # k=3, n=6
        (6, 14, 0, 0, 2 * 0.1189 - 1.0, 2 * 0.5428 - 1.0),  # k=6, n=20
        (10, 10, 0, 0, 2 * 0.2719 - 1.0, 2 * 0.7281 - 1.0),  # k=10, n=20
        (6, 0, 0, 0, 2 * 0.5407 - 1.0, 1.0),               # k=6, n=6 (upper clamped)
    ]

    def test_interval_matches_r_transformed_oracle(self):
        for b, c, both, neither, lo_exp, hi_exp in self.R_ORACLE:
            t, x = self._seq(b, c, both, neither)
            r = mcnemar_exact(t, x)
            assert r.ci_low == pytest.approx(lo_exp, abs=1e-3), (b, c, r.ci_low, lo_exp)
            assert r.ci_high == pytest.approx(hi_exp, abs=1e-3), (b, c, r.ci_high, hi_exp)

    def test_no_discordance_is_conservative_not_degenerate(self):
        t, x = self._seq(0, 0, 8, 8)
        r = mcnemar_exact(t, x)
        assert r.ci_low < 0.0 < r.ci_high
        assert r.risk_difference == pytest.approx(0.0)

    def test_confirmatory_interval_is_not_the_wald_interval(self):
        """Lock in the CP construction; a Wald interval would exclude 0 here."""
        from qwen_train.f2_statistics import _wald_halfwidth

        b, c, both, neither = 5, 0, 5, 0  # d=5, m=10
        t, x = self._seq(b, c, both, neither)
        r = mcnemar_exact(t, x)
        wald_hw = _wald_halfwidth(r.n_pairs)
        # CP interval includes 0; the Wald interval around RD=0.5 would not.
        assert r.ci_low < 0.0
        assert r.ci_low == pytest.approx(-0.0218, abs=1e-3)
        assert r.risk_difference - wald_hw > 0.0


# ---------------------------------------------------------------------------
# F2-CLARIFICATION-005 - what required_pairs ACTUALLY returns, and the one
# invariant that protects the frozen rule n = max(300, required_pairs(...)).
# ---------------------------------------------------------------------------


class TestRequiredPairsInterpretation:
    """The return value is a rounded-cell, CONDITIONAL FIRST CROSSING.

    These tests do not re-decide the design number. They pin the MEANING of the
    function so 116 cannot later be quoted as an unconditional total-pair
    requirement, and they pin the invariant that actually makes the frozen
    ``n = max(300, ...)`` equal 300 for every authorized planning value.
    """

    AUTHORIZED = dict(
        delta=0.20, discordance=0.50, alpha=0.05, power=0.90, sided="two-sided"
    )

    @staticmethod
    def _conditional_power_at(m, delta=0.20, pi_d=0.50, alpha=0.05):
        """The exact power required_pairs evaluates for a given m."""
        n = round(m * pi_d)
        b = round((n + m * delta) / 2.0)
        c = n - b
        return exact_power(b, c, alpha=alpha, sided="two-sided")

    def test_it_matches_the_value_recorded_in_f2_impl_auth_013(self):
        plan = required_pairs(**self.AUTHORIZED)
        assert plan.required_pairs == 116
        assert max(300, plan.required_pairs) == 300

    def test_the_crossing_is_not_monotone_in_m(self):
        """m = 116 clears the target and m = 118 does not.

        Both halves are the contract: the achieved-power curve is a sawtooth,
        because n and b are rounded and the exact two-sided critical value is a
        step function of n. A later reader must not "fix" 118 by assuming the
        curve rises, and must not read 116 as a guaranteed lower bound.
        """
        at_116 = self._conditional_power_at(116)
        at_118 = self._conditional_power_at(118)
        at_130 = self._conditional_power_at(130)
        assert at_116 >= 0.90
        assert at_118 < 0.90, "power FELL when m grew - that is the documented shape"
        assert at_118 < at_116
        assert at_130 >= 0.90
        assert at_130 < 1.0

    @pytest.mark.parametrize("pi_d", [0.20, 0.30, 0.50, 0.75, 1.00])
    def test_every_authorized_sensitivity_point_stays_below_the_frozen_minimum(
        self, pi_d
    ):
        """THE invariant protecting n: max(300, required) == 300 everywhere."""
        plan = required_pairs(
            delta=0.20, discordance=pi_d, alpha=0.05, power=0.90, sided="two-sided"
        )
        assert plan.required_pairs is not None, pi_d
        assert plan.required_pairs < 300, pi_d
        assert max(300, plan.required_pairs) == 300, pi_d

    def test_pi_d_below_delta_is_refused_rather_than_estimated(self):
        """AUTH-013's sensitivity row '0.10 | not attainable' is the code's rule."""
        with pytest.raises(ValueError, match="cannot be larger"):
            required_pairs(delta=0.20, discordance=0.10, power=0.90)

    def test_the_plan_caveats_its_own_return_value(self):
        """The metadata must not claim the returned N is a safe over-estimate."""
        note = required_pairs(**self.AUTHORIZED).assumptions()["note"]
        assert "FIRST m" in note
        assert "OVER-estimate" not in note


class TestBinomialTailNumericalStability:
    """comb(n, i) cannot be converted to a float once it exceeds ~1.8e308.

    Above roughly n = 1024 the exact-integer product raises OverflowError, which
    used to abort the whole calculation. The fallback must return the same value
    the exact integer arithmetic gives, not an approximation nobody checked.
    """

    N = 2000

    @staticmethod
    def _exact_tail(lo: int, hi: int, n: int) -> float:
        """Exact rational tail, integer arithmetic only."""
        return sum(comb(n, i) for i in range(lo, hi + 1)) / (1 << n)

    def test_large_n_no_longer_raises(self):
        assert 0.0 <= binom_sf(1000, self.N, 0.5) <= 1.0
        assert 0.0 <= binom_cdf(999, self.N, 0.5) <= 1.0

    def test_large_n_matches_an_exact_integer_oracle(self):
        assert binom_sf(1000, self.N, 0.5) == pytest.approx(
            self._exact_tail(1000, self.N, self.N), rel=1e-9
        )
        assert binom_cdf(999, self.N, 0.5) == pytest.approx(
            self._exact_tail(0, 999, self.N), rel=1e-9
        )

    def test_the_two_tails_still_partition_the_distribution(self):
        for k in (1000, 1234, 1750):
            sf = binom_sf(k, self.N, 0.5)
            cdf = binom_cdf(k - 1, self.N, 0.5)
            assert sf + cdf == pytest.approx(1.0, abs=1e-9), k

    def test_the_exact_path_is_untouched_where_it_already_worked(self):
        """The fallback may only run on OverflowError - never in place of exacts."""
        for k, n in ((6, 20), (10, 20), (150, 300), (500, 1000)):
            exact = sum(
                comb(n, i) * 0.5**i * 0.5 ** (n - i) for i in range(k, n + 1)
            )
            assert binom_sf(k, n, 0.5) == exact
