#!/usr/bin/env python3
"""Derive V3 watch — run daily by Hermes cron (no_agent).

The venue's next era has four observable moments; this script pings on each:
  1. Snapshot vote "DIP: Launch Derive V3" leaves `active` -> final result (port-the-reader clock starts).
  2. A new Derive forum topic matching "migration" -> likely the migration notice (>=14 days ahead).
  3. api.derive.xyz/v3 starts answering (fired 2026-09-28) -> infrastructure warming up; catalogue + WS
     live since (all books empty -- staged pre-migration).
  4. The v3 venue starts TRADING (ETH-PERP activity, or any long-dated ETH option going two-sided)
     -> the migration era has arrived; port the reader per docs.derive.xyz/migrating/breaking-changes.

This watch also carries Paul Mendes's deferred DRV entry reminder (2026-09-28, "remind me after the
migration"): a heads-up line rides the vote-close and migration-notice alerts, and the full reminder
(+ a live price) fires when the venue starts trading. See ~/Projects/options/HANDOFF.md (Open items).

Contract: stdout EMPTY unless a signal fired (deduped via data/v3_watch_state.json).
Degraded signal probes go to stderr; if BOTH must-live probes (snapshot + forum) fail, exit 1 loud.
The v3 trading probe and the DRV price fetch are best-effort: they never fail the watch.
Stdlib only.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "v3_watch_state.json"
PID = "0xf0b2a758ec18edf9e27c5e779dd601140ae0766888de52a9fb2d749d3a87fdcb"
DRV_BASE = "0x9D0E8F5B25384C7310CB8C6AE32C8FBEB645D083"  # DRV on Base; price fallback for the reminder
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# Deferred DRV entry (Paul Mendes, 2026-09-28): revisit after the migration.
DRV_SOON = (
    "DRV entry (deferred): comes due after the migration — small spot only; "
    "pilot + ladder $0.36 / $0.32; no chase above $0.50."
)
DRV_NOW = (
    "DRV ENTRY — deferred reminder, now due: buy small, spot only; pilot at market + ladder "
    "$0.36 / $0.32; no chase above $0.50. (Levels from the 2026-09-28 read — re-check live.)"
)


def http(url, data=None, headers=None, timeout=25):
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def gql(query):
    body = json.dumps({"query": query}).encode()
    hdr = {"Content-Type": "application/json", "Origin": "https://snapshot.box"}
    _, txt = http("https://hub.snapshot.org/graphql", data=body, headers=hdr)
    return json.loads(txt)


def drv_price():
    """Best-effort live DRV price for the reminder. DexScreener first (Base pool; this host gets
    CoinGecko 403s in bursts), CoinGecko as fallback. None on failure — never fails the watch."""
    try:
        _, txt = http("https://api.dexscreener.com/latest/dex/tokens/%s" % DRV_BASE, timeout=15)
        pairs = [p for p in (json.loads(txt).get("pairs") or []) if p.get("priceUsd")]
        if pairs:
            top = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            return float(top["priceUsd"])
    except Exception:
        pass
    try:
        _, txt = http("https://api.coingecko.com/api/v3/simple/price?ids=derive&vs_currencies=usd", timeout=15)
        return float(json.loads(txt)["derive"]["usd"])
    except Exception:
        return None


def v3_trading_evidence():
    """Evidence string if v3 shows any trading activity, else None. A one-way door: REST can lag
    live books, so this can fire late, never early."""
    ev = []
    try:
        _, txt = http("https://api.derive.xyz/v3/public/get_tickers?instrument_type=perp&currency=ETH", timeout=15)
        tk = ((json.loads(txt).get("result") or {}).get("tickers") or {}).get("ETH-PERP") or {}
        stats = tk.get("stats") or {}
        oi, n = float(stats.get("oi") or 0), int(stats.get("n") or 0)
        bb, ba = float(tk.get("b") or 0), float(tk.get("a") or 0)
        if oi or n or bb or ba:
            ev.append("ETH-PERP oi=%g trades=%d bid/ask=%g/%g" % (oi, n, bb, ba))
    except Exception as e:
        print("V3 WATCH DEGRADED: perp ticker probe failed: %s" % e, file=sys.stderr)
    try:
        _, txt = http(
            "https://api.derive.xyz/v3/public/get_tickers?instrument_type=option&currency=ETH&expiry_date=20270924",
            timeout=15,
        )
        tk = (json.loads(txt).get("result") or {}).get("tickers") or {}
        live = [k for k, v in tk.items() if float(v.get("a") or 0) or float(v.get("b") or 0)]
        if live:
            ev.append("long-dated ETH options quoting (%d, e.g. %s)" % (len(live), live[0]))
    except Exception as e:
        print("V3 WATCH DEGRADED: option ticker probe failed: %s" % e, file=sys.stderr)
    return " / ".join(ev) if ev else None


def main():
    alerts = []
    fails = 0
    state = json.loads(STATE.read_text()) if STATE.exists() else {}

    # 1. Snapshot vote state
    try:
        d = gql('{ proposal(id: "%s") { state scores scores_total votes } }' % PID)
        p = (d.get("data") or {}).get("proposal") or {}
        st = p.get("state")
        prev = state.get("snapshot_state")
        if prev == "active" and st and st != prev:
            s = list(p.get("scores") or []) + [0, 0]
            alerts.append(
                "DERIVE V3 VOTE %s: For %.1fM vs Against %.1fM DRV (%s voters)."
                % (str(st).upper(), s[0] / 1e6, s[1] / 1e6, p.get("votes"))
            )
            alerts.append(
                "On pass: >=14-day notice, then automatic V2->V3 migration + Derive Chain wind-down. "
                "Port the reader at launch (docs.derive.xyz/migrating/breaking-changes.md)."
            )
            alerts.append(DRV_SOON)
        if st:
            state["snapshot_state"] = st
    except Exception as e:
        fails += 1
        print(f"V3 WATCH DEGRADED: snapshot probe failed: {e}", file=sys.stderr)

    # 2. Forum: new topics matching "migration"
    try:
        _, txt = http("https://forums.derive.xyz/search.json?q=migration")
        d = json.loads(txt)
        topics = d.get("topics", []) or []
        ids = sorted({t["id"] for t in topics if t.get("id")})
        old = set(state.get("forum_topic_ids") or [])
        new_ids = sorted(set(ids) - old)
        if old and new_ids:
            for tid in new_ids[:5]:
                t = next((x for x in topics if x.get("id") == tid), None)
                title = (t or {}).get("title", "?")
                alerts.append(f'DERIVE FORUM: new migration-related topic #{tid} "{title}" — check for the migration notice.')
            alerts.append(DRV_SOON)
        if ids:
            state["forum_topic_ids"] = ids
    except Exception as e:
        fails += 1
        print(f"V3 WATCH DEGRADED: forum probe failed: {e}", file=sys.stderr)

    # 3. v3 production API liveness (first answered 2026-09-28; alert fired then)
    try:
        st2, txt2 = http("https://api.derive.xyz/v3/public/get_time", timeout=15)
        live = st2 == 200 and bool(txt2.strip())
        if live and not state.get("v3_api_live"):
            alerts.append(f"DERIVE V3 API ANSWERING: {txt2.strip()[:120]} — the new venue is up; re-run the venue diligence + port the reader.")
        state["v3_api_live"] = live
    except Exception:
        state["v3_api_live"] = False

    # 4. v3 trading liveness (all books empty at the 2026-09-29 re-probe; fires when activity appears)
    try:
        ev = v3_trading_evidence()
    except Exception as e:
        ev = None
        print("V3 WATCH DEGRADED: trading probe failed: %s" % e, file=sys.stderr)
    if ev and not state.get("v3_trading_seen"):
        alerts.append("DERIVE V3 IS TRADING: %s — the new venue is opening; port the reader + re-run the venue diligence." % ev)
        px = drv_price()
        alerts.append(DRV_NOW + (" DRV now $%.4f." % px if px else ""))
    state["v3_trading_seen"] = bool(state.get("v3_trading_seen")) or bool(ev)

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=0))

    if fails >= 2:
        print("V3 WATCH DEAD: both signal probes failed (network?) — check it.")
        sys.exit(1)
    if alerts:
        print("\n".join(alerts))
    sys.exit(0)


if __name__ == "__main__":
    main()
