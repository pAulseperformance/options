"""Overlay semantics: a measurement prices the plan only while it is fresh enough to be true."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from options_core import build_mechanisms, overlay_quotes, plan_hedge  # noqa: E402

NOW = datetime(2026, 9, 26, 12, 30, tzinfo=timezone.utc)
POLICY = {
    "target_ratio": 0.5,
    "min_tenor_days": 180,
    "strike_otm_pct": 10.0,
    "quotes_max_age_hours": 24,
    "venues": [{"kind": "put_venue", "venue": "derive", "live": True, "premium_bps": 0.0,
                "tenor_days": 181, "note": "v2 live on Derive Chain"}],
}
SELECTED = {"instrument": "ETH-20270326-2400-P", "premium_bps": 905.92,
            "cost_per_day_bps": 5.01, "tenor_days": 180.8, "ask": 243.6}


def quotes_doc(fetched_at: datetime, selected: dict | None = SELECTED) -> dict:
    return {
        "schema_version": 1,
        "venue": "derive",
        "fetched_at": fetched_at.isoformat().replace("+00:00", "Z"),
        "selected": selected,
    }


def test_fresh_quote_prices_the_venue_and_the_plan_covers():
    venues = build_mechanisms(POLICY)
    venues, notes, meta = overlay_quotes(venues, quotes_doc(NOW - timedelta(hours=1)),
                                         source="data/quotes.json", max_age_hours=24, now=NOW)
    assert meta["applied"] is True
    assert venues[0].premium_bps == pytest.approx(905.92)
    assert venues[0].tenor_days == 181            # round(180.8) — the measured tenor, not the guess
    assert any("live book" in n for n in notes)

    plan = plan_hedge(4_860.0, POLICY, venues)
    assert plan.is_covered
    assert plan.legs[0].venue == "derive"
    assert plan.legs[0].instrument == "long_put"
    assert plan.legs[0].notional_usd == pytest.approx(2_430.0)
    assert plan.legs[0].cost_usd == pytest.approx(2_430.0 * 905.92 / 10_000.0, rel=1e-6)
    assert plan.coverage_pct == pytest.approx(50.0)


def test_stale_quote_is_ignored_and_the_venue_stays_unquoted():
    venues = build_mechanisms(POLICY)
    venues, notes, meta = overlay_quotes(venues, quotes_doc(NOW - timedelta(hours=30)),
                                         source="data/quotes.json", max_age_hours=24, now=NOW)
    assert meta["applied"] is False
    assert "stale" in meta["reason"]
    assert venues[0].premium_bps == 0.0           # untouched: the placeholder stays a placeholder
    assert any("stale" in n for n in notes)

    plan = plan_hedge(4_860.0, POLICY, venues)
    assert not plan.is_covered
    assert plan.uncovered_usd == pytest.approx(4_860.0)


def test_a_quote_run_without_a_selection_is_not_a_quote():
    venues = build_mechanisms(POLICY)
    venues, notes, meta = overlay_quotes(venues, quotes_doc(NOW, selected=None),
                                         source="s", max_age_hours=24, now=NOW)
    assert meta["applied"] is False
    assert "no qualifying" in meta["reason"]
    assert venues[0].premium_bps == 0.0


def test_unreadable_fetched_at_is_refused():
    doc = quotes_doc(NOW)
    doc["fetched_at"] = "yesterday-ish"
    venues, notes, meta = overlay_quotes(build_mechanisms(POLICY), doc, source="s",
                                         max_age_hours=24, now=NOW)
    assert meta["applied"] is False
    assert "fetched_at" in meta["reason"]


def test_quotes_for_an_unknown_venue_change_nothing():
    doc = quotes_doc(NOW)
    doc["venue"] = "deribit"
    venues, notes, meta = overlay_quotes(build_mechanisms(POLICY), doc, source="s",
                                         max_age_hours=24, now=NOW)
    assert meta is None
    assert any("match no venue" in n for n in notes)
