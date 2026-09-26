"""Where the insurance can be bought.

A mechanism is a **venue that sells long puts**. Derive is the live candidate; Lighter options are
expected later. Venues are compared on one axis — premium in bps of the notional protected —
because the protection sold is the same and only the price and the availability differ.

Nothing here places an order. Availability is a gate and the gate is closed by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from . import costs


@dataclass(frozen=True)
class Availability:
    """Whether this venue can actually be used right now, and if not, why not.

    The reason is mandatory when unavailable. An empty reason is how "we cannot buy insurance"
    silently becomes "we are insured".
    """
    ok: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.ok and not self.reason.strip():
            raise ValueError("an unavailable venue must state a reason")


@runtime_checkable
class Mechanism(Protocol):
    @property
    def name(self) -> str: ...

    tenor_days: int

    def availability(self) -> Availability: ...
    def premium_bps_for(self, notional_usd: float) -> float: ...


@dataclass
class PutVenue:
    """A venue that sells a long-dated protective put.

    `live` defaults to False on purpose. As of 2026-09-26 no venue can execute a long-dated crypto
    put programmatically: Derive V3 mainnet is pre-launch with a verified-empty production
    orderbook, and Lighter options do not exist yet. A venue must be proven live before it can be
    selected, so the quiet failure — recommending insurance that cannot be bought — is impossible.
    """

    venue: str
    premium_bps: float
    tenor_days: int = 270
    live: bool = False
    note: str = ""

    @property
    def name(self) -> str:
        return self.venue

    def availability(self) -> Availability:
        if not self.live:
            reason = self.note or f"{self.venue} has no executable options market"
            return Availability(False, f"{self.venue}: {reason}")
        if self.premium_bps <= 0:
            # Live but unquoted is still unquotable. Refusing here stops a zero placeholder from
            # winning a cheapest-venue comparison it never earned.
            return Availability(
                False,
                f"{self.venue}: live but has no real quote (premium_bps={self.premium_bps})",
            )
        return Availability(True, "")

    def premium_bps_for(self, notional_usd: float) -> float:
        return self.premium_bps

    def premium_usd_for(self, notional_usd: float) -> float:
        return notional_usd * self.premium_bps / costs.BPS


def build_mechanisms(policy: dict) -> list[Mechanism]:
    """Instantiate every venue named in policy, in policy order (order breaks cost ties)."""
    venues: list[Mechanism] = []
    for spec in policy.get("venues", []):
        if spec.get("kind", "put_venue") != "put_venue":
            raise ValueError(f"unknown venue kind: {spec.get('kind')!r}")
        venues.append(PutVenue(
            venue=spec["venue"],
            premium_bps=float(spec.get("premium_bps", 0.0)),
            tenor_days=int(spec.get("tenor_days", 270)),
            live=bool(spec.get("live", False)),
            note=spec.get("note", ""),
        ))
    return venues
