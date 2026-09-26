"""The decision engine: exposure + policy + mechanisms -> one explicit plan.

Design rule, and the whole reason this is a separate package: **a plan never silently covers
nothing.** If no mechanism can execute, the plan says so and names why for each one. Silence is
reserved for "nothing to do", never for "could not do it".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .mechanisms import Mechanism


@dataclass(frozen=True)
class HedgeLeg:
    mechanism: str
    notional_usd: float
    cost_bps: float
    caps_upside: bool
    has_liquidation_risk: bool
    rationale: str

    def as_dict(self) -> dict:
        return {
            "mechanism": self.mechanism,
            "notional_usd": round(self.notional_usd, 2),
            "cost_bps": round(self.cost_bps, 4),
            "caps_upside": self.caps_upside,
            "has_liquidation_risk": self.has_liquidation_risk,
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
    burned_off: bool = False   # a trigger says hedged = 0 right now

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
               horizon_days: float | None = None, burned_off: bool = False) -> HedgePlan:
    """Choose the cheapest available mechanism for the policy's target coverage.

    `burned_off` short-circuits to a zero-hedge plan: a policy trigger has fired that says the
    correct hedge is none. That is a real decision, not an absence of one, and is recorded.
    """
    target_ratio = float(policy.get("target_ratio", 0.0))
    horizon = float(horizon_days if horizon_days is not None
                    else policy.get("horizon_days", 30))
    max_cost_bps = policy.get("max_cost_bps")
    triggers = [str(t) for t in policy.get("triggers", [])]
    notes: list[str] = []

    if burned_off:
        return HedgePlan(
            exposure_usd=exposure_usd, target_ratio=target_ratio, covered_usd=0.0,
            uncovered_usd=0.0, triggers=triggers, burned_off=True,
            notes=["a policy trigger fires: the correct hedge is currently zero"],
        )

    if exposure_usd <= 0:
        return HedgePlan(exposure_usd=exposure_usd, target_ratio=target_ratio,
                         covered_usd=0.0, uncovered_usd=0.0, triggers=triggers,
                         notes=["no exposure: nothing to hedge"])

    target_notional = exposure_usd * target_ratio

    candidates: list[tuple[Mechanism, float]] = []
    unavailable: list[str] = []
    for mech in mechanisms:
        avail = mech.availability()
        if not avail.ok:
            unavailable.append(f"{mech.name}: {avail.reason}")
            continue
        candidates.append((mech, mech.cost_bps(target_notional, horizon)))

    if not candidates:
        # The failure mode this whole package guards against.
        notes.append("NO MECHANISM AVAILABLE — exposure is unhedged")
        notes.extend(unavailable)
        return HedgePlan(
            exposure_usd=exposure_usd, target_ratio=target_ratio, covered_usd=0.0,
            uncovered_usd=exposure_usd, triggers=triggers, notes=notes,
        )

    # Cheapest first; policy order breaks ties (stable sort preserves it).
    candidates.sort(key=lambda pair: pair[1])
    chosen, chosen_cost = candidates[0]

    if max_cost_bps is not None and chosen_cost > float(max_cost_bps):
        notes.append(
            f"cheapest available mechanism costs {chosen_cost:.1f} bps, above the "
            f"policy ceiling of {float(max_cost_bps):.1f} bps — declining to hedge"
        )
        notes.extend(f"unavailable: {u}" for u in unavailable)
        return HedgePlan(
            exposure_usd=exposure_usd, target_ratio=target_ratio, covered_usd=0.0,
            uncovered_usd=exposure_usd, triggers=triggers, notes=notes,
        )

    rationale = (f"cheapest of {len(candidates)} available mechanism(s) at {chosen_cost:.1f} bps "
                 f"over {horizon:.0f}d")
    if unavailable:
        notes.extend(f"unavailable: {u}" for u in unavailable)

    leg = HedgeLeg(
        mechanism=chosen.name,
        notional_usd=target_notional,
        cost_bps=chosen_cost,
        caps_upside=chosen.caps_upside,
        has_liquidation_risk=chosen.has_liquidation_risk,
        rationale=rationale,
    )
    return HedgePlan(
        exposure_usd=exposure_usd, target_ratio=target_ratio,
        covered_usd=min(target_notional, exposure_usd),
        uncovered_usd=max(exposure_usd - target_notional, 0.0),
        legs=[leg], triggers=triggers, notes=notes,
    )
