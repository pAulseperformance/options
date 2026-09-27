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


# --- --portfolio: measured positions instead of a hand-typed number ------------------------

def write_portfolio(tmp_path: Path, complete: bool = True, usd: float = 4891.85,
                    accounts: list | None = None, version: int = 1) -> Path:
    body = {
        "schema_version": version, "produced_by": "options/portfolio_reader",
        "read_only": True, "fetched_at": "2026-09-27T00:10:00+00:00", "stale_after_hours": 24,
        "complete": complete,
        "prices": {"source": "t", "fetched_at": "2026-09-27T00:10:00+00:00",
                   "rates": {"ETH": 2715.97}},
        "accounts": accounts if accounts is not None else [
            {"venue": "ethereum-l1", "label": "spot · Ethereum L1", "read": True, "ok": True,
             "usd_total": usd, "positions": []}],
        "totals": {"usd_total": usd, "cash_usd": 0.0, "crypto_usd": usd},
        "exposure": {"usd": usd, "label": "net crypto (spot + perp), all readable accounts",
                     "components": [], "notes": []},
        "notes": [],
    }
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps(body))
    return p


def test_portfolio_artifact_supplies_the_exposure(tmp_path):
    out = tmp_path / "coverage.json"
    argv = ["--policy", str(write_policy(tmp_path)),
            "--portfolio", str(write_portfolio(tmp_path)), "--write", str(out)]
    assert coverage_cli_main(argv) == 0
    art = json.loads(out.read_text())
    assert art["subject"]["exposure_usd"] == pytest.approx(4891.85)
    assert art["subject"]["label"].startswith("net crypto")
    assert "portfolio:" in art["subject"]["source"]
    assert art["plan"]["exposure_usd"] == pytest.approx(4891.85)


def test_exposure_usd_and_portfolio_together_are_rejected(tmp_path):
    argv = ["--policy", str(write_policy(tmp_path)), "--exposure-usd", "4860",
            "--portfolio", str(write_portfolio(tmp_path))]
    assert coverage_cli_main(argv) == 2


def test_neither_exposure_nor_portfolio_is_rejected(tmp_path):
    assert coverage_cli_main(["--policy", str(write_policy(tmp_path))]) == 2


def test_an_incomplete_portfolio_is_refused_with_the_reason(tmp_path, capsys):
    bad = [{"venue": "lighter-mainnet", "label": "Lighter · mainnet", "read": True, "ok": False,
            "error": "TimeoutError: read timed out", "positions": []}]
    argv = ["--policy", str(write_policy(tmp_path)),
            "--portfolio", str(write_portfolio(tmp_path, complete=False, accounts=bad))]
    assert coverage_cli_main(argv) == 2
    err = capsys.readouterr().err
    assert "incomplete" in err and "Lighter · mainnet" in err


def test_a_wrong_portfolio_version_is_refused(tmp_path):
    argv = ["--policy", str(write_policy(tmp_path)),
            "--portfolio", str(write_portfolio(tmp_path, version=2))]
    assert coverage_cli_main(argv) == 2


def test_a_zero_exposure_portfolio_is_refused(tmp_path, capsys):
    argv = ["--policy", str(write_policy(tmp_path)),
            "--portfolio", str(write_portfolio(tmp_path, usd=0.0))]
    assert coverage_cli_main(argv) == 2
    assert "no net long exposure" in capsys.readouterr().err
