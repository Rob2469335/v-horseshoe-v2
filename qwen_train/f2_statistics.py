"""Exact paired-binary statistics for Experiment J F2 (R2).

Why exact conditional McNemar
----------------------------
F2's primary endpoint is BINARY and the design is PAIRED: one task contributes a
T observation and an X observation under an identical frozen artifact. Ordinary
independent two-sample proportion formulas are therefore wrong twice over -- they
model the wrong dependence and, empirically, inflate required N by a median
factor of ~2.15 on shared-item evaluations (arXiv 2605.30315, "Resolution
Diagnostics for Paired LLM Evaluation", which derives the Connor 1987 paired
required-N and calibrates five McNemar variants).

The test conditions on the DISCORDANT pairs: under H0 the number of discordant
pairs favouring T is Binomial(b + c, 1/2). This module implements that exactly,
with no normal approximation, because the plausible confirmatory N for this
design is small enough that asymptotic chi-square is unjustified.

Methodological constraints taken from current SOTA
--------------------------------------------------
* No bootstrap. evalstats (arXiv 2609.35815) reports that on paired binary data
  NO bootstrap variant reached nominal coverage even at N = 100, and explicitly
  advises against bootstrap CIs for N < 100. F0 section 11's illustrative
  "bootstrap CI for risk difference" is therefore NOT used.
* Two-sided by default. VRL-Bench (arXiv 2609.12404) finds verbal-memory
  updates that REDUCE success, and "Honest Lying" (2026) documents
  memory-HARMFUL environments, so a benefit-only test would make harm
  unreportable. ``sided`` is a parameter, not an assumption.
* Exact/conditional variants are ~3pp CONSERVATIVE relative to the asymptotic
  normal approximation (arXiv 2605.30315), so required N computed here is a
  slight OVER-estimate. That is the safe direction for planning.

Every function here is pure and offline: no experiment is executed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from math import comb
from typing import Iterable, Literal

__all__ = [
    "McNemarResult",
    "binom_sf",
    "mcnemar_exact",
    "paired_risk_difference",
    "exact_power",
    "required_pairs",
    "PowerPlan",
    "contingency_table",
]

Sided = Literal["one-sided", "two-sided"]


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p). Numerically stable for small n."""
    if n <= 0:
        return 1.0
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k, n + 1))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if n <= 0:
        return 1.0
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(0, k + 1))


@dataclass(frozen=True)
class McNemarResult:
    """Outcome of an exact conditional McNemar test on paired binary data."""

    n_pairs: int
    b: int  # T succeeds, X fails  (favours treatment)
    c: int  # X succeeds, T fails  (favours control)
    n_concordant_t: int  # both succeed
    n_concordant_x: int  # both fail
    p_value: float
    risk_difference: float
    ci_low: float
    ci_high: float
    sided: str

    @property
    def n_discordant(self) -> int:
        return self.b + self.c

    @property
    def direction(self) -> str:
        if self.risk_difference > 0:
            return "favours_treatment"
        if self.risk_difference < 0:
            return "favours_control"
        return "null"


def contingency_table(t_values: Iterable[bool], x_values: Iterable[bool]):
    """Build (b, c, both, neither) from paired boolean sequences.

    Fails closed on a length mismatch: silently truncating would change the
    denominator and could turn a discordant pair into a missing one.
    """
    t = list(t_values)
    x = list(x_values)
    if len(t) != len(x):
        raise ValueError(
            f"paired sequences must be the same length: T={len(t)} X={len(x)}"
        )
    b = sum(1 for ti, xi in zip(t, x) if ti and not xi)
    c = sum(1 for ti, xi in zip(t, x) if (not ti) and xi)
    both = sum(1 for ti, xi in zip(t, x) if ti and xi)
    neither = sum(1 for ti, xi in zip(t, x) if (not ti) and (not xi))
    return b, c, both, neither


def _critical_value(n_discordant: int, alpha: float, sided: Sided) -> int | None:
    """Smallest k whose H0 tail is <= alpha (binom_sf decreases in k)."""
    if n_discordant <= 0:
        return None
    if sided == "one-sided":
        t = 1
        while t <= n_discordant and binom_sf(t, n_discordant, 0.5) > alpha:
            t += 1
        return t if t <= n_discordant else None
    # two-sided: symmetric doubling of the one-sided tail
    t = 0
    while t <= n_discordant and 2.0 * binom_sf(t + 1, n_discordant, 0.5) > alpha:
        t += 1
    return t + 1 if t + 1 <= n_discordant else None


