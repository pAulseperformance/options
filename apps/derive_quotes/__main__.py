"""derive_quotes — read the live Derive book and record what the protective put actually costs.

Read-only: a public websocket, no account, no keys, nothing signed. Publishes data/quotes.json,
the measurement that lets coverage_cli replace its placeholder premiums with real ones.

One venue, two deployments. `auto` (the default) tries v3 first — the zkVM-on-L1 deployment that
supersedes v2 at the migration — and falls back to v2 (live today) while v3 has nothing to sell.
A deployment that quotes wins over one that does not; a buyable put wins over a mere market.

Run:
    PYTHONPATH=apps uv run --with websockets --with pyyaml --no-project python -m derive_quotes
    # dry-run the staged deployment against populated testnet:
    ... python -m derive_quotes --deployment v3 \
        --endpoint wss://testnet.api.derive.xyz/v3/ws --rest https://testnet.api.derive.xyz/v3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))  # apps may import packages (rule 1)

from .quote import (  # noqa: E402
    best_levels, build_artifact, catalogue_instruments, pick_deployment,
    rows_from_snapshots, select_protective_put,
)

DEPLOYMENTS = {
    # Venue knowledge lives beside the venue's code, not in policy: an endpoint is not a decision.
    # derive v2 (api.lyra.finance) is the live production deployment on Derive Chain. derive v3
    # (api.derive.xyz/v3) is the zkVM-on-L1 successor — staged since 2026-09-28; its books stay
    # empty until the migration fills them. Auto order: v3 first (the going concern), v2 as the
    # fallback while it still quotes. The WS surface is source-compatible across both (verified
    # 2026-09-29: same channel names, same snapshot frames — see ops/derive_book_probe.mjs).
    "v3": {
        "venue": "derive",
        "ws": "wss://api.derive.xyz/v3/ws",
        "rest": "https://api.derive.xyz/v3",
        "deployment": "v3 · zkVM on Ethereum L1",
        "catalogue": "get_all_instruments",   # renamed on v3; result carries {instruments, pagination}
        "paginate": True,
    },
    "v2": {
        "venue": "derive",
        "ws": "wss://api.lyra.finance/ws",
        "rest": "https://api.lyra.finance",
        "deployment": "v2 · Derive Chain (production)",
        "catalogue": "get_instruments",
    },
}
AUTO_ORDER = ("v3", "v2")
BOOK_CHANNEL = "orderbook.{instrument}.1.10"   # group 1, depth 10 — the venue's public book params
STRIKE_WINDOW = (0.50, 1.20)                   # subscribe band around spot; policy narrows the strike
SPOT_WAIT_S = 10.0
DEFAULT_TIMEOUT_S = 25.0


def load_policy(path: Path) -> dict:
    """Same contract as coverage_cli's loader — duplicated on purpose (rule 2: apps never import each other)."""
    text = path.read_text()
    if path.suffix in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment guard
            raise SystemExit(
                "PyYAML is required to read a YAML policy. Install it, or point --policy at a "
                "JSON file with the same keys."
            ) from exc
        return yaml.safe_load(text)
    return json.loads(text)


def rest_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "options/derive_quotes (read-only)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def fetch_catalogue(cfg: dict, asset: str) -> list[dict]:
    """Active-option catalogue for one deployment. v3 paginates (100/page by default; we ask for
    1000 and loop any remaining pages so a growing board can't silently truncate the grid)."""
    url = (f"{cfg['rest']}/public/{cfg['catalogue']}"
           f"?instrument_type=option&currency={asset}&expired=false")
    if cfg.get("paginate"):
        url += "&page_size=1000"
    payload = rest_get(url)
    instruments = catalogue_instruments(payload)
    if cfg.get("paginate"):
        pages = int(((payload.get("result") or {}).get("pagination") or {}).get("num_pages") or 1)
        for page in range(2, pages + 1):
            instruments += catalogue_instruments(rest_get(url + f"&page={page}"))
    return instruments


def _mid(book: dict) -> float | None:
    best_bid, best_ask = best_levels(book)
    if best_bid is None or best_ask is None:
        return None
    return (best_bid[0] + best_ask[0]) / 2.0


def _subscribe(ws, channels: list[str], sub_id: int) -> None:
    ws.send(json.dumps({"id": sub_id, "method": "subscribe", "params": {"channels": channels}}))


