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

import pytest

from qwen_train.f2_statistics import (
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