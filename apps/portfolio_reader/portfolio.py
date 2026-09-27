"""The pure half of portfolio_reader: venue payloads in, one artifact out.

Everything here is a plain dict -> dict transformation so the arithmetic that matters — net
exposure, what counts as cash, what a failed account does to `complete` — is testable without
a network, a wallet, or a venue.

Rules this file enforces, each a line earned elsewhere:

- A value that cannot be computed honestly is an ERROR, not a zero. A nonzero holding with no
  price marks its account not-ok (`complete: false`) and the planner refuses the artifact,
  rather than pricing insurance against an understated position.
- The venue's own number wins where the venue provides one (`total_asset_value`), but when our
  component sum disagrees materially the note says so — a silent reconciliation would hide a
  fact worth seeing.
- Delivery of the numbers is a mint of its own: amounts keep their precision (ETH arrives as
  1.8011419346783952, not 1.8), USD rounds to cents.
"""
from __future__ import annotations

STABLES = {"USDC", "USDT", "USDG", "DAI", "PYUSD"}

# The artifact's own freshness rule: a dashboard shows STALE past this age. Positions move
# when a human moves them, so the window is a day — the same cadence as the coverage window.
STALE_AFTER_H = 24


def num(x, default=None):
    """Venue strings ('1106.603906329141') and numbers alike; `default` on junk or None."""
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _readable(account: dict) -> bool:
    return bool(account.get("read", True))


def readable_accounts(accounts: list[dict]) -> list[dict]:
    """Accounts that were actually read — the only ones that count toward `complete`."""
    return [a for a in accounts if _readable(a)]


def normalize_l1(wallet: str, balances: dict, rates: dict, tokens_checked: list[str]) -> dict:
    """Ethereum L1 spot -> an account. `balances` is already decimal-adjusted.

    Prices missing for a NONZERO holding mark the account not-ok: we cannot value what we
    hold, so the honest answer is 'unknown', not '$0'.
    """
    positions: list[dict] = []
    errors: list[str] = []
    for asset, amt in sorted(balances.items()):
        amt = num(amt, 0.0) or 0.0
        if not amt:
            continue
        price = 1.0 if asset in STABLES else rates.get(asset)
        if price is None:
            errors.append(f"no price for {asset} ({amt:g} held)")
            continue
        positions.append({
            "asset": asset, "kind": "cash" if asset in STABLES else "spot",
            "amount": round(amt, 12), "price_usd": price, "usd": round(amt * price, 2),
        })
    notes: list[str] = []
    zero_checked = [t for t in tokens_checked if not (num(balances.get(t), 0.0) or 0.0)]
    if zero_checked:
        notes.append(f"{', '.join(zero_checked)} checked — all zero")
    return {
        "venue": "ethereum-l1", "label": "spot · Ethereum L1", "read": True,
        "address": wallet,
        "ok": not errors, "error": "; ".join(errors) or None,
        "usd_total": (round(sum(p["usd"] for p in positions), 2) if not errors else None),
        "usd_total_source": "computed" if not errors else None,
        "positions": positions, "notes": notes,
    }


