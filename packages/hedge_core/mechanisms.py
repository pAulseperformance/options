"""Hedge mechanisms behind one interface.

The point of the protocol is that "hedge the book" has more than one implementation and the
right one changes with the market. A perp short pays funding and can be liquidated; a long put
pays premium once and cannot. Both are hedges. Only one is usually correct.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from . import costs


@dataclass(frozen=True)
class Availability:
    """Whether a mechanism can actually be used right now, and if not, why not.

    The reason is mandatory when unavailable. An empty reason is how "we cannot hedge"
    silently becomes "we are hedged".
    """
    ok: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.ok and not self.reason.strip():
            raise ValueError("an unavailable mechanism must state a reason")


@runtime_checkable
class Mechanism(Protocol):
    name: str
    caps_upside: bool
    has_liquidation_risk: bool

    def availability(self) -> Availability: ...
    def cost_bps(self, notional_usd: float, horizon_days: float) -> float: ...


class PerpShortMechanism:
    """Short perp against the held asset. Mechanism #1 — already live in the stack.

    Cost is funding carry (which can be income) plus round-trip execution. There is no premium,
    but it caps upside and carries liquidation risk, so it is not free even when funding is
    negative.
    """

    name = "perp_short"
    caps_upside = True
    has_liquidation_risk = True

    def __init__(self, funding_bps_per_day: float, entry_exit_bps: float = 0.0,
                 venue: str = "lighter", venue_live: bool = True) -> None:
        self.funding_bps_per_day = funding_bps_per_day
        self.entry_exit_bps = entry_exit_bps
        self.venue = venue
        self.venue_live = venue_live

    def availability(self) -> Availability:
        if not self.venue_live:
            return Availability(False, f"{self.venue} perp venue is not reachable")
        return Availability(True, "")

    def cost_bps(self, notional_usd: float, horizon_days: float) -> float:
        return costs.perp_short_cost_bps(
            self.funding_bps_per_day, horizon_days, self.entry_exit_bps
        )


class LongPutMechanism:
    """Buy a put for insurance. Mechanism #2 — GATED on a live venue.

    Cost is the premium, known at entry and capped forever. Keeps upside, no liquidation.
    `venue_live` defaults to False on purpose: as of 2026-09-26 no venue can execute a
    long-dated crypto put programmatically (Derive V3 production book is empty). Flip it in
    config when `ops/derive_book_probe.mjs` reports a non-empty book.
    """

    name = "long_put"
    caps_upside = False
    has_liquidation_risk = False

    def __init__(self, premium_bps: float, tenor_days: int = 270,
                 venue: str = "derive", venue_live: bool = False,
                 venue_note: str = "") -> None:
        self.premium_bps = premium_bps
        self.tenor_days = tenor_days
        self.venue = venue
        self.venue_live = venue_live
        self.venue_note = venue_note

    def availability(self) -> Availability:
        if not self.venue_live:
            reason = self.venue_note or f"{self.venue} has no executable book"
            return Availability(False, f"{self.venue}: {reason}")
        return Availability(True, "")

    def cost_bps(self, notional_usd: float, horizon_days: float) -> float:
        # Premium is paid once at entry and is the entire cost; the horizon does not change it.
        # Deliberately NOT scaled by horizon — that is the difference from funding carry.
        return self.premium_bps


def build_mechanisms(policy: dict) -> list[Mechanism]:
    """Instantiate every mechanism named in policy, in policy order (order breaks cost ties)."""
    mechs: list[Mechanism] = []
    for spec in policy.get("mechanisms", []):
        kind = spec.get("kind")
        if kind == "perp_short":
            mechs.append(PerpShortMechanism(
                funding_bps_per_day=float(spec.get("funding_bps_per_day", 0.0)),
                entry_exit_bps=float(spec.get("entry_exit_bps", 0.0)),
                venue=spec.get("venue", "lighter"),
                venue_live=bool(spec.get("venue_live", True)),
            ))
        elif kind == "long_put":
            mechs.append(LongPutMechanism(
                premium_bps=float(spec.get("premium_bps", 0.0)),
                tenor_days=int(spec.get("tenor_days", 270)),
                venue=spec.get("venue", "derive"),
                venue_live=bool(spec.get("venue_live", False)),
                venue_note=spec.get("venue_note", ""),
            ))
        else:
            raise ValueError(f"unknown mechanism kind: {kind!r}")
    return mechs
