"""Simplified Australian resident individual income tax estimator.

Brackets below are FY2026-27 (the 16% bracket dropped to 15% from 1 July
2026) — https://www.superguide.com.au/super-booster/income-tax-rates-brackets
These are a single hardcoded snapshot, not derived from any config, since
they change shape (not just a scalar) roughly once a year; update the table
each financial year rather than trying to make it configurable.

Deliberately ignores: HECS/HELP repayments, the Medicare Levy Surcharge, the
Medicare levy low-income phase-in, and any offsets — all of those need
per-person data this app doesn't collect. This is a ballpark, not a payslip.
"""

from __future__ import annotations

MEDICARE_LEVY_RATE = 0.02

# (lower bound inclusive, upper bound exclusive, rate on that slice). Upper
# bound of None means "no ceiling" (the top bracket).
RESIDENT_TAX_BRACKETS_2026_27: tuple[tuple[float, float | None, float], ...] = (
    (0, 18_200, 0.0),
    (18_200, 45_000, 0.15),
    (45_000, 135_000, 0.30),
    (135_000, 190_000, 0.37),
    (190_000, None, 0.45),
)


def marginal_rate(taxable_income: float) -> float:
    """Combined rate (bracket + Medicare levy) applying to the next dollar earned."""
    bracket_rate = 0.0
    for lower, _upper, rate in RESIDENT_TAX_BRACKETS_2026_27:
        if taxable_income > lower:
            bracket_rate = rate
    return bracket_rate + MEDICARE_LEVY_RATE


def estimate_tax_aud(taxable_income: float) -> float:
    """Progressive income tax plus flat Medicare levy, in dollars.

    taxable_income should exclude employer super contributions — those
    aren't part of an individual's assessable income (the fund pays its own
    contributions tax on them instead).
    """
    if taxable_income <= 0:
        return 0.0

    tax = 0.0
    for lower, upper, rate in RESIDENT_TAX_BRACKETS_2026_27:
        if taxable_income <= lower:
            break
        slice_upper = taxable_income if upper is None else min(taxable_income, upper)
        tax += (slice_upper - lower) * rate

    return tax + taxable_income * MEDICARE_LEVY_RATE