def mcnemar_exact(
    t_values: Iterable[bool],
    x_values: Iterable[bool],
    *,
    alpha: float = 0.05,
    sided: Sided = "two-sided",
) -> McNemarResult:
    """Exact conditional McNemar test on paired binary observations.

    p-value: under H0 the discordant split is Binomial(b + c, 1/2). Two-sided p
    is the symmetric double of the smaller tail; one-sided p is the tail in the
    observed direction only.
    """
    b, c, both, neither = contingency_table(t_values, x_values)
    n = b + c
    m = b + c + both + neither
    if n == 0:
        # No discordant pair => the test cannot reject; report p = 1 honestly
        # rather than claiming evidence of equivalence.
        rd = 0.0
        return McNemarResult(
            n_pairs=m,
            b=0,
            c=0,
            n_concordant_t=both,
            n_concordant_x=neither,
            p_value=1.0,
            risk_difference=rd,
            ci_low=-_wald_halfwidth(m),
            ci_high=_wald_halfwidth(m),
            sided=sided,
        )

    k = max(b, c)
    if sided == "one-sided":
        p = binom_sf(k, n, 0.5)
    else:
        p = min(1.0, 2.0 * binom_sf(k, n, 0.5))

    rd = (b - c) / m if m else 0.0
    # Risk-difference interval. Not a bootstrap (see module docstring): the
    # conditional (Bonett-Price style) interval is reported by
    # ``paired_risk_difference``; this keeps McNemarResult self-contained with a
    # conservative normal-approximation half-width.
    hw = _wald_halfwidth(m)
    return McNemarResult(
        n_pairs=m,
        b=b,
        c=c,
        n_concordant_t=both,
        n_concordant_x=neither,
        p_value=p,
        risk_difference=rd,
        ci_low=max(-1.0, rd - hw),
        ci_high=min(1.0, rd + hw),
        sided=sided,
    )


def _wald_halfwidth(m: int, z: float = 1.959963985) -> float:
    """Conservative normal half-width for a paired proportion, m pairs.

    Uses the unpaired worst case (var <= 1/4 per arm) so the interval can only
    be too wide, never too narrow. ``evalstats`` shows bootstrap intervals are
    OVERconfident here, so erring wide is the correct direction.
    """
    if m <= 0:
        return 1.0
    return z * math.sqrt(1.0 / (4.0 * m))


