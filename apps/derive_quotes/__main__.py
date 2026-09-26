"""derive_quotes — read the live Derive book and record what the protective put actually costs.

Read-only: a public websocket, no account, no keys, nothing signed. Publishes data/quotes.json,
the measurement that lets coverage_cli replace its placeholder premiums with real ones.

Run:
    PYTHONPATH=apps uv run --with websockets --with pyyaml --no-project python -m derive_quotes
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

from .quote import best_levels, build_artifact, rows_from_snapshots, select_protective_put  # noqa: E402

ENDPOINTS = {
    # Venue knowledge lives beside the venue's code, not in policy: an endpoint is not a decision.
    # v2 (api.lyra.finance) is the live production deployment on Derive Chain. v3 (api.derive.xyz)
    # is the pre-launch zk stack with an EMPTY production book — do not point this at it until it
    # trades; re-check with ops/derive_book_probe.mjs, which is the v3 gate.
    "derive": {
        "ws": "wss://api.lyra.finance/ws",
        "rest": "https://api.lyra.finance",
        "deployment": "v2 · Derive Chain (production)",
    },
}

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="derive_quotes", description=__doc__)
    ap.add_argument("--venue", default="derive")
    ap.add_argument("--policy", default=str(ROOT / "config" / "policy.yml"))
    ap.add_argument("--asset", default=None, help="underlying (default: the policy's asset)")
    ap.add_argument("--out", default=str(ROOT / "data" / "quotes.json"))
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help="seconds to wait for the option book snapshots")
    ap.add_argument("--endpoint", default=None, help="override the venue's websocket URL")
    args = ap.parse_args(argv)

    if args.venue not in ENDPOINTS:
        print(f"unknown venue {args.venue!r}; known: {sorted(ENDPOINTS)}", file=sys.stderr)
        return 2
    venue_cfg = ENDPOINTS[args.venue]
    ws_url = args.endpoint or venue_cfg["ws"]

    policy = load_policy(Path(args.policy))
    asset = args.asset or policy.get("asset", "ETH")
    min_tenor = float(policy.get("min_tenor_days", 0) or 0)
    now = datetime.now(timezone.utc)

    try:
        catalogue = rest_get(f"{venue_cfg['rest']}/public/get_instruments"
                             f"?instrument_type=option&currency={asset}&expired=false")["result"]
    except Exception as exc:
        print(f"catalogue fetch failed: {exc}", file=sys.stderr)
        return 2

    try:
        from websockets.sync.client import connect
    except ImportError:
        print("the websockets package is required — run with: PYTHONPATH=apps uv run "
              "--with websockets --with pyyaml --no-project python -m derive_quotes",
              file=sys.stderr)
        return 2

    lo, hi = STRIKE_WINDOW
    grid: list[dict] = []
    option_books: dict = {}
    observed_at = now
    try:
        print(f"connecting {ws_url} (public, read-only)…")
        with connect(ws_url, open_timeout=15, close_timeout=5) as ws:
            # Phase 1 — the spot premiums are measured against: the perp's own mid.
            perp = f"{asset}-PERP"
            _subscribe(ws, [BOOK_CHANNEL.format(instrument=perp)], 1)
            perp_books: dict = {}
            _pump(ws, perp_books, time.monotonic() + SPOT_WAIT_S, want=1, accept={perp})
            spot = _mid(perp_books.get(perp) or {})
            if spot is None:
                print(f"no {perp} book arrived; cannot price anything", file=sys.stderr)
                return 2

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
            expiry_times = {}
            for inst in grid:
                ts = (inst.get("option_details") or {}).get("expiry")
                if ts is not None:
                    expiry_times[inst["instrument_name"]] = datetime.fromtimestamp(
                        ts, tz=timezone.utc)

            if grid:
                names = {i["instrument_name"] for i in grid}
                _subscribe(ws, [BOOK_CHANNEL.format(instrument=n) for n in sorted(names)], 2)
                _pump(ws, option_books, time.monotonic() + args.timeout,
                      want=len(names), accept=names)
            observed_at = datetime.now(timezone.utc)
    except Exception as exc:
        print(f"websocket session failed: {exc}", file=sys.stderr)
        return 2

    rows = rows_from_snapshots(option_books, observed_at, expiry_times)
    min_amount = min((float(i.get("minimum_amount") or 0.0) for i in grid), default=0.0)
    selected = select_protective_put(rows, policy, spot, min_tradable_size=min_amount)

    notes = [
        "public unauthenticated feed; no account, no keys, nothing signed",
        f"spot {spot:,.4f} from {asset}-PERP mid, observed "
        f"{observed_at.isoformat().replace('+00:00', 'Z')}",
        f"grid: active puts, tenor >= {min_tenor:.0f}d, strikes within {lo:.0%}..{hi:.0%} of "
        f"spot ({len(grid)} subscribed, {len(option_books)} reported)",
    ]
    if selected is None:
        notes.append("no two-sided long-dated put qualified — the venue is unquoted at this tenor")

    artifact = build_artifact(
        venue=args.venue, deployment=venue_cfg["deployment"], endpoint=ws_url, asset=asset,
        fetched_at=observed_at, spot=spot, spot_source=f"{asset}-PERP mid",
        policy_inputs={
            "min_tenor_days": policy.get("min_tenor_days"),
            "strike_otm_pct": policy.get("strike_otm_pct"),
            "max_premium_bps": policy.get("max_premium_bps"),
        },
        rows=rows, selected=selected, notes=notes,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n")

    print(f"\nspot {spot:,.2f}  |  {len(grid)} subscribed, {len(rows)} books reported")
    for r in rows:
        if r.two_sided:
            print(f"  {r.instrument:26} {r.bid:>9} / {r.ask:<9}  ask = "
                  f"{r.premium_bps(spot):8.1f} bps  ({r.tenor_days:6.1f}d, "
                  f"{r.cost_per_day_bps(spot):5.2f} bps/day)")
    if selected:
        print(f"\n  SELECTED  {selected['instrument']}  ask {selected['ask']}  "
              f"{selected['premium_bps']} bps of protected notional  "
              f"({selected['cost_per_day_bps']} bps/day)")
    else:
        print("\n  SELECTED  none — no qualified two-sided quote")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
