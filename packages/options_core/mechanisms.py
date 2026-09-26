"""Where the insurance can be bought.

A mechanism is a **venue that sells long puts**. Derive is the live candidate; Lighter options are
expected later. Venues are compared on one axis — premium in bps of the notional protected —
because the protection sold is the same and only the price and the availability differ.

Nothing here places an order. Availability is a gate and the gate is closed by default.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
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


def overlay_quotes(venues: list[Mechanism], quotes: dict, *, source: str,
                   max_age_hours: float | None,
                   now: datetime) -> tuple[list[Mechanism], list[str], dict | None]:
    """Replace a venue's placeholder premium with a measured one — when the measurement is fresh.

    The quotes artifact (see apps/derive_quotes) is a measurement with a timestamp; a plan may
    use it only while it is young enough to still describe the market. Stale, unreadable, or
    missing quotes leave the venue exactly as policy wrote it — a placeholder can never stand in
    for a measurement.

    Returns (venues, notes, meta). Notes go onto the plan (they explain every outcome, including
    "we had quotes and chose not to use them"); meta is the provenance block for the coverage
    artifact, or None when the quotes file matched no venue in policy at all.
    """
    venue_name = str(quotes.get("venue", "?"))
    out = list(venues)
    notes: list[str] = []

    idx = next((i for i, v in enumerate(out) if v.name == venue_name), None)
    if idx is None:
        return out, [f"quotes for {venue_name!r} match no venue in policy — ignored"], None

    meta: dict = {
        "source": source,
        "venue": venue_name,
        "fetched_at": quotes.get("fetched_at"),
        "age_hours": None,
        "applied": False,
        "reason": None,
    }

    fetched_at = _parse_iso(quotes.get("fetched_at"))
    if fetched_at is None:
        meta["reason"] = "quotes file has no readable fetched_at"
        notes.append(f"{venue_name}: quotes ignored — {meta['reason']}")
        return out, notes, meta

    age_hours = (now - fetched_at).total_seconds() / 3600.0
    meta["age_hours"] = round(age_hours, 2)
    if max_age_hours is not None and age_hours > float(max_age_hours):
        meta["reason"] = f"stale: {age_hours:.1f}h old, over the {max_age_hours}h policy limit"
        notes.append(f"{venue_name}: live quotes ignored — {meta['reason']}")
        return out, notes, meta

    selected = quotes.get("selected")
    if not selected:
        meta["reason"] = "no qualifying two-sided quote in the last measurement"
        notes.append(f"{venue_name}: the last quote run found no qualifying long-dated put")
        return out, notes, meta

    venue = out[idx]
    if not isinstance(venue, PutVenue):  # only the put venue carries an overlayable premium
        meta["reason"] = f"{venue_name}: venue carries no quotable premium"
        notes.append(f"{venue_name}: quotes ignored — {meta['reason']}")
        return out, notes, meta

    out[idx] = replace(
        venue,
        premium_bps=float(selected["premium_bps"]),
        tenor_days=int(round(float(selected["tenor_days"]))),
        note=(f"priced from the live book: {selected['instrument']} ask "
              f"{selected.get('ask')} @ {quotes.get('fetched_at')}"),
    )
    meta.update({
        "applied": True,
        "instrument": selected.get("instrument"),
        "premium_bps": selected.get("premium_bps"),
        "cost_per_day_bps": selected.get("cost_per_day_bps"),
        "tenor_days": selected.get("tenor_days"),
    })
    notes.append(
        f"priced from the live book: {selected.get('instrument')} ask {selected.get('ask')} — "
        f"{selected.get('premium_bps')} bps of protected notional @ {quotes.get('fetched_at')}"
    )
    return out, notes, meta


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