def _pump(ws, books: dict, deadline: float, want: int, accept: set[str] | None = None) -> None:
    """Read frames into `books` until every wanted instrument arrived, or the deadline passes.

    `accept` bounds which instruments can enter `books` — a live feed keeps sending updates for
    instruments subscribed in an earlier phase, and those must not leak into (or complete) the
    current collection.
    """
    while time.monotonic() < deadline and len(books) < want:
        try:
            raw = ws.recv(timeout=max(0.05, deadline - time.monotonic()))
        except TimeoutError:
            return
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if "id" in msg and isinstance(msg.get("result"), dict):
            status = msg["result"].get("status") or {}
            bad = {c: s for c, s in status.items() if s != "ok"}
            if bad:
                print(f"  subscribe issues: {json.dumps(bad)[:300]}", file=sys.stderr)
            continue
        if msg.get("method") != "subscription":
            continue
        params = msg.get("params") or {}
        ch = params.get("channel") or ""
        if not ch.startswith("orderbook."):
            continue
        inst = ch.split(".")[1]
        if accept is not None and inst not in accept:
            continue
        books.setdefault(inst, params.get("data") or {})


def measure(key: str, cfg: dict, policy: dict, asset: str, timeout: float,
            ws_url: str, rest: str, now: datetime) -> dict:
    """One deployment's full read: catalogue -> perp spot -> long-dated grid -> rows -> selection.

    Never raises for venue states — a deployment that is up but empty is a result, not an error:
    `reason` says why it has no measurement, and the caller decides which attempt to publish.
    """
    out = {
        "key": key, "deployment": cfg["deployment"], "ws_url": ws_url,
        "spot": None, "spot_source": None, "rows": [], "selected": None,
        "grid": 0, "reported": 0, "observed_at": now, "reason": None,
        "spot_ok": False, "genuine": 0,
    }
    min_tenor = float(policy.get("min_tenor_days", 0) or 0)

    try:
        catalogue = fetch_catalogue(dict(cfg, rest=rest), asset)
    except Exception as exc:
        out["reason"] = f"catalogue fetch failed: {exc}"
        return out

    lo, hi = STRIKE_WINDOW
    grid: list[dict] = []
    expiry_times: dict = {}
    option_books: dict = {}
    observed_at = now
    try:
        from websockets.sync.client import connect

        print(f"[{key}] connecting {ws_url} (public, read-only)…")
        with connect(ws_url, open_timeout=15, close_timeout=5) as ws:
            # Phase 1 — the spot premiums are measured against: the perp's own mid.
            perp = f"{asset}-PERP"
            _subscribe(ws, [BOOK_CHANNEL.format(instrument=perp)], 1)
            perp_books: dict = {}
            _pump(ws, perp_books, time.monotonic() + SPOT_WAIT_S, want=1, accept={perp})
            spot = _mid(perp_books.get(perp) or {})
            if spot is None:
                out["reason"] = f"no {perp} book — venue staged or not trading"
                return out

            # Phase 2 — the long-dated put grid: policy's tenor floor, active instruments only.
            for inst in catalogue:
                name = str(inst.get("instrument_name", ""))
                if not inst.get("is_active") or not name.endswith("-P"):
                    continue
                od = inst.get("option_details") or {}
                exp_ts, strike = od.get("expiry"), od.get("strike")
                if exp_ts is None or strike is None:
                    continue
                if (exp_ts - now.timestamp()) / 86400.0 < min_tenor:
                    continue
                if not (spot * lo <= float(strike) <= spot * hi):
                    continue
                grid.append(inst)
            grid.sort(key=lambda i: i["instrument_name"])
            for inst in grid:
                ts = (inst.get("option_details") or {}).get("expiry")
                if ts is not None:
                    expiry_times[inst["instrument_name"]] = datetime.fromtimestamp(
                        ts, tz=timezone.utc)

            if grid:
                names = {i["instrument_name"] for i in grid}
                _subscribe(ws, [BOOK_CHANNEL.format(instrument=n) for n in sorted(names)], 2)
                _pump(ws, option_books, time.monotonic() + timeout,
                      want=len(names), accept=names)
            observed_at = datetime.now(timezone.utc)
    except Exception as exc:
        out["reason"] = f"websocket session failed: {exc}"
        return out

    rows = rows_from_snapshots(option_books, observed_at, expiry_times)
    min_amount = min((float(i.get("minimum_amount") or 0.0) for i in grid), default=0.0)
    selected = select_protective_put(rows, policy, spot, min_tradable_size=min_amount)
    genuine = [r for r in rows if r.two_sided and r.ask_size >= max(min_amount, 0.0)]
    out.update(
        spot=spot, spot_source=f"{asset}-PERP mid", rows=rows, selected=selected,
        grid=len(grid), reported=len(option_books), observed_at=observed_at,
        spot_ok=True, genuine=len(genuine),
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="derive_quotes", description=__doc__)
    ap.add_argument("--venue", default="derive", help="venue name (only 'derive' exists)")
    ap.add_argument("--deployment", default="auto", choices=["auto", *DEPLOYMENTS],
                    help="which deployment to read; auto = v3 first, v2 fallback")
    ap.add_argument("--policy", default=str(ROOT / "config" / "policy.yml"))
    ap.add_argument("--asset", default=None, help="underlying (default: the policy's asset)")
    ap.add_argument("--out", default=str(ROOT / "data" / "quotes.json"))
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help="seconds to wait for the option book snapshots (per deployment)")
    ap.add_argument("--endpoint", default=None, help="override the deployment's websocket URL")
    ap.add_argument("--rest", default=None, help="override the deployment's REST base")
    args = ap.parse_args(argv)

    if args.venue != "derive":
        print(f"unknown venue {args.venue!r}; known: ['derive']", file=sys.stderr)
        return 2

    policy = load_policy(Path(args.policy))
    asset = args.asset or policy.get("asset", "ETH")
    min_tenor = float(policy.get("min_tenor_days", 0) or 0)
    now = datetime.now(timezone.utc)

    try:
        from websockets.sync.client import connect  # noqa: F401
    except ImportError:
        print("the websockets package is required — run with: PYTHONPATH=apps uv run "
              "--with websockets --with pyyaml --no-project python -m derive_quotes",
              file=sys.stderr)
        return 2

    order = AUTO_ORDER if args.deployment == "auto" else (args.deployment,)
    attempts = []
    for key in order:
        cfg = DEPLOYMENTS[key]
        attempts.append(measure(
            key, cfg, policy, asset, args.timeout,
            ws_url=args.endpoint or cfg["ws"], rest=args.rest or cfg["rest"], now=now,
        ))

    picked_i = pick_deployment(attempts)
    if picked_i is None:
        print("no deployment produced a measurement:", file=sys.stderr)
        for a in attempts:
            print(f"  {a['key']}: {a['reason'] or 'no spot and no rows'}", file=sys.stderr)
        return 2
    picked = attempts[picked_i]

    notes = [
        "public unauthenticated feed; no account, no keys, nothing signed",
        f"spot {picked['spot']:,.4f} from {asset}-PERP mid, observed "
        f"{picked['observed_at'].isoformat().replace('+00:00', 'Z')}",
        f"grid: active puts, tenor >= {min_tenor:.0f}d, strikes within {STRIKE_WINDOW[0]:.0%}.."
        f"{STRIKE_WINDOW[1]:.0%} of spot ({picked['grid']} subscribed, "
        f"{picked['reported']} reported)",
    ]
    if len(order) > 1:
        notes.append("deployment auto order " + " -> ".join(order))
    for a in attempts:
        if a is not picked:
            notes.append(f"{a['key']}: not published — {a['reason'] or 'no qualifying quote'}")
    if args.endpoint or args.rest:
        notes.append(f"endpoint override: {picked['ws_url']}")
    if picked["selected"] is None:
        notes.append("no two-sided long-dated put qualified — the venue is unquoted at this tenor")

    artifact = build_artifact(
        venue="derive", deployment=picked["deployment"], endpoint=picked["ws_url"], asset=asset,
        fetched_at=picked["observed_at"], spot=picked["spot"], spot_source=picked["spot_source"],
        policy_inputs={
            "min_tenor_days": policy.get("min_tenor_days"),
            "strike_otm_pct": policy.get("strike_otm_pct"),
            "max_premium_bps": policy.get("max_premium_bps"),
        },
        rows=picked["rows"], selected=picked["selected"], notes=notes,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n")

    print(f"\n[{picked['key']}] spot {picked['spot']:,.2f}  |  {picked['grid']} subscribed, "
          f"{len(picked['rows'])} books reported")
    for r in picked["rows"]:
        if r.two_sided:
            print(f"  {r.instrument:26} {r.bid:>9} / {r.ask:<9}  ask = "
                  f"{r.premium_bps(picked['spot']):8.1f} bps  ({r.tenor_days:6.1f}d, "
                  f"{r.cost_per_day_bps(picked['spot']):5.2f} bps/day)")
    if picked["selected"]:
        sel = picked["selected"]
        print(f"\n  SELECTED  {sel['instrument']}  ask {sel['ask']}  {sel['premium_bps']} bps of "
              f"protected notional  ({sel['cost_per_day_bps']} bps/day)")
    else:
        print("\n  SELECTED  none — no qualified two-sided quote")
    for a in attempts:
        if a is not picked:
            print(f"  [{a['key']}: not published — {a['reason'] or 'no qualifying quote'}]")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
