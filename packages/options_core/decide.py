"""The decision: exposure + policy + venues -> one explicit recommendation.

Design rule, and the whole reason this is a separate package: **a plan never silently covers
nothing.** If no venue can sell the insurance, the plan says so and names the reason for each one.
Silence is reserved for "nothing to do", never for "could not do it".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import costs
from .mechanisms import Mechanism


@dataclass(frozen=True)
class HedgeLeg:
    venue: str
    instrument: str
    tenor_days: int
    notional_usd: float
    cost_bps: float
    rationale: str

    @property
    def cost_usd(self) -> float:
        return self.notional_usd * self.cost_bps / costs.BPS

    @property
    def cost_per_day_bps(self) -> float:
        return costs.cost_per_day_bps(self.cost_bps, self.tenor_days)

    def as_dict(self) -> dict:
        return {
            "venue": self.venue,
            "instrument": self.instrument,
            "tenor_days": self.tenor_days,
            "notional_usd": round(self.notional_usd, 2),
            "cost_bps": round(self.cost_bps, 4),
            "cost_usd": round(self.cost_usd, 2),
            "cost_per_day_bps": round(self.cost_per_day_bps, 4),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class HedgePlan:
    exposure_usd: float
    target_ratio: float
    covered_usd: float
    legs: list[HedgeLeg] = field(default_factory=list)
    uncovered_usd: float = 0.0
    triggers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    burned_off: bool = False   # a trigger says insured = 0 right now

    @property
    def is_covered(self) -> bool:
        return bool(self.legs)

    @property
    def coverage_pct(self) -> float:
        if not self.exposure_usd:
            return 0.0
        return 100.0 * min(self.covered_usd, self.exposure_usd) / self.exposure_usd

    def as_dict(self) -> dict:
        return {
            "exposure_usd": round(self.exposure_usd, 2),
            "target_ratio": self.target_ratio,
            "covered_usd": round(self.covered_usd, 2),
            "uncovered_usd": round(self.uncovered_usd, 2),
            "coverage_pct": round(self.coverage_pct, 2),
            "is_covered": self.is_covered,
            "burned_off": self.burned_off,
            "legs": [l.as_dict() for l in self.legs],
            "triggers": list(self.triggers),
            "notes": list(self.notes),
        }


def plan_hedge(exposure_usd: float, policy: dict, mechanisms: list[Mechanism],
               burned_off: bool = False) -> HedgePlan:
    """Choose the cheapest venue that can actually sell the required protection.

    `burned_off` short-circuits to a zero plan: a policy trigger has fired that says the correct
    hedge is none. That is a real decision, not an absence of one, and is recorded as such.
    """
    target_ratio = float(policy.get("target_ratio", 0.0))
    min_tenor = int(policy.get("min_tenor_days", 0))
    max_premium_bps = policy.get("max_premium_bps")
    triggers = [str(t) for t in policy.get("triggers", [])]
    notes: list[str] = []

    if burned_off:
        return HedgePlan(
            exposure_usd=exposure_usd, target_ratio=target_ratio, covered_usd=0.0,
            uncovered_usd=0.0, triggers=triggers, burned_off=True,
            notes=["a policy trigger fires: the correct amount of insurance is currently zero"],
        )

    if exposure_usd <= 0:
        return HedgePlan(exposure_usd=exposure_usd, target_ratio=target_ratio,
                         covered_usd=0.0, uncovered_usd=0.0, triggers=triggers,
                         notes=["no exposure: nothing to insure"])

    target_notional = exposure_usd * target_ratio

    candidates: list[tuple[Mechanism, float]] = []
    unavailable: list[str] = []
    for mech in mechanisms:
        avail = mech.availability()
        if not avail.ok:
            unavailable.append(avail.reason)
            continue
        if min_tenor and mech.tenor_days < min_tenor:
            unavailable.append(
                f"{mech.name}: tenor {mech.tenor_days}d is shorter than the policy's "
                f"{min_tenor}d minimum cover"
            )
            continue
        candidates.append((mech, mech.premium_bps_for(target_notional)))

    if not candidates:
        # The failure mode this whole package guards against.
        notes.append("NO VENUE AVAILABLE — position is uninsured")
        notes.extend(unavailable)
        return HedgePlan(
            exposure_usd=exposure_usd, target_ratio=target_ratio, covered_usd=0.0,
            uncovered_usd=exposure_usd, triggers=triggers, notes=notes,
        )

    # Cheapest premium first; policy order breaks ties (stable sort preserves it).
    candidates.sort(key=lambda pair: pair[1])
    chosen, chosen_cost = candidates[0]

    if max_premium_bps is not None and chosen_cost > float(max_premium_bps):
        notes.append(
            f"cheapest available insurance costs {costs.premium_as_pct(chosen_cost):.2f}% of "
            f"protected notional ({chosen_cost:.0f} bps), above the policy ceiling of "
            f"{costs.premium_as_pct(float(max_premium_bps)):.2f}% — declining to insure"
        )
        notes.extend(f"unavailable: {u}" for u in unavailable)
        return HedgePlan(
            exposure_usd=exposure_usd, target_ratio=target_ratio, covered_usd=0.0,
            uncovered_usd=exposure_usd, triggers=triggers, notes=notes,
        )

    rationale = (
        f"cheapest of {len(candidates)} venue(s) that can cover {min_tenor}d: "
        f"{costs.premium_as_pct(chosen_cost):.2f}% of protected notional over "
        f"{chosen.tenor_days}d"
    )
    if unavailable:
        notes.extend(f"unavailable: {u}" for u in unavailable)

    leg = HedgeLeg(
        venue=chosen.name,
        instrument="long_put",
        tenor_days=chosen.tenor_days,
        notional_usd=target_notional,
        cost_bps=chosen_cost,
        rationale=rationale,
    )
    return HedgePlan(
        exposure_usd=exposure_usd, target_ratio=target_ratio,
        covered_usd=min(target_notional, exposure_usd),
        uncovered_usd=max(exposure_usd - target_notional, 0.0),
        legs=[leg], triggers=triggers, notes=notes,
    )
