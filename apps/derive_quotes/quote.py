"""Pure quote logic for a Derive deployment: parse the wire, price the put, pick the leg.

No I/O in this module — every function is a function of its inputs, so the rules are tested
offline against a recorded scan (tests/fixtures/). The socket lives in `__main__`.

The unit, settled once: option prices arrive as USDC per unit of underlying, and insurance is
bought to protect a notional, so a quote is expressed as `premium_bps = price / spot * BPS` —
bps of the notional it protects. Identical unit to `options_core.costs`, deliberately: nothing
downstream needs to know which venue (or tenor) a number came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from options_core import costs

__all__ = ["QuoteRow", "best_levels", "parse_instrument", "rows_from_snapshots",
           "select_protective_put", "catalogue_instruments", "pick_deployment",
           "build_artifact"]

_NAME_RE = re.compile(r"^(?P<base>[A-Z0-9]+)-(?P<expiry>\d{8})-(?P<strike>\d+)-(?P<kind>[PC])$")


@dataclass(frozen=True)
class QuoteRow:
    """One option instrument's book, as observed at scan time."""
    instrument: str
    kind: str                 # "P" or "C"
    expiry: datetime          # UTC
    strike: float
    tenor_days: float         # expiry - observed_at, fractional days
    bid: float | None         # None when that side of the book is empty
    ask: float | None
    bid_size: float
    ask_size: float

    @property
    def two_sided(self) -> bool:
        return self.bid is not None and self.ask is not None

    def premium_bps(self, spot: float) -> float:
        """What buying protection costs, in bps of the notional the put protects.

        The ask is the executable side: a buyer pays it. Never the mid — a mid is an opinion,
        an ask is a price.
        """
        if self.ask is None:
            raise ValueError(f"{self.instrument}: no ask — nothing to buy")
        return costs.bps_of(self.ask, spot)

    def cost_per_day_bps(self, spot: float) -> float:
        return costs.cost_per_day_bps(self.premium_bps(spot), self.tenor_days)

    def premium_bps_mid(self, spot: float) -> float | None:
        """The mid, for reference only — never for a decision. None when a side is missing."""
        bid, ask = self.bid, self.ask
        if bid is None or ask is None:
            return None
        return costs.bps_of((bid + ask) / 2.0, spot)

    def as_dict(self, spot: float) -> dict:
        out = {
            "instrument": self.instrument,
            "kind": self.kind,
            "expiry": self.expiry.date().isoformat(),
            "tenor_days": round(self.tenor_days, 1),
            "strike": self.strike,
            "bid": self.bid, "bid_size": self.bid_size,
            "ask": self.ask, "ask_size": self.ask_size,
        }
        if self.ask is not None:
            out["premium_bps_ask"] = round(self.premium_bps(spot), 2)
        mid_bps = self.premium_bps_mid(spot)
        if mid_bps is not None:
            out["premium_bps_mid"] = round(mid_bps, 2)
        return out


def parse_instrument(name: str) -> tuple[str, datetime, float, str]:
    """'ETH-20270326-2400-P' -> ('ETH', datetime(2027-3-26, 8:00, UTC), 2400.0, 'P')."""
    m = _NAME_RE.match(name)
    if not m:
        raise ValueError(f"not a Derive option instrument name: {name!r}")
    expiry = datetime.strptime(m.group("expiry"), "%Y%m%d").replace(tzinfo=timezone.utc)
    return m.group("base"), expiry, float(m.group("strike")), m.group("kind")


