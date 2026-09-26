"""hedge-core: the insurance leg as a decision, not an order.

Venue-agnostic on purpose. The mechanisms (perp short, long put) are implementations behind one
interface; the decision — how much to hedge, by which mechanism, at what cost — is the product.
"""
from .costs import (BPS, bps_of, implied_leverage_days, long_put_cost_bps,
                    perp_short_cost_bps)
from .decide import HedgeLeg, HedgePlan, plan_hedge
from .mechanisms import (Availability, LongPutMechanism, Mechanism,
                         PerpShortMechanism, build_mechanisms)

__all__ = [
    "BPS", "bps_of", "implied_leverage_days", "long_put_cost_bps", "perp_short_cost_bps",
    "HedgeLeg", "HedgePlan", "plan_hedge",
    "Availability", "LongPutMechanism", "Mechanism", "PerpShortMechanism", "build_mechanisms",
]
