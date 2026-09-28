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

# Below this many units, an asset that no feed prices is DUST: it is shown with no value rather
# than failing the account and poisoning the artifact. Above it, the missing price is a real
# problem and the account fails on purpose.
DUST_UNITS = 0.001

# The LIT staking public pool: VENUE-WIDE, where staked LIT is held in custody. Not an operator's
# trading pool — money here is deposited into a strategy pool and earns (or loses) with it, and a
# stake is worth what its shares are backed by, not what went in.
LIT_STAKING_POOL = 281474976624800

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


def stake_market(pool_raw: dict | None) -> dict | None:
    """The LIT staking pool's own economics, from its own account payload.

    One share is backed by `pool LIT / total shares` — the venue's own numbers, so the rate cannot
    drift from the pool it describes. Verified against the venue's published series: its
    `daily_return` IS the share-price ratio minus one (a FRACTION, not a percent), so a day of
    +0.1255 is +12.55% — this pool swings that hard, which is why the advertised APY is shown
    next to what the last 30 reported days actually compounded to.

    Returns None instead of raising: a stake is a nice-to-have, and a pool read that fails must
    never take the portfolio down with it.
    """
    if not pool_raw:
        return None
    info = pool_raw.get("pool_info") or {}
    lit = next((num(a.get("balance"), 0.0) or 0.0
                for a in pool_raw.get("assets") or [] if a.get("symbol") == "LIT"), 0.0)
    total = num(info.get("total_shares"), 0.0) or 0.0
    if not lit or not total:
        return None
    daily = [num(p.get("daily_return"), 0.0) or 0.0
             for p in info.get("daily_returns") or [] if p.get("daily_return")]
    prices = [num(p.get("share_price"), 0.0) or 0.0
              for p in info.get("share_prices") or [] if p.get("share_price")]
    compounded = 1.0
    for r in daily[-30:]:
        compounded *= 1.0 + r
    return {
        "pool": pool_raw.get("account_index"),
        "pool_lit": round(lit, 8),
        "total_shares": total,
        "lit_per_share": lit / total,
        "share_price_usd": prices[-1] if prices else None,
        "apy_pct": num(info.get("annual_percentage_yield")),
        "sharpe": num(info.get("sharpe_ratio")),
        "daily_returns": [round(r, 8) for r in daily[-30:]],
        "daily_returns_days": len(daily),
        "realised_30d_pct": round((compounded - 1.0) * 100.0, 4),
    }


def pool_summary(raw: dict) -> dict | None:
    """A public pool's own block — name, fee, the operator's share, APY and Sharpe.

    The share fraction is the number that must never be lost: a pool's equity is partly OTHER
    people's capital, so counting all of it as the owner's worth overstates the book by exactly
    the depositors' slice. `operator_shares / total_shares` is that fraction.
    """
    info = raw.get("pool_info") or {}
    if not info:
        return None
    total = num(info.get("total_shares"), 0.0) or 0.0
    operator = num(info.get("operator_shares"), 0.0) or 0.0
    frac = (operator / total) if total else None
    prices = info.get("share_prices") or []
    return {
        "name": raw.get("name") or None,
        "description": (raw.get("description") or "").strip() or None,
        "operator_fee_pct": num(info.get("operator_fee")),
        "operator_share_pct": None if frac is None else round(frac * 100.0, 4),
        "total_shares": total or None,
        "operator_shares": operator or None,
        "apy_pct": num(info.get("annual_percentage_yield")),
        "sharpe": num(info.get("sharpe_ratio")),
        "share_price": num((prices[-1] or {}).get("share_price")) if prices else None,
    }