def normalize_lighter(raw: dict, index: int, host: str, venue: str, label: str,
                      rates: dict) -> dict:
    """One Lighter account payload -> an account.

    Asset balances become `cash` (stables) or `balance` (everything else); the venue's own
    marks value them where it gives one (position_value in the same payload), the public spot
    feed fills the rest. Positions become signed `perp` rows — sign is what makes the netting
    on the other side of this file possible.
    """
    positions: list[dict] = []
    errors: list[str] = []
    markets = raw.get("positions") or []

    # The venue's own marks, for balances it does not value directly.
    marks: dict[str, float] = {}
    for p in markets:
        amt = num(p.get("position"), 0.0) or 0.0
        val = num(p.get("position_value"))
        if amt and val is not None and p.get("symbol"):
            marks.setdefault(p["symbol"], abs(val) / abs(amt))

    for a in raw.get("assets") or []:
        sym = a.get("symbol")
        margin = num(a.get("margin_balance"), 0.0) or 0.0
        plain = num(a.get("balance"), 0.0) or 0.0
        amt = margin if margin else plain
        if not amt or not sym:
            continue
        price = 1.0 if sym in STABLES else (rates.get(sym) or marks.get(sym))
        if price is None:
            errors.append(f"no price for {sym} ({amt:g} held)")
            continue
        positions.append({
            "asset": sym, "kind": "cash" if sym in STABLES else "balance",
            "amount": round(amt, 12), "price_usd": price, "usd": round(amt * price, 2),
        })

    nonzero = 0
    for p in markets:
        amt = num(p.get("position"), 0.0) or 0.0
        if not amt:
            continue
        nonzero += 1
        sign = -1 if (num(p.get("sign"), 1) or 1) < 0 else 1
        val = num(p.get("position_value"))
        positions.append({
            "asset": p.get("symbol"), "kind": "perp",
            "side": "long" if sign > 0 else "short",
            "amount": round(sign * abs(amt), 10),
            "usd": None if val is None else round(abs(val), 2),
            "entry": num(p.get("avg_entry_price")),
            "unrealized_pnl": num(p.get("unrealized_pnl")),
            "liquidation_price": num(p.get("liquidation_price")) or None,
        })

    notes = [f"{nonzero} of {len(markets)} markets nonzero"] if markets else []
    if not errors:
        has_perps = any(p["kind"] == "perp" for p in positions)
        venue_total = num(raw.get("total_asset_value"))
        computed = round(sum(p["usd"] for p in positions if p.get("usd") is not None), 2)
        if venue_total is not None and not has_perps:
            # Balances-only account: our sum and the venue's number are the same kind of
            # quantity, so a material gap means something is unseen — say so.
            if abs(computed - venue_total) > max(5.0, abs(venue_total) * 0.02):
                notes.append(f"balances sum to ${computed:,.2f} vs venue total ${venue_total:,.2f} "
                             f"— showing the venue's number")
        elif venue_total is not None:
            # With open positions the two numbers are different quantities BY CONSTRUCTION:
            # the venue's total is collateral + unrealized PnL; position notional is what a
            # price move reaches, not account value. Explain, never "reconcile".
            notes.append("venue total = collateral + unrealized PnL; open position notional is "
                         "exposure, not value")
        usd_total = venue_total if venue_total is not None else computed
    else:
        usd_total = None
    return {
        "venue": venue, "label": label, "read": True,
        "account_index": index, "host": host, "l1_address": raw.get("l1_address"),
        "ok": not errors, "error": "; ".join(errors) or None,
        "usd_total": None if usd_total is None else round(usd_total, 2),
        "usd_total_source": ("venue" if num(raw.get("total_asset_value")) is not None else "computed")
                            if not errors else None,
        "collateral_usd": num(raw.get("collateral")),
        "available_usd": num(raw.get("available_balance")),
        "positions": positions, "notes": notes,
    }


def failed_account(venue: str, label: str, error: str) -> dict:
    """A source that could not be read says so and poisons `complete` — never silently $0."""
    return {
        "venue": venue, "label": label, "read": True, "ok": False, "error": error,
        "usd_total": None, "usd_total_source": None, "positions": [],
        "notes": ["unreadable — excluded from totals and exposure"],
    }


def placeholder_derive() -> dict:
    """The next venue, standing visibly empty: not read because there is nothing to read yet.

    `read: false` keeps it out of `complete` and out of the exposure net — it is a placeholder,
    not an account, and pretending either way would be the bug.
    """
    return {
        "venue": "derive", "label": "Derive v2 (Lyra)", "read": False, "ok": True, "error": None,
        "usd_total": None, "usd_total_source": None, "positions": [],
        "notes": ["not funded yet — no account to read; add a reader here when it is"],
    }


def derive_prices_from_positions(accounts: list[dict]) -> dict:
    """Asset -> USD price, taken from rows that carry both a value and a size (venue marks)."""
    out: dict[str, float] = {}
    for a in readable_accounts(accounts):
        for p in a.get("positions") or []:
            amt, usd = p.get("amount"), p.get("usd")
            if out.get(p.get("asset")) is None and amt and usd is not None and abs(amt) > 1e-12:
                out[p["asset"]] = abs(usd) / abs(amt)
    return out


