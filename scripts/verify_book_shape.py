"""Verify the published artifact by SHAPE only — counts, kinds and flags, never amounts.

The book's figures are the user's private business and the reader's stdout carries them, so this
prints what changed structurally and nothing that could be quoted.
"""
import json
from collections import Counter
from pathlib import Path

d = json.loads(Path("data/portfolio.json").read_text())

kinds = Counter(p["kind"] for a in d["accounts"] for p in a["positions"])
print("complete            :", d["complete"])
print("accounts read       :", sum(1 for a in d["accounts"] if a.get("read")), "of", len(d["accounts"]))
print("accounts ok         :", sum(1 for a in d["accounts"] if a.get("ok")))
print("position kinds      :", dict(kinds))
print("rows with no price  :", sum(1 for a in d["accounts"] for p in a["positions"] if p.get("usd") is None))
print("totals source       :", d["totals"].get("usd_total_source"))
print("rates carry         :", ", ".join(sorted(d["prices"]["rates"])))
print("rh marks error      :", d["prices"].get("rh_marks_error", "(none)"))
print("history rows        :", sum(1 for _ in Path("data/portfolio_history.jsonl").open()))

notes = [n for a in d["accounts"] for n in a.get("notes", [])]
for needle in ("no feed prices it", "held in the venue's locked_balance", "no price in any feed",
               "understated"):
    hit = [n for n in notes if needle in n]
    print(f"notes[{needle!r}]: {len(hit)}")
