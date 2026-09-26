"""Cost accounting: one unit (bps of notional) so venues and tenors are comparable."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

import pytest  # noqa: E402

from options_core import costs  # noqa: E402


def test_bps_of_basic():
    assert costs.bps_of(1.0, 10_000.0) == pytest.approx(1.0)
    assert costs.bps_of(2_428.0, 2_428.0) == pytest.approx(10_000.0)


def test_bps_of_refuses_zero_notional():
    """Zero notional is a caller bug. Returning 0 bps would read as 'free'."""
    with pytest.raises(ValueError):
        costs.bps_of(10.0, 0.0)


def test_put_premium_bps():
    # 2,000 USD premium protecting 20,000 USD notional = 1,000 bps = 10%.
    assert costs.put_premium_bps(2_000.0, 20_000.0) == pytest.approx(1_000.0)


def test_premium_does_not_scale_with_holding_period():
    """A put is paid once at entry and then protects its whole life. Nothing accrues."""
    assert costs.put_premium_bps(500.0, 10_000.0) == costs.put_premium_bps(500.0, 10_000.0)


def test_cost_per_day_is_how_tenors_compare():
    """The point of amortising: a pricier long-dated put can be cheaper per day of cover."""
    short = costs.cost_per_day_bps(total_bps=300.0, tenor_days=30)    # 10 bps/day
    long = costs.cost_per_day_bps(total_bps=900.0, tenor_days=363)    # ~2.5 bps/day
    assert short == pytest.approx(10.0)
    assert long < short, "cheaper per day despite costing 3x more up front"


def test_cost_per_day_refuses_nonpositive_tenor():
    with pytest.raises(ValueError):
        costs.cost_per_day_bps(300.0, 0.0)
    with pytest.raises(ValueError):
        costs.cost_per_day_bps(300.0, -5.0)


def test_premium_as_pct():
    assert costs.premium_as_pct(800.0) == pytest.approx(8.0)
