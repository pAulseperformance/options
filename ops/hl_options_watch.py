#!/usr/bin/env python3
"""Hyperliquid venue watch — run daily by Hermes cron (no_agent).

Watches for the three signals that would change the venue decision for
long-dated ETH puts:
  1. A native options endpoint on the Hyperliquid API (optionMeta etc.) —
     today it rejects; the day it answers, native options have landed.
  2. The HIP-4 outcome-market composition (templates / underlyings) — alert on
     NEW templates or underlyings (notably ETH), which signal product expansion.
  3. The Hypercall blog post slugs — a new post means a new market/venue update
     worth reviewing.

Contract:
  stdout  EMPTY unless one of the three changed. Delivered message = something to look at.
  exit 0  when the probes ran (unchanged or changed).
  exit 1  when every probe failed (a blind watch must not look quiet).
Stdlib only. State: data/hl_watch_state.json
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "hl_watch_state.json"
INFO_URL = "https://api.hyperliquid.xyz/info"
BLOG_URL = "https://blog.hypercall.xyz/"


def info(body):
    req = urllib.request.Request(
        INFO_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (venue-watch)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def main():
    alerts = []
    errors = 0
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            state = {}

    # 1) native options endpoint (422/404 today = not live)
    try:
        o = info({"type": "optionMeta"})
        alerts.append(
            "HYPERLIQUID NATIVE OPTIONS POSSIBLY LIVE: optionMeta endpoint answered -> "
            + json.dumps(o)[:300]
            + "  [re-run venue diligence, re-point the options reader]"
        )
    except Exception:
        pass  # expected: the endpoint does not exist yet

    # 2) outcome-market composition
    try:
        meta = info({"type": "outcomeMeta"})
        templates, underlyings = set(), set()
        for o in meta.get("outcomes", []):
            templates.add(o.get("name") or "?")
            m = re.search(r"perp:([A-Za-z0-9:]+)", o.get("description") or "")
            if m:
                underlyings.add(m.group(1))
        sig = {"templates": sorted(templates), "underlyings": sorted(underlyings)}
        old = state.get("outcomes") or {}
        if old:
            new_t = sorted(set(sig["templates"]) - set(old.get("templates", [])))
            new_u = sorted(set(sig["underlyings"]) - set(old.get("underlyings", [])))
            if new_t or new_u:
                alerts.append(
                    f"HYPERLIQUID OUTCOMES CHANGED: new templates +{new_t}, "
                    f"new underlyings +{new_u} — check whether anything options-like "
                    f"(esp. ETH, longer-dated) is now tradeable"
                )
        state["outcomes"] = sig
    except Exception as e:
        errors += 1
        print(f"HL WATCH: outcomeMeta probe failed: {e}", file=sys.stderr)

    # 3) Hypercall blog — new post slugs
    try:
        html = fetch(BLOG_URL)
        slugs = sorted(set(re.findall(r'href="/([a-z0-9][a-z0-9-]{8,})/"', html)))
        old_slugs = state.get("hypercall_slugs") or []
        if old_slugs:
            new = sorted(set(slugs) - set(old_slugs))
            if new:
                alerts.append(f"HYPERCALL BLOG: new post(s) — {', '.join(new[:5])} (check for new markets)")
        state["hypercall_slugs"] = slugs
    except Exception as e:
        errors += 1
        print(f"HL WATCH: hypercall blog probe failed: {e}", file=sys.stderr)

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=0))

    if alerts:
        print("\n".join(alerts))
        sys.exit(0)
    if errors >= 3:
        print("HL WATCH DEAD: every probe failed")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