def paired_risk_difference(
    t_values: Iterable[bool],
    x_values: Iterable[bool],
    *,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Return (point estimate, CI low, CI high) for the paired risk difference.

    Uses the exact conditional structure: given the discordant count n = b + c,
    the conditional distribution of b is Binomial(n, theta) and theta is estimated
    by b/n. The interval is the Clopper-Pearson (exact binomial) interval for
    theta, mapped to the risk difference via
    ``RD = (2*theta - 1) * n / m``. That is the standard exact conditional
    construction for McNemar-style paired proportions and, unlike a bootstrap,
    has correct coverage at small m (arXiv 2605.30315; Bonett & Price 2012 for
    the asymptotically-smoother alternative reported there).
    """
    b, c, both, neither = contingency_table(t_values, x_values)
    m = b + c + both + neither
    n = b + c
    if m == 0:
        return 0.0, -1.0, 1.0
    if n == 0:
        # No discordance: the plug-in paired RD is exactly 0 with ZERO paired
        # variance, so a naive interval collapses to [0, 0] and claims a
        # certainty the data does not support -- exactly the overconfidence
        # evalstats documents for small-sample paired intervals. Report the
        # conservative unpaired WORST-CASE half-width instead (var <= 1/4 per
        # arm), so the interval can only be too wide, never too narrow.
        p_t = (b + both) / m
        p_x = (c + both) / m  # X succeeds on c (discordant) or both (concordant)
        hw = _wald_halfwidth(m)
        return p_t - p_x, max(-1.0, p_t - p_x - hw), min(1.0, p_t - p_x + hw)

    alpha = 1.0 - confidence
    # Clopper-Pearson via the Beta quantile relation, solved by bisection on the
    # binomial tail (no scipy dependency).
    lo = _clopper_pearson_lower(b, n, alpha / 2.0)
    hi = _clopper_pearson_upper(b, n, alpha / 2.0)
    scale = n / m
    return (
        (b - c) / m,
        max(-1.0, (2.0 * lo - 1.0) * scale),
        min(1.0, (2.0 * hi - 1.0) * scale),
    )


def _binom_cdf_bisect(x: float, n: int, target: float, *, upper: bool) -> float:
    """Solve P(X <= x) or P(X >= x) = target for x in [0, n] by bisection."""
    lo, hi = 0.0, float(n)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        k = int(math.floor(mid))
        val = binom_cdf(k, n, x) if not upper else 1.0 - binom_cdf(k - 1, n, x)
        if val < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _clopper_pearson_lower(k: int, n: int, alpha: float) -> float:
    if k == 0:
        return 0.0
    return _binom_cdf_bisect(k / n, n, alpha, upper=True)


def _clopper_pearson_upper(k: int, n: int, alpha: float) -> float:
    if k == n:
        return 1.0
    return _binom_cdf_bisect(k / n, n, alpha, upper=False)


def exact_power(b: int, c: int, *, alpha: float = 0.05, sided: Sided = "two-sided") -> float:
    """Exact power of the McNemar test at true discordant counts (b, c).

    H0 rejection threshold is derived at the nominal alpha; power is then the
    probability of exceeding it under the Binomial(n, b/n) alternative.
    """
    n = b + c
    if n == 0:
        return alpha if sided == "one-sided" else alpha
    t = _critical_value(n, alpha, sided)
    if t is None:
        return alpha
    return binom_sf(t, n, b / n)


@dataclass(frozen=True)
class PowerPlan:
    """A documented, reproducible sample-size plan.

    Every field is an EXPLICIT assumption. There is no default delta and no
    default discordance: both must be supplied, because both are empirical
    quantities and inventing them would fabricate the sample size.
    """

    delta: float
    discordance: float
    alpha: float
    power: float
    sided: Sided
    required_pairs: int | None
    achieved_power_at: int | None

    def assumptions(self) -> dict:
        return {
            "delta": self.delta,
            "discordance_pi_d": self.discordance,
            "alpha": self.alpha,
            "target_power": self.power,
            "sided": self.sided,
            "test": "exact conditional McNemar (binomial on discordant pairs)",
            "ci": "Clopper-Pearson conditional interval on the paired risk difference",
            "estimator": "paired risk difference (b - c) / m",
            "note": (
                "Exact conditional variants are ~3pp conservative vs the "
                "asymptotic normal approximation (arXiv 2605.30315), so this N "
                "is a slight OVER-estimate."
            ),
        }


def required_pairs(
    *,
    delta: float,
    discordance: float,
    alpha: float = 0.05,
    power: float = 0.80,
    sided: Sided = "two-sided",
    max_pairs: int = 5000,
) -> PowerPlan:
    """Smallest m reaching ``power`` for a target risk difference ``delta``.

    ``discordance`` is the expected fraction of pairs that are discordant
    (pi_d = (b + c) / m). It is an EMPIRICAL quantity: for Experiment J it must
    come from the no-lesson X/C0 calibration, not from this module.

    Given pi_d and delta, the discordant counts are
    ``b = m(pi_d + delta)/2`` and ``c = m(pi_d - delta)/2``, which requires
    ``delta <= pi_d`` (a net risk difference cannot exceed the discordant rate).
    That constraint is checked, not silently clamped.
    """
    if not 0.0 < discordance <= 1.0:
        raise ValueError(f"discordance must be in (0, 1], got {discordance}")
    if delta <= 0:
        raise ValueError(f"delta must be positive, got {delta}")
    if delta > discordance:
        raise ValueError(
            f"delta={delta} exceeds discordance={discordance}: the paired risk "
            "difference cannot be larger than the fraction of pairs that differ "
            "at all. Supply a larger discordance (from calibration) or a smaller "
            "effect of interest."
        )
    for m in range(2, max_pairs + 1):
        n = round(m * discordance)
        b = round((n + m * delta) / 2.0)
        c = n - b
        if b < 0 or c < 0:
            continue
        if exact_power(b, c, alpha=alpha, sided=sided) >= power:
            return PowerPlan(
                delta=delta,
                discordance=discordance,
                alpha=alpha,
                power=power,
                sided=sided,
                required_pairs=m,
                achieved_power_at=m,
            )
    return PowerPlan(
        delta=delta,
        discordance=discordance,
        alpha=alpha,
        power=power,
        sided=sided,
        required_pairs=None,
        achieved_power_at=None,
    )