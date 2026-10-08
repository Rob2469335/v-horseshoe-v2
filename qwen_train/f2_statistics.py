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
  normal approximation (arXiv 2605.30315). That comparison is about the TEST,
  and it is the safe direction for planning.

  It is NOT a statement about the value ``required_pairs`` returns. See
  ``required_pairs`` for what that return value is and is not: it is a
  rounded-cell, conditional FIRST CROSSING of a non-monotone curve, and it can
  sit BELOW the sample size at which the power target is robustly met.

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


def _binom_log_sum(lo: int, hi: int, n: int, p: float) -> float:
    """Sum the binomial pmf over ``lo <= i <= hi`` in log space.

    Fallback for n large enough that ``comb(n, i)`` can no longer be converted
    to a float (comb(2000, 1000) is ~1e600, and ``int * float`` raises
    ``OverflowError``). Only reached when the exact-integer path overflows; the
    exact path is preferred wherever it works because it keeps the arithmetic
    integral until the final product.
    """
    if lo > hi:
        return 0.0
    if p <= 0.0:
        return 1.0 if lo <= 0 else 0.0
    if p >= 1.0:
        return 0.0 if hi < n else 1.0
    lp = math.log(p)
    lq = math.log1p(-p)
    log_n = math.lgamma(n + 1)
    total = 0.0
    for i in range(lo, hi + 1):
        total += math.exp(
            log_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq
        )
    return min(1.0, total)


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p). Numerically stable for small n."""
    if n <= 0:
        return 1.0
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    try:
        return sum(comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k, n + 1))
    except OverflowError:
        # comb(n, i) too large for a float: fall back to log space rather than
        # failing the whole calculation.
        return _binom_log_sum(k, n, n, p)


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if n <= 0:
        return 1.0
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    try:
        return sum(comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(0, k + 1))
    except OverflowError:
        return _binom_log_sum(0, k, n, p)


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

    The reported ``ci_low``/``ci_high`` are the ONE authoritative confirmatory
    interval: the two-sided 95% Clopper-Pearson exact conditional interval on the
    discordant direction, transformed to the paired risk difference (see
    ``_paired_rd_and_interval``). There is no separate Wald interval on this
    path; the frozen F2 interval is produced by the same helper that
    ``paired_risk_difference`` uses, so the two entry points cannot disagree.
    """
    b, c, both, neither = contingency_table(t_values, x_values)
    n = b + c
    m = b + c + both + neither
    if n == 0:
        # No discordant pair => the test cannot reject; report p = 1 honestly
        # rather than claiming evidence of equivalence. The interval is the
        # authorized conservative paired interval, never a degenerate [0, 0].
        rd, ci_low, ci_high = _paired_rd_and_interval(b, c, both, neither)
        return McNemarResult(
            n_pairs=m,
            b=0,
            c=0,
            n_concordant_t=both,
            n_concordant_x=neither,
            p_value=1.0,
            risk_difference=rd,
            ci_low=ci_low,
            ci_high=ci_high,
            sided=sided,
        )

    k = max(b, c)
    if sided == "one-sided":
        p = binom_sf(k, n, 0.5)
    else:
        p = min(1.0, 2.0 * binom_sf(k, n, 0.5))

    rd, ci_low, ci_high = _paired_rd_and_interval(b, c, both, neither)
    return McNemarResult(
        n_pairs=m,
        b=b,
        c=c,
        n_concordant_t=both,
        n_concordant_x=neither,
        p_value=p,
        risk_difference=rd,
        ci_low=ci_low,
        ci_high=ci_high,
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


def _paired_rd_and_interval(
    b: int,
    c: int,
    both: int,
    neither: int,
    *,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """The ONE authoritative paired risk-difference estimate and 95% interval.

    With ``d = b + c`` discordant pairs and ``m`` total pairs:

    * point estimate ``RD = (b - c) / m``;
    * the two-sided ``confidence`` Clopper-Pearson exact conditional interval
      ``[p_L, p_U]`` for the discordant direction ``p = b / d`` (Clopper-Pearson
      via the binomial tail, solved by bisection -- no scipy dependency),
      transformed to the risk difference by
      ``RD_L = d * (2*p_L - 1) / m`` and ``RD_U = d * (2*p_U - 1) / m``
      (equivalently ``(2*p - 1) * d / m``).

    ``d == 0`` is the authorized conservative case: the plug-in paired RD has
    zero paired variance, so a naive interval would collapse to ``[0, 0]`` and
    claim a certainty the data does not support. The unpaired worst-case
    half-width (``var <= 1/4`` per arm) is reported instead, so the interval can
    only be too wide, never too narrow.

    This helper is shared by ``mcnemar_exact`` and ``paired_risk_difference`` so
    the two entry points cannot report different confirmatory intervals.
    """
    m = b + c + both + neither
    n = b + c
    if m == 0:
        return 0.0, -1.0, 1.0
    if n == 0:
        hw = _wald_halfwidth(m)
        return 0.0, max(-1.0, -hw), min(1.0, hw)

    alpha = 1.0 - confidence
    lo = _clopper_pearson_lower(b, n, alpha / 2.0)
    hi = _clopper_pearson_upper(b, n, alpha / 2.0)
    scale = n / m
    return (
        (b - c) / m,
        max(-1.0, (2.0 * lo - 1.0) * scale),
        min(1.0, (2.0 * hi - 1.0) * scale),
    )


def paired_risk_difference(
    t_values: Iterable[bool],
    x_values: Iterable[bool],
    *,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Return (point estimate, CI low, CI high) for the paired risk difference.

    Uses the exact conditional structure: given the discordant count d = b + c,
    the conditional distribution of b is Binomial(d, theta) and theta is estimated
    by b/d. The interval is the Clopper-Pearson (exact binomial) interval for
    theta, mapped to the risk difference via
    ``RD = (2*theta - 1) * d / m``. That is the standard exact conditional
    construction for McNemar-style paired proportions and, unlike a bootstrap,
    has correct coverage at small m (arXiv 2605.30315; Bonett & Price 2012 for
    the asymptotically-smoother alternative reported there).

    This is the same construction ``mcnemar_exact`` now reports, via the shared
    ``_paired_rd_and_interval`` helper.
    """
    b, c, both, neither = contingency_table(t_values, x_values)
    return _paired_rd_and_interval(b, c, both, neither, confidence=confidence)


def _binom_cdf_bisect(k: int, n: int, target: float, *, upper: bool) -> float:
    """Solve for the binomial probability ``p`` in [0, 1] whose tail equals target.

    ``upper=True``  -> solve ``P(X >= k | n, p) = target`` (the CP LOWER bound)
    ``upper=False`` -> solve ``P(X <= k | n, p) = target`` (the CP UPPER bound)

    Both tails are monotone in ``p`` (P(X >= k) increases, P(X <= k) decreases), so
    bisection over the PROBABILITY interval [0, 1] is valid. An earlier version
    bisected over [0, n] and derived a count from the probability midpoint, which
    produced impossible bounds (e.g. 2.0 for n = 20); see F2-IMPL-AUTH-015.
    """
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if upper:
            val = 1.0 - binom_cdf(k - 1, n, mid)
            if val < target:
                lo = mid
            else:
                hi = mid
        else:
            val = binom_cdf(k, n, mid)
            if val > target:
                lo = mid
            else:
                hi = mid
    return (lo + hi) / 2.0


def _clopper_pearson_lower(k: int, n: int, alpha: float) -> float:
    if k == 0:
        return 0.0
    return _binom_cdf_bisect(k, n, alpha, upper=True)


def _clopper_pearson_upper(k: int, n: int, alpha: float) -> float:
    if k == n:
        return 1.0
    return _binom_cdf_bisect(k, n, alpha, upper=False)


def exact_power(b: int, c: int, *, alpha: float = 0.05, sided: Sided = "two-sided") -> float:
    """Exact power of the McNemar test at true discordant counts (b, c).

    H0 rejection threshold is derived at the nominal alpha; power is then the
    probability of exceeding it under the Binomial(n, b/n) alternative.

    When no rejection threshold exists (``t is None``) the alpha fallback is
    reported rather than 0.0. Under the two-sided doubling rule at alpha=0.05
    that happens for n < 6 discordant pairs, where even the most extreme split
    is not significant. The fallback is BELOW any planning target (0.80/0.90),
    so ``required_pairs`` can never mistake it for a crossing; it is a
    conservative floor for a test that in fact cannot reject.
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
                "asymptotic normal approximation (arXiv 2605.30315). That is a "
                "property of the TEST, not a guarantee about this return value: "
                "required_pairs returns the FIRST m whose rounded, conditional "
                "power clears the target, on a non-monotone curve. See the "
                "function docstring before quoting any returned N as a "
                "requirement."
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
    """Smallest m whose power reaches ``power`` for a target risk difference ``delta``.

    **What this returns — read before quoting it.** The answer is the FIRST ``m``
    for which power clears the target when power is evaluated at the ROUNDED
    cell ``n = round(m * discordance)``, ``b = round((n + m * delta) / 2)``,
    ``c = n - b``. Two consequences:

    * The power evaluated is **CONDITIONAL**: it fixes the discordant count at
      ``round(m * pi_d)`` and asks P(reject | exactly that many). It is not the
      unconditional power, which averages over ``Binomial(m, pi_d)``.
    * Achieved power is **NOT monotone in m**: ``n`` and ``b`` are rounded and
      the exact two-sided critical value is a step function of ``n``, so a
      larger ``m`` can have LOWER power than the returned one.

    The return value is therefore a *first crossing*, not a guaranteed lower
    bound — power can dip below ``power`` again at larger ``m``. For Experiment
    J's authorized parameters this does not change ``n`` (the frozen rule is
    ``n = max(300, ...)`` and every ``required_pairs`` result in the authorized
    pi_d range is below 300), but the value must not be quoted as "the number
    of pairs needed" without that qualification.

    ``discordance`` (pi_d) is an EMPIRICAL quantity this module cannot estimate:
    ``pi_d = P(T xor X)`` requires both arms plus their association, so a
    no-lesson X/C0 calibration (no T arm) cannot identify it
    (`F2-IMPL-AUTH-013`; readiness plan s2.4 correction, 2026-10-05).

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