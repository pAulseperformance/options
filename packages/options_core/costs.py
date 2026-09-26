"""Cost accounting — everything in basis points of notional.

One unit everywhere so venues are comparable. The same put on two venues is the same protection
at two prices; expressing both in bps of the notional they protect is what makes the comparison
meaningful, and that comparison is the entire reason this package exists.
"""
from __future__ import annotations

BPS = 10_000.0


def bps_of(usd: float, notional_usd: float) -> float:
    """USD cost expressed in bps of notional. Guarded: notional 0 is a caller bug, not 0 bps."""
    if notional_usd:
        return BPS * usd / notional_usd
    raise ValueError("notional_usd must be non-zero to express a cost in bps")


def put_premium_bps(premium_usd: float, notional_usd: float) -> float:
    """Premium paid, expressed against the notional it protects.

    This is the whole cost of the insurance: a long put's maximum loss is the premium. It does
    not scale with holding period — you pay once at entry and the position is then protected for
    the option's whole life, which is why a long-dated put suits a swing position and a rolling
    short-dated one does not.
    """
    return bps_of(premium_usd, notional_usd)


def cost_per_day_bps(total_bps: float, tenor_days: float) -> float:
    """Amortise a premium over its tenor.

    The honest way to compare a cheap 30-day put against an expensive 363-day one: the long-dated
    put costs more up front but covers a far longer window, and per day of cover it is often the
    cheaper of the two.
    """
    if tenor_days <= 0:
        raise ValueError("tenor_days must be positive")
    return total_bps / tenor_days


def premium_as_pct(total_bps: float) -> float:
    """bps -> percent, for display. 800 bps is 8% of the protected notional."""
    return total_bps / 100.0