def normalize_lighter(raw: dict, index: int, host: str, venue: str, label: str,
                      rates: dict, role: str = "book",
                      stake_market: dict | None = None) -> dict:
    """One Lighter account payload -> an account.

    Asset balances become `cash` (stables) or `balance` (everything else); the venue's own
    marks value them where it gives one (position_value in the same payload), the public spot
    feed fills the rest. Positions become signed `perp` rows — sign is what makes the netting
    on the other side of this file possible.

    A PUBLIC POOL is an account too, but not all of it is the operator's: `usd_total` stays the
    venue's own pool equity while `owned_fraction` carries the share the operator actually holds,
    and `totals`/`exposure` scale by it. Showing the pool's full equity as the owner's worth is
    the one mistake this field exists to prevent.
    """
    positions: list[dict] = []
    errors: list[str] = []
    markets = raw.get("positions") or []
    locked_only: list[str] = []
    stake_notes: list[str] = []
    unpriced: list[str] = []

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
        if not amt:
            # A holding that only ever appears in the venue's locked_balance is NOT read here
            # (and would break the account on a missing price) — it is named instead, so the
            # gap is visible rather than silently absent.
            if sym and (num(a.get("locked_balance"), 0.0) or 0.0) > 0:
                locked_only.append(sym)
            continue
        price = 1.0 if sym in STABLES else (rates.get(sym) or marks.get(sym))
        if price is None:
            # A DUST holding of an asset no feed prices — rhSPY 0.0001 in an otherwise idle
            # account — must not fail the account and poison the whole artifact, and must not
            # vanish either: it becomes a ROW with no value, named, so the gap shows up in the
            # book beside the money instead of silently changing it. Anything larger still fails
            # the account: the gate exists to stop a real position being valued at nothing.
            if amt < DUST_UNITS:
                unpriced.append(f"{sym} {amt:g}")
                positions.append({"asset": sym, "kind": "balance", "amount": round(amt, 12),
                                  "price_usd": None, "usd": None})
                continue
            errors.append(f"no price for {sym} ({amt:g} held)")
            continue
        positions.append({
            "asset": sym, "kind": "cash" if sym in STABLES else "balance",
            "amount": round(amt, 12), "price_usd": price, "usd": round(amt * price, 2),
        })

    # Staked LIT is real value that nothing has ever shown, and it is not in this account either:
    # the staking POOL account's LIT holding and the venue's own staked total are the same number
    # (113,568,143), so a stake is custody the pool holds — and the venue's account total excludes
    # it, exactly the way it excludes a plain balance. Counted as its own `staked` row. If LIT has
    # no price on this account the stake is NAMED and skipped: an unpriceable row must never be
    # allowed to poison the artifact.
    for sh in raw.get("shares") or []:
        staked = num(sh.get("principal_amount"), 0.0) or 0.0
        if staked <= 0:
            continue
        price = rates.get("LIT") or marks.get("LIT")
        if price is None:
            stake_notes.append(f"{staked:,.4f} LIT staked in pool {sh.get('public_pool_index')} — "
                               f"no LIT price on this account, so the stake is NOT counted")
            continue
        shares = num(sh.get("shares_amount"), 0.0) or 0.0
        pool_index = sh.get("public_pool_index")
        # A stake is worth what its SHARES ARE BACKED BY, not what was paid in — the difference IS
        # the yield, and it has never appeared on any page. The pool's own LIT-per-share is the
        # only rate that cannot drift from the pool it came from, so it is used only for the pool
        # it belongs to; any other pool's stake falls back to its principal.
        backed = None
        if stake_market and pool_index == stake_market.get("pool") and shares:
            backed = shares * stake_market["lit_per_share"]
        value = backed if backed else staked
        row = {
            "asset": "LIT", "kind": "staked", "amount": round(staked, 12),
            "price_usd": price, "usd": round(value * price, 2),
            "principal_usd": round(staked * price, 2),
            "pool": pool_index,
        }
        if shares:
            row["shares"] = shares
            row["value_amount"] = round(value, 12)
        # The 3-day cooldown is the sell tell on this venue: a non-empty queue means part of the
        # stake is on its way out, and the date is when it becomes sellable.
        unlocks = raw.get("pending_unlocks") or []
        row["unlocking"] = len(unlocks)
        if unlocks:
            # pending_unlocks entries carry `amount` (LIT, falling back to `principal`) and an
            # `unlock_timestamp` in ms — the moment the 3-day cooldown ends and it is sellable.
            queued = sum(num(u.get("amount") if u.get("amount") is not None else u.get("principal"),
                             0.0) or 0.0 for u in unlocks)
            row["unlock_usd"] = round(queued * price, 2)
        positions.append(row)
        if backed and abs(backed - staked) > 1e-9:
            gain = backed - staked
            stake_notes.append(
                f"the {staked:,.4f} LIT staked is backed by {backed:,.4f} LIT of pool shares — "
                f"{gain:+,.4f} LIT ({gain / staked * 100:+.2f}%) of accrued yield, counted as value")

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

    notes = ([f"{nonzero} of {len(markets)} markets nonzero"] if markets else []) + stake_notes
    if unpriced:
        notes.append(f"{', '.join(unpriced)} held with no price in any feed — shown but NOT "
                     f"counted; this account's value is understated by whatever they are worth")
    pool = pool_summary(raw)
    owned = 1.0
    if pool is not None:
        pct = pool.get("operator_share_pct")
        if pct is None:
            notes.append("public pool with no share table — counted at 100% of its equity, which "
                         "OVERSTATES your capital if anyone else has deposited")
        else:
            owned = pct / 100.0
            notes.append(f"public pool · the operator holds {pct:g}% of shares — totals and "
                         f"exposure count {pct:g}% of this account; the rest is depositors'")
    if not errors:
        has_perps = any(p["kind"] == "perp" for p in positions)
        venue_total = num(raw.get("total_asset_value"))
        stable = sum(p["usd"] for p in positions
                     if p["kind"] == "cash" and p.get("usd") is not None)
        holdings = sum(p["usd"] for p in positions
                       if p["kind"] in ("balance", "staked") and p.get("usd") is not None)
        upnl = sum(num(p.get("unrealized_pnl"), 0.0) or 0.0
                   for p in positions if p["kind"] == "perp")
        # Measured on every account we read: `total_asset_value` = the STABLECOIN balance + perp
        # unrealized PnL, exactly. So comparing it against the same-scope sum is a check that CAN
        # fail — it ran only for balances-only accounts before, which is how a mis-read stable
        # figure could hide behind an open position.
        same_scope = stable + upnl
        if venue_total is not None and abs(same_scope - venue_total) > max(5.0, abs(venue_total) * 0.02):
            notes.append(f"balances sum to ${same_scope:,.2f} vs venue total ${venue_total:,.2f} "
                         f"— showing the venue's number")
        if venue_total is not None and has_perps:
            # The two numbers are different quantities BY CONSTRUCTION: the venue's total is the
            # collateral plus unrealized PnL; position notional is what a price move reaches.
            notes.append("venue total = collateral + unrealized PnL; open position notional is "
                         "exposure, not value")
        if venue_total is None:
            usd_total, source = stable + holdings + upnl, "computed"
        elif holdings > 0.005:
            # Anything NON-STABLE the account owns sits outside the venue's total: the LIT staking
            # pool reports collateral 0 while holding 113.5M LIT, and a real mainnet account holds
            # 41 ETH outside its reported $20.9k. Value is the venue's number PLUS those holdings;
            # publishing the venue's figure alone would hide six figures of a real position.
            usd_total, source = venue_total + holdings, "venue+holdings"
            notes.append(f"venue total ${venue_total:,.2f} covers the stablecoin balance and perp "
                         f"PnL only; this account also holds ${holdings:,.2f} of other assets — the "
                         f"value shown is the whole account")
        else:
            usd_total, source = venue_total, "venue"
        if locked_only:
            notes.append(f"{', '.join(locked_only)} appears only in the venue's locked_balance and "
                         f"is NOT counted — this account's value is understated")
    else:
        usd_total = source = venue_total = None
    return {
        "venue": venue, "label": label, "read": True,
        "role": "pool" if pool is not None else role,
        "account_index": index, "host": host, "l1_address": raw.get("l1_address"),
        "ok": not errors, "error": "; ".join(errors) or None,
        "usd_total": None if usd_total is None else round(usd_total, 2),
        "usd_total_source": source,
        # The venue's own number, kept visible: it is the stablecoin+uPnL figure, NOT the account.
        "venue_total_usd": None if venue_total is None else round(venue_total, 2),
        # 1.0 for an account that is entirely the owner's; a pool's operator share otherwise.
        "owned_fraction": owned,
        "pool": pool,
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


def owned_fraction(account: dict) -> float:
    """The share of an account that is the owner's own capital.

    1.0 for everything except a pool with outside depositors, where the operator's share of the
    pool's shares is the fraction of its equity (and of its positions) that is really his.
    """
    f = account.get("owned_fraction")
    if f is None:
        return 1.0
    try:
        f = float(f)
    except (TypeError, ValueError):
        return 1.0
    return min(max(f, 0.0), 1.0)


def owned_usd(account: dict) -> float | None:
    """What this account contributes to the owner's worth — `usd_total`, scaled by his share."""
    total = account.get("usd_total")
    return None if total is None else round(total * owned_fraction(account), 2)


def exposure_of(accounts: list[dict], rates: dict) -> dict:
    """Net crypto per asset across ALL readable accounts — the number the planner insures.

    Positive nets are what a protective put can pay against. A net of exactly zero (a long on
    one venue against a short on another) and a net below zero (already short) contribute $0
    and say why, so nothing silently disappears.

    A pool account's legs are counted at the operator's own share: a drop reaches the pool's
    full position, but only his fraction of that loss is his. The fraction is named in the notes
    rather than folded in silently.
    """
    nets: dict[str, float] = {}
    contribs: dict[str, list[tuple[str, float]]] = {}
    notes: list[str] = []
    scaled: list[str] = []
    for a in accounts:
        if not _readable(a):
            continue
        where = str(a.get("label") or a.get("venue") or "?")
        if not a.get("ok"):
            notes.append(f"{where} unreadable — excluded from exposure")
            continue
        f = owned_fraction(a)
        if f != 1.0:
            scaled.append(f"{where} {f * 100:g}%")
        for p in a.get("positions") or []:
            if p.get("kind") == "cash":
                continue
            asset = p.get("asset")
            if not asset:
                continue
            amt = (num(p.get("amount"), 0.0) or 0.0) * f
            nets[asset] = nets.get(asset, 0.0) + amt
            contribs.setdefault(asset, []).append(
                (where if f == 1.0 else f"{where} ({f * 100:g}% share)", amt))
    if scaled:
        notes.append("counted at the operator's share, not the account's full size — "
                     + ", ".join(scaled) + " · the depositors' part is not your exposure")

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

    `usd_total` is what is the OWNER'S. An account holding other people's capital (a public
    pool) contributes its operator share, and the difference is published separately as
    `delegated_usd` so the pool's real size is visible without being claimed as his worth.
    """
    cash = crypto = 0.0
    gross = owned = 0.0
    for a in readable_accounts(accounts):
        if not a.get("ok"):
            continue
        f = owned_fraction(a)
        for p in a.get("positions") or []:
            usd = p.get("usd")
            if usd is None:
                continue
            if p.get("kind") == "cash":
                cash += usd * f
            else:
                crypto += usd * f
        if a.get("usd_total") is not None:
            gross += a["usd_total"]
            owned += a["usd_total"] * f
    out = {"usd_total": round(owned, 2), "cash_usd": round(cash, 2),
           "crypto_usd": round(crypto, 2),
           "displayed_accounts": len(readable_accounts(accounts))}
    if gross - owned > 0.005:
        out["usd_total_gross"] = round(gross, 2)
        out["delegated_usd"] = round(gross - owned, 2)
    return out


def stake_totals(accounts: list[dict], market: dict | None) -> dict | None:
    """The whole staking position, across accounts, priced from the pool's own share rate."""
    rows = [p for a in readable_accounts(accounts) for p in (a.get("positions") or [])
            if p.get("kind") == "staked"]
    if not rows and not market:
        return None
    staked = sum(p.get("amount") or 0.0 for p in rows)
    value_lit = sum((p.get("value_amount") if p.get("value_amount") is not None
                     else p.get("amount")) or 0.0 for p in rows)
    usd = sum(p.get("usd") or 0.0 for p in rows)
    principal_usd = sum(p.get("principal_usd") if p.get("principal_usd") is not None
                        else (p.get("usd") or 0.0) for p in rows)
    shares = sum(p.get("shares") or 0.0 for p in rows)
    out = dict(market or {})
    out.update({
        "staked_lit": round(staked, 8),
        "value_lit": round(value_lit, 8),
        "accrued_lit": round(value_lit - staked, 8),
        "value_usd": round(usd, 2),
        "principal_usd": round(principal_usd, 2),
        "accrued_usd": round(usd - principal_usd, 2),
        "shares": shares,
    })
    total = out.get("total_shares")
    out["share_of_pool_pct"] = (shares / total * 100.0) if (shares and total) else None
    return out


def history_row(artifact: dict) -> dict:
    """One day's line for the book's own history — the numbers only a memory can show.

    Pure, so it can be tested without a filesystem, and complete: a day read while an account was
    failing is recorded with `complete: false` rather than skipped, because a series that hides its
    own gaps is worse than no series.
    """
    t = artifact.get("totals") or {}
    x = artifact.get("exposure") or {}
    stake = artifact.get("stake") or {}
    return {
        "date": (artifact.get("fetched_at") or "")[:10],
        "fetched_at": artifact.get("fetched_at"),
        "usd_total": t.get("usd_total"),
        "usd_total_gross": t.get("usd_total_gross"),
        "delegated_usd": t.get("delegated_usd"),
        "exposure_usd": x.get("usd"),
        "cash_usd": t.get("cash_usd"),
        "accounts": t.get("displayed_accounts"),
        "complete": artifact.get("complete"),
        "staked_lit": stake.get("staked_lit"),
        "stake_accrued_lit": stake.get("accrued_lit"),
        "by_account": {a.get("label") or a.get("venue"):
                       round((a.get("usd_total") or 0.0) * (a.get("owned_fraction") or 1.0), 2)
                       for a in artifact.get("accounts") or [] if a.get("read") is not False},
    }


def build_artifact(accounts: list[dict], prices: dict, fetched_at: str,
                   stake_market: dict | None = None) -> dict:
    """Assemble the published artifact. `fetched_at` is passed in so this stays pure."""
    complete = all(a.get("ok") for a in readable_accounts(accounts))
    totals = totals_of(accounts)
    notes = [
        f"read-only: public RPC + public venue APIs; {sum(1 for a in accounts if not _readable(a))} "
        f"venue(s) present but not read",
        "exposure nets long and short rows per asset; the TWO numbers differ by design: "
        "totals = what it is worth, exposure = what a drop reaches",
    ]
    pools = [a for a in readable_accounts(accounts) if a.get("role") == "pool"]
    if pools:
        named = "; ".join(
            f"{(a.get('pool') or {}).get('name') or a.get('label')} — "
            f"{a.get('usd_total'):,.2f} pooled, "
            + (f"{a['pool']['operator_share_pct']:g}% yours"
               if (a.get("pool") or {}).get("operator_share_pct") is not None else "share unknown")
            for a in pools)
        notes.append(f"pools are partly other people's capital: {named}"
                     + (f" · ${totals['delegated_usd']:,.2f} of the gross total is depositors'"
                        if totals.get("delegated_usd") else ""))
    stake = stake_totals(accounts, stake_market)
    if stake and stake.get("staked_lit"):
        line = (f"LIT staking: {stake['staked_lit']:,.4f} LIT deposited, shares worth "
                f"{stake['value_lit']:,.4f} LIT")
        if stake.get("accrued_lit"):
            line += (f" — {stake['accrued_lit']:+,.4f} LIT of accrued yield "
                     f"(${stake['accrued_usd']:,.2f}), counted as value")
        notes.append(line)
        if stake.get("apy_pct") is not None:
            notes.append(
                f"the LIT staking pool reports {stake['apy_pct']:g}% APY (the venue's own pool-wide "
                f"figure) while its last {len(stake.get('daily_returns') or [])} reported days "
                f"compounded to {stake.get('realised_30d_pct'):+g}% — the headline and the path "
                f"are different things, and this pool has printed both +12% and -11% days")
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
        "totals": totals,
        "exposure": exposure_of(accounts, prices.get("rates") or {}),
        "stake": stake,
        "notes": notes,
    }