def exposure_of(accounts: list[dict], rates: dict) -> dict:
    """Net crypto per asset across ALL readable accounts — the number the planner insures.

    Positive nets are what a protective put can pay against. A net of exactly zero (a long on
    one venue against a short on another) and a net below zero (already short) contribute $0
    and say why, so nothing silently disappears.
    """
    nets: dict[str, float] = {}
    contribs: dict[str, list[tuple[str, float]]] = {}
    notes: list[str] = []
    for a in accounts:
        if not _readable(a):
            continue
        if not a.get("ok"):
            notes.append(f"{a.get('label') or a.get('venue')} unreadable — excluded from exposure")
            continue
        for p in a.get("positions") or []:
            if p.get("kind") == "cash":
                continue
            asset = p.get("asset")
            if not asset:
                continue
            amt = num(p.get("amount"), 0.0) or 0.0
            nets[asset] = nets.get(asset, 0.0) + amt
            where = str(a.get("label") or a.get("venue") or "?")
            contribs.setdefault(asset, []).append((where, amt))

    marks = derive_prices_from_positions(accounts)
    components: list[dict] = []
    total = 0.0
    for asset, net in sorted(nets.items(), key=lambda kv: -abs(kv[1])):
        net = round(net, 10)
        price = rates.get(asset) or marks.get(asset)
        usd = None if price is None else round(net * price, 2)
        comp = {"asset": asset, "net_amount": net,
                "price_usd": None if price is None else round(price, 6), "usd": usd}
        if usd is None:
            comp["note"] = "no price — cannot value"
            notes.append(f"{asset}: no price source — exposure cannot include it")
        elif net < 0:
            comp["usd"] = usd
            comp["note"] = "net short — a protective put does not insure this side"
            notes.append(f"{asset} nets to {net:g} (short) — counted as 0")
        elif usd == 0 and contribs.get(asset):
            where = " and ".join(f"{amt:+g} on {label}" for label, amt in contribs[asset])
            comp["note"] = "offsetting pair — carries no exposure"
            notes.append(f"{asset}: {where} — net 0, excluded")
        else:
            total += usd
        components.append(comp)

    return {
        "usd": round(total, 2),
        "label": "net crypto (spot + perp), all readable accounts",
        "components": components,
        "notes": notes,
    }


def totals_of(accounts: list[dict]) -> dict:
    """What the portfolio is worth: every readable account, cash included.

    `crypto_usd` here is GROSS position value (both legs of an offsetting pair show up) —
    deliberately different from `exposure.usd`, and the reason the two fields exist apart.
    """
    cash = crypto = 0.0
    for a in readable_accounts(accounts):
        if not a.get("ok"):
            continue
        for p in a.get("positions") or []:
            usd = p.get("usd")
            if usd is None:
                continue
            if p.get("kind") == "cash":
                cash += usd
            else:
                crypto += usd
    total = sum(a["usd_total"] for a in readable_accounts(accounts)
                if a.get("ok") and a.get("usd_total") is not None)
    return {"usd_total": round(total, 2), "cash_usd": round(cash, 2),
            "crypto_usd": round(crypto, 2), "displayed_accounts": len(readable_accounts(accounts))}


def build_artifact(accounts: list[dict], prices: dict, fetched_at: str) -> dict:
    """Assemble the published artifact. `fetched_at` is passed in so this stays pure."""
    complete = all(a.get("ok") for a in readable_accounts(accounts))
    notes = [
        f"read-only: public RPC + public venue APIs; {sum(1 for a in accounts if not _readable(a))} "
        f"venue(s) present but not read",
        "exposure nets long and short rows per asset; the TWO numbers differ by design: "
        "totals = what it is worth, exposure = what a drop reaches",
    ]
    if not complete:
        notes.insert(0, "INCOMPLETE — at least one readable account failed; totals and exposure "
                        "UNDERSTATE reality. coverage_cli refuses this artifact by design.")
    return {
        "schema_version": 1,
        "produced_by": "options/portfolio_reader",
        "read_only": True,
        "fetched_at": fetched_at,
        "stale_after_hours": STALE_AFTER_H,
        "complete": complete,
        "prices": prices,
        "accounts": accounts,
        "totals": totals_of(accounts),
        "exposure": exposure_of(accounts, prices.get("rates") or {}),
        "notes": notes,
    }
