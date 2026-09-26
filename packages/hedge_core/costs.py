"""Cost accounting for hedge mechanisms — everything in basis points of notional.

One unit everywhere so mechanisms are comparable. A perp short and a long put are not the
same product, but their cost over a holding period IS comparable, and that comparison is the
entire reason this package exists.
"""
from __future__ import annotations

BPS = 10_000.0


def bps_of(usd: float, notional_usd: float) -> float:
    """USD cost expressed in bps of notional. Guarded: notional 0 is a caller bug, not 0 bps."""
    if notional_usd:
        return BPS * usd / notional_usd
    raise ValueError("notional_usd must be non-zero to express a cost in bps")


def perp_short_cost_bps(funding_bps_per_day: float, horizon_days: float,
                        entry_exit_bps: float = 0.0) -> float:
    """Funding carry over the horizon, plus round-trip execution.

    Funding is signed: positive means a short RECEIVES it (cost is negative, i.e. income).
    That is why a perp short can be cheaper than free — and why it is worth comparing at all.
    """
    return funding_bps_per_day * horizon_days + entry_exit_bps


def long_put_cost_bps(premium_usd: float, notional_usd: float,
                      exit_bps: float = 0.0) -> float:
    """Premium paid, expressed against the notional it protects.

    This is the whole cost: a long put's maximum loss is the premium. Unlike a perp short
    there is no liquidation and no margin call, which is why it is worth paying for when the
    horizon is long.
    """
    return bps_of(premium_usd, notional_usd) + exit_bps


def implied_leverage_days(cost_bps: float, horizon_days: float) -> float:
    """Cost per day, for comparing mechanisms with different natural horizons."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    return cost_bps / horizon_days
