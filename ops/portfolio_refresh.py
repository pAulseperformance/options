#!/usr/bin/env python3
"""Portfolio refresh — the reader's clock. Run EVERY MINUTE by Hermes cron (no_agent).

Why this exists: every P&L on the trading dashboard's account band is a DISPLAY over
`data/portfolio.json`, and that file only changes when `portfolio_reader` runs. Nothing scheduled
it, so the book's P&L was frozen at the last manual run (13.5h old the morning it was reported)
while the mark prices printed beside it ticked every 20s — the panel looked live and was not.
This script is the missing clock, nothing more: the reader itself is unchanged.

Why a minute and not five: the band polls every 20s, so this clock IS the P&L's real refresh rate,
and a five-minute one made the number step while everything around it moved. Measured BEFORE the
change (three consecutive full reads ~30s apart, then a probe at 1-minute spacing over 8 ticks):
the public account endpoint answered every read, and the share of ticks carrying a throttled
account was the same ~10-14% at 1-minute spacing as at 5-minute spacing — the 429/405 walls come
from the fleet's other venue traffic, not from this clock's own rate. Faster also SHORTENS each
wall: an incomplete artifact now stands for a minute instead of five. ~17 calls and 6-9s per tick.

Contract:
  stdout  EMPTY when the artifact republishes complete. A delivered message therefore always means
          the READ is wrong (an account failed), never "the book moved" — and only when that read
          stays wrong: the venue answers a burst with 429/405 and the next read is whole again, so
          a wall must survive PERSIST_TICKS reads before it is worth anyone's attention.
  exit 0  the refresh ran — including the case where the artifact published with a failed account
          (it still publishes, marked incomplete, on purpose).
  exit 1  the refresh itself is broken (reader wrote nothing / uv missing / unreadable artifact).
          A dead refresh must not look like a quiet book.

Two guards, both cheap:
  * a lock file, so a slow run and the next tick cannot overlap and interleave two writes;
  * a MIN_AGE floor on the artifact, so a manual `python -m portfolio_reader` and this tick do not
    fight over the same file. It must stay UNDER the cron interval — at 90s a one-minute clock
    skipped every other tick (60s < 90s) and quietly ran at two minutes instead.

State: data/portfolio_refresh_state.json (the incomplete streak, the hourly reminder of a read that
keeps failing). Stdlib only — no venv, no deps, runs on the scheduler's python.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "data" / "portfolio.json"
STATE = ROOT / "data" / "portfolio_refresh_state.json"
LOCK = ROOT / "data" / ".portfolio_refresh.lock"

MIN_AGE_S = 40        # MUST stay below the cron interval (60s), or half the ticks are skipped. Its
                      # only job is to not fight a read that just happened (a manual run, or a slow
                      # previous tick) — it is not the pacing.
READER_TIMEOUT = 300  # a full read is ~6s warm; this is a ceiling, not an expectation
REMIND_EVERY_S = 3600 # a read that stays incomplete speaks once an hour, not every tick
PERSIST_TICKS = 2     # consecutive incomplete reads before a wall is worth a message (see contract)
LIGHTER_PIN = "lighter-sdk @ git+https://github.com/elliottech/lighter-python.git"


def find_uv():
    for c in (shutil.which("uv"), os.path.expanduser("~/.local/bin/uv"),
              "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if c and Path(c).exists():
            return c
    return None


def artifact_age_s():
    try:
        return time.time() - ARTIFACT.stat().st_mtime
    except OSError:
        return None


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(s):
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=1))
    os.replace(tmp, STATE)


def failed_accounts(artifact):
    rows = [a for a in artifact.get("accounts") or []
            if a.get("read") is not False and a.get("ok") is not True]
    out = []
    for a in rows:
        err = (a.get("error") or "no error text").splitlines()[0][:180]
        out.append(f"{a.get('label') or a.get('venue')} — {err}")
    return out


def main():
    age = artifact_age_s()
    if age is not None and age < MIN_AGE_S:
        return 0                                   # just read (manual run or a fast repeat): skip
    if age is None:
        print("PORTFOLIO REFRESH: no artifact yet at %s — reading for the first time" % ARTIFACT)

    uv = find_uv()
    if not uv:
        print("PORTFOLIO REFRESH DEAD: uv not found on PATH or common locations — the book's P&L "
              "is frozen at the last read until this is fixed")
        return 1

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                                   # another refresh is mid-read; its result stands

    started = time.time()
    try:
        proc = subprocess.run(
            [uv, "run", "--with", LIGHTER_PIN, "--no-project", "python", "-m", "portfolio_reader"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=READER_TIMEOUT,
            env={**os.environ, "PYTHONPATH": "apps"},
        )
        rc, stderr = proc.returncode, (proc.stderr or "")
    except subprocess.TimeoutExpired:
        print(f"PORTFOLIO REFRESH DEAD: the reader did not finish in {READER_TIMEOUT}s — the book's "
              f"P&L is frozen at the last read")
        return 1
    except OSError as e:  # uv vanished between the which() and the exec, or is not executable
        print(f"PORTFOLIO REFRESH DEAD: cannot launch the reader ({type(e).__name__}: {e}) — the "
              f"book's P&L is frozen at the last read")
        return 1
    finally:
        LOCK.unlink(missing_ok=True)

    # rc is ambiguous: the reader exits 1 BOTH for "published, one account failed" and for an
    # uncaught crash. The artifact's own mtime separates them — did this run write anything?
    wrote = (artifact_age_s() or 9e9) <= (time.time() - started) + 1.0
    if not wrote:
        tail = " | ".join((stderr or "").strip().splitlines()[-2:]) or "no stderr"
        print(f"PORTFOLIO REFRESH DEAD: the reader exited {rc} without publishing — {tail}. "
              f"Every book P&L on the dashboard is frozen at the last good read.")
        return 1

    try:
        artifact = json.loads(ARTIFACT.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"PORTFOLIO REFRESH DEAD: unreadable artifact after the read ({type(e).__name__}: {e})")
        return 1

    if artifact.get("complete"):
        state = load_state()
        if state.get("announced"):
            print("book read recovered — every account is readable again; totals and exposure are "
                  "whole.")
        save_state({"incomplete": False, "since": None, "last_alert": None, "streak": 0,
                    "announced": False})
        return 0

    now = time.time()
    state = load_state()
    was_incomplete = bool(state.get("incomplete"))
    streak = (state.get("streak") or 0) + 1 if was_incomplete else 1
    since = state.get("since") if was_incomplete else None
    since = since or artifact.get("fetched_at") or datetime.now(timezone.utc).isoformat()
    announced = bool(state.get("announced"))
    # A single throttled account is a ONE-READ event: the venue answers a burst with 429/405 and the
    # next read comes back whole (measured at 1-minute spacing: 1 tick in 7). Announcing every one of
    # those would page the Signals topic for a blip that heals itself, so the signal is the read that
    # STAYS incomplete — the same failure on PERSIST_TICKS consecutive reads — plus an hourly
    # reminder for as long as it lasts. The streak resets on the first whole read.
    fresh_news = (not announced) and streak >= PERSIST_TICKS
    due = announced and (now - (state.get("last_alert") or 0)) >= REMIND_EVERY_S
    if fresh_news or due:
        announced = True
    save_state({"incomplete": True, "since": since, "streak": streak, "announced": announced,
                "last_alert": now if (fresh_news or due) else state.get("last_alert")})
    if fresh_news or due:
        bad = failed_accounts(artifact)
        print("BOOK READ INCOMPLETE since %s (%d reads in a row) — the dashboard's book UNDERSTATES "
              "reality right now.\n"
              "  %s\n"
              "  totals and exposure exclude what could not be read, and the plan refuses this "
              "artifact by design." % (since, streak,
                                       "\n  ".join(bad) if bad else "no per-account error text"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
