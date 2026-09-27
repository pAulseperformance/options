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
LIGHTER = [  # (venue, label, host, account index)
    ("lighter-mainnet", "Lighter · mainnet", "https://mainnet.zklighter.elliot.ai", 281474976483780),
    ("lighter-rh", "Lighter · RH chain", "https://api.rh.lighter.xyz", 281474976709370),
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
        for sym in ("ETH", "BTC"):
            r = _get(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot")
            rates[sym] = float(r["data"]["amount"])
        return rates

    def binance() -> dict:
        rates = {}
        for sym, pair in (("ETH", "ETHUSDT"), ("BTC", "BTCUSDT")):
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

    try:
        accounts.append(core.normalize_l1(args.wallet, fetch_l1_balances(args.wallet), rates,
                                          list(ERC20)))
    except Exception as e:  # noqa: BLE001
        accounts.append(core.failed_account("ethereum-l1", "spot · Ethereum L1",
                                            f"{type(e).__name__}: {e}"))

    for venue, label, host, index in LIGHTER:
        try:
            raw = asyncio.run(_lighter_account(host, index))
            accounts.append(core.normalize_lighter(raw, index, host, venue, label, rates))
        except Exception as e:  # noqa: BLE001
            accounts.append(core.failed_account(venue, label, f"{type(e).__name__}: {e}"))

    accounts.append(core.placeholder_derive())

    artifact = core.build_artifact(accounts, prices, fetched_at)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n")

    for a in artifact["accounts"]:
        val = "—" if a.get("usd_total") is None else f"${a['usd_total']:,.2f}"
        mark = "✓" if a.get("ok") and a.get("read", True) else ("·" if not a.get("read", True) else "✗")
        print(f"  {a['label']:<24} {mark} {val:>14}"
              + (f"   [{a['error']}]" if not a.get("ok") else ""))
    t, x = artifact["totals"], artifact["exposure"]
    comp = ", ".join(f"{c['asset']} {c['net_amount']:+g} → ${c['usd']:,.2f}"
                     if c.get("usd") is not None else f"{c['asset']} ?"
                     for c in x["components"]) or "nothing"
    print(f"  total ${t['usd_total']:,.2f} · exposure ${x['usd']:,.2f} ({comp})")
    if not artifact["complete"]:
        print("  INCOMPLETE — at least one account failed; coverage_cli will refuse this artifact")
    print(f"wrote {out}")
    if args.json:
        print(json.dumps(artifact, indent=2))
    return 0 if artifact["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
