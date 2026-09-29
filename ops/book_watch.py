#!/usr/bin/env python3
"""Derive long-dated book watch — run every 30 min by Hermes cron (no_agent).

Contract:
  stdout  EMPTY unless the long-dated options book has come BACK (state transition).
          A delivered message therefore always means "there is something to buy".
  exit 0  always when the pipeline ran (even with an empty book).
  exit 1  with one stdout line when the watch itself is broken (scanner failed,
          uv missing) — a dead watch must not look quiet.

Side effect: refreshes data/quotes.json + data/coverage.json every tick, so the
trading dashboard stays current between manual runs.

Deployment-aware: the scanner picks the deployment to measure (v3 first, v2 fallback),
and alerts fire both when a book returns and when the published deployment moves —
"the pipeline now measures v3" is the migration-era signal, with or without quotes.

State: data/book_watch_state.json (dedupe signature). Stdlib only.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "book_watch_state.json"
GENUINE_MIN_SIZE = 1.0  # a two-sided quote at size >= 1.0 is a real market, not a stub


def evaluate(q, prev):
    """Pure decision + dedupe. Returns (alert_text_or_None, new_state_signature).

    Fires when the long-dated book newly has something to buy — or when the published
    deployment itself moved (v3 coming up, or falling back — the migration-era signal).
    Also fires once when the pipeline's source becomes v3 even with no quotes yet: the
    venue transition is worth knowing before its book fills.
    """
    two = [r for r in q.get("ladder", []) if r.get("bid") and r.get("ask")]
    genuine = [r for r in two if (r.get("ask_size") or 0) >= GENUINE_MIN_SIZE]
    dep = q.get("deployment") or ""
    sig = {
        "deployment": dep,
        "genuine": sorted(r["instrument"] for r in genuine),
        "selected": (q.get("selected") or {}).get("instrument"),
    }
    was_has = bool(prev.get("genuine")) or bool(prev.get("selected"))
    now_has = bool(genuine) or bool(sig["selected"])
    moved = bool(prev.get("deployment")) and prev["deployment"] != dep

    if now_has and (not was_has or moved):
        head = "DERIVE LONG-DATED BOOK IS BACK"
        if moved:
            head += f" — venue moved to {dep}"
        elif dep:
            head += f" ({dep})"
        lines = [head]
        for r in sorted(genuine, key=lambda r: r.get("premium_bps_ask") or 9e9)[:4]:
            lines.append(
                "  {i}: bid {b} / ask {a} x size {s} — {p} bps, {t:.0f}d".format(
                    i=r["instrument"],
                    b=r.get("bid"),
                    a=r.get("ask"),
                    s=r.get("ask_size"),
                    p=r.get("premium_bps_ask"),
                    t=r.get("tenor_days") or 0,
                )
            )
        if sig["selected"]:
            lines.append(f"policy-selected: {sig['selected']} — the plan can republish as COVERED")
        else:
            lines.append("no policy-qualifying put yet (premium/size vs config/policy.yml)")
        if "v3" in dep:
            lines.append("first quotes on the new venue — expected around the migration; "
                         "the plan reprices from this measurement.")
        lines.append("window may be brief — last time the maker left within 90 minutes.")
        return "\n".join(lines), sig

    if moved and "v3" in dep:
        return "\n".join([
            "DERIVE PIPELINE MOVED TO V3 — the measured deployment changed, v2 is quiet;",
            f"{dep} is now the venue being watched, no quotes yet.",
            "This is the migration-era transition; the watch keeps measuring v3 every 30 min.",
        ]), sig

    return None, sig


def find_uv():
    for c in (
        shutil.which("uv"),
        os.path.expanduser("~/.local/bin/uv"),
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
    ):
        if c and Path(c).exists():
            return c
    return None


def run(cmd, timeout):
    return subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONPATH": "apps"},
    )


def main():
    uv = find_uv()
    if not uv:
        print("BOOK WATCH DEAD: uv not found on PATH or common locations")
        sys.exit(1)

    base = [uv, "run", "--with", "websockets", "--with", "pyyaml", "--with", "jsonschema", "--no-project"]

    scan = run(base + ["python", "-m", "derive_quotes"], timeout=240)
    if scan.returncode != 0:
        tail = (scan.stderr or "").strip().splitlines()[-2:]
        print(f"BOOK WATCH DEAD: derive_quotes exited {scan.returncode}: {' | '.join(tail)}")
        sys.exit(1)

    # refresh the plan artifact too (dashboard stays current; planner refuses incomplete input by design)
    run(
        base
        + [
            "python", "-m", "coverage_cli",
            "--policy", "config/policy.yml",
            "--portfolio", "data/portfolio.json",
            "--quotes", "data/quotes.json",
            "--write", "data/coverage.json",
        ],
        timeout=240,
    )

    try:
        q = json.loads((ROOT / "data" / "quotes.json").read_text())
    except Exception as e:
        print(f"BOOK WATCH DEAD: unreadable data/quotes.json: {e}")
        sys.exit(1)

    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text())
        except Exception:
            prev = {}
    text, sig = evaluate(q, prev)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(sig, indent=0))
    if text:
        print(text)
    sys.exit(0)


if __name__ == "__main__":
    main()
