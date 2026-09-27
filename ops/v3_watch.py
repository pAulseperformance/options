#!/usr/bin/env python3
"""Derive V3 watch — run daily by Hermes cron (no_agent).

The venue's next era has three observable moments; this script pings on each:
  1. Snapshot vote "DIP: Launch Derive V3" leaves `active` -> final result (port-the-reader clock starts).
  2. A new Derive forum topic matching "migration" -> likely the migration notice (>=14 days ahead).
  3. api.derive.xyz/v3 starts answering like a live API -> new venue up; re-run diligence + port.

Contract: stdout EMPTY unless a signal fired (deduped via data/v3_watch_state.json).
Degraded sub-probes go to stderr; if BOTH signal probes (snapshot + forum) fail, exit 1 loud.
Stdlib only.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "v3_watch_state.json"
PID = "0xf0b2a758ec18edf9e27c5e779dd601140ae0766888de52a9fb2d749d3a87fdcb"
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


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
                % (
                    str(st).upper(),
                    s[0] / 1e6,
                    s[1] / 1e6,
                    p.get("votes"),
                )
            )
            alerts.append(
                "On pass: >=14-day notice, then automatic V2->V3 migration + Derive Chain wind-down. "
                "Port the reader at launch (docs.derive.xyz/migrating/breaking-changes.md)."
            )
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
        if old:
            for tid in sorted(set(ids) - old)[:5]:
                t = next((x for x in topics if x.get("id") == tid), None)
                title = (t or {}).get("title", "?")
                alerts.append(f'DERIVE FORUM: new migration-related topic #{tid} "{title}" — check for the migration notice.')
        if ids:
            state["forum_topic_ids"] = ids
    except Exception as e:
        fails += 1
        print(f"V3 WATCH DEGRADED: forum probe failed: {e}", file=sys.stderr)

    # 3. v3 production API liveness (expected dead until launch; answer = signal)
    try:
        st2, txt2 = http("https://api.derive.xyz/v3/public/get_time", timeout=15)
        live = st2 == 200 and bool(txt2.strip())
        if live and not state.get("v3_api_live"):
            alerts.append(f"DERIVE V3 API ANSWERING: {txt2.strip()[:120]} — the new venue is up; re-run the venue diligence + port the reader.")
        state["v3_api_live"] = live
    except Exception:
        state["v3_api_live"] = False

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