def best_levels(book: dict) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Best (price, size) per side — derived from the prices, never taken from array order.

    A feed that accumulates levels it was never told to remove can serve a stale level first,
    so the top of the book is max(bids) / min(asks), not element [0]. (This exact bug cost the
    MM bot a week of fiction; the fix is to derive, not to trust position.)
    """
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    best_bid = max(((float(p), float(s)) for p, s in bids), default=None)
    best_ask = min(((float(p), float(s)) for p, s in asks), default=None)
    return best_bid, best_ask


def rows_from_snapshots(snapshots: dict, observed_at: datetime,
                        expiry_times: dict[str, datetime] | None = None) -> list[QuoteRow]:
    """Wire snapshots -> rows. `snapshots` maps instrument name -> {'bids': [[px, size]], ...}.

    Prices and sizes arrive as strings; either side may be empty. An instrument name we cannot
    parse is a wire surprise, not a candidate: raise, so it can never be silently misbucketed.

    `expiry_times` optionally supplies the venue's real expiry instants (from the instrument
    catalogue). Without it the expiry is read from the name, which lands on 00:00 UTC — the
    tenor is then understated by the time-of-day (conservative, but less exact).
    """
    expiry_times = expiry_times or {}
    rows: list[QuoteRow] = []
    for name in sorted(snapshots):
        _, name_expiry, strike, kind = parse_instrument(name)
        expiry = expiry_times.get(name, name_expiry)
        best_bid, best_ask = best_levels(snapshots[name] or {})
        rows.append(QuoteRow(
            instrument=name, kind=kind, expiry=expiry, strike=strike,
            tenor_days=(expiry - observed_at).total_seconds() / 86400.0,
            bid=best_bid[0] if best_bid else None,
            bid_size=best_bid[1] if best_bid else 0.0,
            ask=best_ask[0] if best_ask else None,
            ask_size=best_ask[1] if best_ask else 0.0,
        ))
    return rows


def select_protective_put(rows: list[QuoteRow], policy: dict, spot: float,
                          min_tradable_size: float = 0.0) -> dict | None:
    """The venue's representative protective put, per policy — or None when nothing qualifies.

    Qualify: a PUT, tenor >= policy's min_tenor_days (cover must outlast the position),
    two-sided, with an ask of at least `min_tradable_size` (the venue's own minimum). A
    one-sided stub or a dust quote is not a market and must not win the slot — the same rule as
    `options_core.mechanisms`, applied to measurements instead of placeholders.

    Then: the strike nearest the policy's target (`spot * (1 - strike_otm_pct/100)`) within each
    expiry, and across expiries the lowest cost per day of cover — the honest comparator between
    tenors. Ties break toward the earlier expiry.
    """
    min_tenor = float(policy.get("min_tenor_days", 0))
    otm_pct = float(policy.get("strike_otm_pct", 0.0) or 0.0)
    target = spot * (1.0 - otm_pct / 100.0)

    by_expiry: dict[datetime, list[QuoteRow]] = {}
    for r in rows:
        if r.kind != "P" or not r.two_sided:
            continue
        if r.tenor_days < min_tenor or r.ask_size < min_tradable_size:
            continue
        by_expiry.setdefault(r.expiry, []).append(r)

    best: tuple[float, QuoteRow] | None = None
    for expiry in sorted(by_expiry):
        exp_rows = by_expiry[expiry]
        exp_rows.sort(key=lambda r: (abs(r.strike - target), r.strike))
        row = exp_rows[0]
        cpd = row.cost_per_day_bps(spot)
        if best is None or cpd < best[0]:
            best = (cpd, row)

    if best is None:
        return None
    cpd, row = best
    return {
        "instrument": row.instrument,
        "strike": row.strike,
        "expiry": row.expiry.date().isoformat(),
        "tenor_days": round(row.tenor_days, 1),
        "premium_bps": round(row.premium_bps(spot), 2),
        "cost_per_day_bps": round(cpd, 4),
        "ask": row.ask,
        "ask_size": row.ask_size,
        "bid": row.bid,
        "bid_size": row.bid_size,
        "basis": "ask",
        "target_strike": round(target, 2),
        "strike_otm_pct": round(100.0 * (1.0 - row.strike / spot), 2),
    }


def catalogue_instruments(payload: dict) -> list[dict]:
    """Normalize a catalogue response across deployments: v2 returns a bare list under `result`;
    v3 wraps the same instrument objects in `result.instruments` (paginated). One shape downstream."""
    result = payload.get("result")
    if isinstance(result, list):
        return list(result)
    if isinstance(result, dict):
        return list(result.get("instruments") or [])
    return []


def pick_deployment(attempts: list[dict]) -> int | None:
    """Which deployment's measurement gets published, attempts given in preference order.

    First match wins along this ladder — a buyable put beats a mere market, a market beats a
    functional-but-empty venue (an empty board is still an answer: "unquoted", and it must be
    publishable when it is all a venue is showing):

      1. `selected` — a policy-qualifying put, something the plan could actually buy;
      2. `genuine`  — any two-sided quote of tradable size;
      3. `spot_ok`  — a spot was measured at all.

    None = nothing usable anywhere; the caller then fails loud with every attempt's reason.
    """
    for rank in ("selected", "genuine", "spot_ok"):
        for i, a in enumerate(attempts):
            if a.get(rank):
                return i
    return None


def build_artifact(*, venue: str, deployment: str, endpoint: str, asset: str,
                   fetched_at: datetime, spot: float, spot_source: str,
                   policy_inputs: dict, rows: list[QuoteRow], selected: dict | None,
                   notes: list[str]) -> dict:
    """The published quotes artifact: what the book showed, and what it costs. Data, not code."""
    return {
        "schema_version": 1,
        "produced_by": "options/derive_quotes",
        "read_only": True,
        "venue": venue,
        "deployment": deployment,
        "endpoint": endpoint,
        "asset": asset,
        "fetched_at": fetched_at.isoformat().replace("+00:00", "Z"),
        "spot": {"value": round(spot, 4), "source": spot_source},
        "policy_inputs": policy_inputs,
        "selected": selected,
        "ladder": [r.as_dict(spot) for r in rows],
        "notes": notes,
    }
