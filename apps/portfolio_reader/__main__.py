"""portfolio_reader — the I/O half: public RPC + public venue APIs -> data/portfolio.json.

    PYTHONPATH=apps uv run --with "lighter-sdk @ git+https://github.com/elliottech/lighter-python.git" \\
        --no-project python -m portfolio_reader

Read-only by construction: the only wallet interaction is `eth_getBalance`/`eth_call` on a
public node, the only venue interaction is Lighter's public account endpoint (via the official
SDK), and prices come from a public spot feed. No keys exist anywhere in this repo.

A source that fails degrades ONE account: the artifact still publishes, marked `complete:
false`, and the planner refuses it — an understated exposure must never be priced as if whole.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import portfolio as core

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_WALLET = "0xe83ECEe6ad078a64F641BdA924c841Fd0F7D58f9"
L1_RPC = "https://ethereum-rpc.publicnode.com"
ERC20 = {  # symbol -> (contract, decimals) — checked every run so "0" is an answer, not a gap
    "WETH": ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
    "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
    "WBTC": ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),
}
LIGHTER = [  # (venue, label, host, account index, role) — one row per account READ
    ('lighter-mainnet', 'Lighter · mainnet', 'https://mainnet.zklighter.elliot.ai', 281474976483780, 'book'),
    ('lighter-rh', 'Lighter · RH chain', 'https://api.rh.lighter.xyz', 281474976709370, 'book'),
    ('lighter-mainnet', 'Lighter · mainnet · main account', 'https://mainnet.zklighter.elliot.ai', 728660, 'book'),
    ('lighter-rh', 'Lighter · RH · main account', 'https://api.rh.lighter.xyz', 29257, 'book'),
    ('lighter-mainnet', 'Lighter · pool · Christ is King', 'https://mainnet.zklighter.elliot.ai', 281474976498676, 'pool'),
    # The two remaining accounts under this wallet. Both are near-empty and that is the point:
    # an account nobody can see is an account nobody checks, and "enumerate them all" means the
    # page shows every door into the venue, including the ones with nothing behind them yet.
    ('lighter-mainnet', 'Lighter · mainnet · sub 1 (empty)', 'https://mainnet.zklighter.elliot.ai', 281474976485868, 'book'),
    ('lighter-mainnet', 'Lighter · mainnet · sub 2 (idle)', 'https://mainnet.zklighter.elliot.ai', 281474976511844, 'book'),
]
UA = {"User-Agent": "Mozilla/5.0 (options/portfolio_reader; read-only)"}


def _post(url: str, payload: dict, timeout: float = 30.0):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={**UA, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _get(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def fetch_l1_balances(wallet: str) -> dict:
    """Native ETH + the checked token set, decimal-adjusted. A zero IS read and recorded."""
    balances: dict[str, float] = {}
    r = _post(L1_RPC, {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                       "params": [wallet, "latest"]})
    if "result" not in r:
        raise RuntimeError(f"eth_getBalance: {r.get('error') or r}")
    balances["ETH"] = int(r["result"], 16) / 10**18
    data = "0x70a08231" + wallet[2:].lower().rjust(64, "0")
    for sym, (contract, decimals) in ERC20.items():
        r = _post(L1_RPC, {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                           "params": [{"to": contract, "data": data}, "latest"]})
        if "result" not in r:
            raise RuntimeError(f"balanceOf {sym}: {r.get('error') or r}")
        balances[sym] = int(r["result"], 16) / 10**decimals
    return balances


def fetch_prices() -> dict:
    """Spot rates for the assets the portfolio values off market data (ETH, BTC).

    Two independent public sources: a wrong price would misvalue every report, so the reader
    refuses to publish (prices error handled upstream) rather than guess.
    """
    def coinbase() -> dict:
        rates = {}
        # UNI is here because an account holds it and the venue has no market for it: the only
        # price that exists for it is an exchange's. ETH and BTC are the assets the L1 wallet and
        # the books' margins are quoted in.
        for sym in ("ETH", "BTC", "UNI"):
            r = _get(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot")
            rates[sym] = float(r["data"]["amount"])
        return rates

    def binance() -> dict:
        rates = {}
        for sym, pair in (("ETH", "ETHUSDT"), ("BTC", "BTCUSDT"), ("UNI", "UNIUSDT")):
            r = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={pair}")
            rates[sym] = float(r["price"])
        return rates

    errors = []
    for name, fn in (("coinbase spot", coinbase), ("binance spot", binance)):
        try:
            return {"source": name, "fetched_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"), "rates": fn()}
        except Exception as e:  # noqa: BLE001 - try the next source, then give up loudly
            errors.append(f"{name}: {type(e).__name__}: {e}")
    raise RuntimeError("no price source reachable — " + " | ".join(errors))


RH_MARKS_URL = "https://api.rh.lighter.xyz/api/v1/orderBookDetails"


def fetch_rh_marks() -> dict:
    """Prices for the RH-chain venue's OWN markets, keyed the way the ACCOUNT names the asset.

    The RH venue lists `SPY` where an account's asset row says `rhSPY`, so the tokenised-RH prefix
    is stripped and both spellings are registered. A tokenised equity that no exchange quotes is
    still priced by the venue that makes its market — the book it prints is the only price there is.
    """
    d = _get(RH_MARKS_URL)
    out: dict = {}
    for group in ("spot_order_book_details", "order_book_details"):
        for m in d.get(group) or []:
            sym = str(m.get("symbol") or "").split("/")[0].strip().upper()
            px = m.get("mark_price") or m.get("last_trade_price")
            try:
                px = float(px)
            except (TypeError, ValueError):
                continue
            if sym and px > 0:
                out[sym] = px
                out[f"rh{sym}"] = px
    return out


async def _lighter_account(host: str, index: int) -> dict:
    import lighter  # lazy: the pure half and the tests run without the SDK installed

    client = lighter.ApiClient(configuration=lighter.Configuration(host=host))
    try:
        res = await lighter.AccountApi(client).account(by="index", value=str(index))
        d = res.to_dict()
        if not d.get("accounts"):
            raise RuntimeError(f"Lighter {host} returned no account for index {index}")
        return d["accounts"][0]
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="portfolio_reader", description=__doc__)
    ap.add_argument("--wallet", default=DEFAULT_WALLET,
                    help="the L1 wallet to read (default: the user's spot wallet)")
    ap.add_argument("--out", default=str(ROOT / "data" / "portfolio.json"))
    ap.add_argument("--json", action="store_true", help="print the artifact to stdout")
    args = ap.parse_args(argv)

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    accounts: list[dict] = []

    try:
        prices = fetch_prices()
        rates = prices["rates"]
    except Exception as e:  # noqa: BLE001 - degrade: holdings get listed, values marked unknown
        prices = {"source": None, "fetched_at": fetched_at, "rates": {},
                  "error": f"{type(e).__name__}: {e}"}
        rates = {}

    # The RH chain's own book joins the rates: a holding no exchange quotes — a tokenised equity
    # like rhSPY — is priced by the venue that makes its market. Soft-fail on purpose: a venue read
    # that breaks must not take the run down, and an unpriced holding is named by the reader
    # exactly as it was before.
    try:
        rates.update(fetch_rh_marks())
    except Exception as e:  # noqa: BLE001
        prices["rh_marks_error"] = f"{type(e).__name__}: {e}"

    try:
        accounts.append(core.normalize_l1(args.wallet, fetch_l1_balances(args.wallet), rates,
                                          list(ERC20)))
    except Exception as e:  # noqa: BLE001
        accounts.append(core.failed_account("ethereum-l1", "spot · Ethereum L1",
                                            f"{type(e).__name__}: {e}"))

    # The LIT staking pool's own economics, read once so a stake can be valued by what its shares
    # are BACKED by rather than by what went in. Soft-fail on purpose: a stake is a nice-to-have
    # and a pool read that fails must not take the portfolio down with it.
    market = None
    try:
        market = core.stake_market(
            asyncio.run(_lighter_account("https://mainnet.zklighter.elliot.ai",
                                         core.LIT_STAKING_POOL)))
    except Exception as e:  # noqa: BLE001
        print(f"  ! LIT staking pool read failed ({type(e).__name__}: {e}) — stakes are counted "
              f"at their principal only")

    for venue, label, host, index, role in LIGHTER:
        try:
            raw = asyncio.run(_lighter_account(host, index))
            accounts.append(core.normalize_lighter(raw, index, host, venue, label, rates,
                                                   role, market))
        except Exception as e:  # noqa: BLE001
            accounts.append(core.failed_account(venue, label, f"{type(e).__name__}: {e}"))

    accounts.append(core.placeholder_derive())

    artifact = core.build_artifact(accounts, prices, fetched_at, market)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # ATOMIC: the dashboard polls this file every 60s, so an in-place write is a window where a
    # poll reads half a JSON document and reports "positions not read" for the sake of a rewrite.
    # tmp + rename means a reader ever sees the old artifact or the new one, never a torn one.
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(artifact, indent=2) + "\n")
    os.replace(tmp, out)

    # The book's own memory. One row per DAY, the day's last read: the reader runs on demand, so a
    # row per run would be noise, and the question this answers — is the book growing? — is daily.
    # A failed day is recorded as such rather than skipped: a series that hides its gaps lies.
    hist = out.parent / "portfolio_history.jsonl"
    row = core.history_row(artifact)
    try:
        rows = []
        if hist.exists():
            for line in hist.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue          # a torn line is dropped, never allowed to kill the series
        rows = [r for r in rows if r.get("date") != row["date"]] + [row]
        rows.sort(key=lambda r: r.get("date") or "")
        tmp_h = hist.with_suffix(hist.suffix + ".tmp")
        tmp_h.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        os.replace(tmp_h, hist)
        print(f"  history {len(rows)} day(s) · {hist.name}")
    except OSError as e:  # noqa: BLE001
        print(f"  ! could not append history: {e}")

    for a in artifact["accounts"]:
        val = "—" if a.get("usd_total") is None else f"${a['usd_total']:,.2f}"
        mark = "✓" if a.get("ok") and a.get("read", True) else ("·" if not a.get("read", True) else "✗")
        pct = ((a.get("pool") or {}).get("operator_share_pct"))
        if a.get("ok") and pct is not None:
            val += f"  ({pct:g}% yours → ${core.owned_usd(a):,.2f})"
        print(f"  {a['label']:<38} {mark} {val:>14}"
              + (f"   [{a['error']}]" if not a.get("ok") else ""))
    t, x = artifact["totals"], artifact["exposure"]
    comp = ", ".join(f"{c['asset']} {c['net_amount']:+g} → ${c['usd']:,.2f}"
                     if c.get("usd") is not None else f"{c['asset']} ?"
                     for c in x["components"]) or "nothing"
    print(f"  total ${t['usd_total']:,.2f} · exposure ${x['usd']:,.2f} ({comp})")
    st = artifact.get("stake") or {}
    if st.get("staked_lit"):
        print(f"  LIT staked {st['staked_lit']:,.4f} → shares are backed by {st['value_lit']:,.4f} LIT "
              f"({st['accrued_lit']:+,.4f} accrued, ${st['accrued_usd']:,.2f}) · "
              f"${st['value_usd']:,.2f} · {st['share_of_pool_pct']:.4g}% of the pool · "
              f"venue says {st['apy_pct']:g}% APY vs {st['realised_30d_pct']:+g}% over the last "
              f"{len(st['daily_returns'])} reported days")
    if not artifact["complete"]:
        print("  INCOMPLETE — at least one account failed; coverage_cli will refuse this artifact")
    print(f"wrote {out}")
    if args.json:
        print(json.dumps(artifact, indent=2))
    return 0 if artifact["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
