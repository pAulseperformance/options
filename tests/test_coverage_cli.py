"""coverage_cli end-to-end: the artifact it publishes, with and without fresh quotes."""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "apps"))

coverage_cli_main = importlib.import_module("coverage_cli.__main__").main

NOW = datetime.now(timezone.utc)


def write_policy(tmp: Path) -> Path:
    policy = {
        "target_ratio": 0.5, "asset": "ETH", "min_tenor_days": 180, "strike_otm_pct": 10.0,
        "max_premium_bps": 1200, "quotes_max_age_hours": 24, "triggers": [],
        "venues": [{"kind": "put_venue", "venue": "derive", "live": True, "premium_bps": 0.0,
                    "tenor_days": 181, "note": "v2 live on Derive Chain"}],
    }
    p = tmp / "policy.json"
    p.write_text(json.dumps(policy))
    return p


def write_quotes(tmp: Path, age_hours: float) -> Path:
    q = {
        "schema_version": 1,
        "venue": "derive",
        "fetched_at": (NOW - timedelta(hours=age_hours)).isoformat().replace("+00:00", "Z"),
        "selected": {"instrument": "ETH-20270326-2400-P", "premium_bps": 905.92,
                     "cost_per_day_bps": 5.01, "tenor_days": 180.8, "ask": 243.6},
    }
    p = tmp / "quotes.json"
    p.write_text(json.dumps(q))
    return p


def run(tmp: Path, quotes_path: Path | None) -> dict:
    out = tmp / "coverage.json"
    argv = ["--policy", str(write_policy(tmp)), "--exposure-usd", "4860", "--label", "t",
            "--write", str(out)]
    if quotes_path is not None:
        argv += ["--quotes", str(quotes_path)]
    assert coverage_cli_main(argv) == 0
    return json.loads(out.read_text())


def test_fresh_quotes_produce_the_real_plan(tmp_path):
    art = run(tmp_path, write_quotes(tmp_path, age_hours=1))
    plan = art["plan"]
    assert plan["is_covered"] is True
    assert plan["legs"][0]["venue"] == "derive"
    assert plan["legs"][0]["cost_bps"] == pytest.approx(905.92)
    assert plan["coverage_pct"] == pytest.approx(50.0)
    assert any("live book" in n for n in plan["notes"])
    assert art["quotes"]["applied"] is True
    assert art["quotes"]["instrument"] == "ETH-20270326-2400-P"


def test_stale_quotes_leave_the_venue_unquoted(tmp_path):
    art = run(tmp_path, write_quotes(tmp_path, age_hours=48))
    plan = art["plan"]
    assert plan["is_covered"] is False
    assert plan["uncovered_usd"] == pytest.approx(4860.0)
    assert any("ignored" in n for n in plan["notes"])
    assert art["quotes"]["applied"] is False


def test_without_quotes_the_venue_has_no_real_quote(tmp_path):
    art = run(tmp_path, None)
    plan = art["plan"]
    assert plan["is_covered"] is False
    assert any("no real quote" in n for n in plan["notes"])
    assert "quotes" not in art
