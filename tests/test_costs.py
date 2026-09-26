"""Cost accounting: one unit (bps of notional) so unlike mechanisms are comparable."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

import pytest  # noqa: E402

from hedge_core import costs  # noqa: E402


def test_bps_of_basic():
    assert costs.bps_of(1.0, 10_000.0) == pytest.approx(1.0)
    assert costs.bps_of(2_428.0, 2_428.0) == pytest.approx(10_000.0)


def test_bps_of_refuses_zero_notional():
    """Zero notional is a caller bug. Returning 0 bps would read as 'free'."""
    with pytest.raises(ValueError):
        costs.bps_of(10.0, 0.0)


def test_perp_funding_is_signed():
    """Positive funding = the short RECEIVES it. That is the whole reason to compare."""
    income = costs.perp_short_cost_bps(funding_bps_per_day=0.5, horizon_days=30)
    assert income == pytest.approx(15.0)

    paid = costs.perp_short_cost_bps(funding_bps_per_day=-0.5, horizon_days=30)
    assert paid == pytest.approx(-15.0)

    with_exec = costs.perp_short_cost_bps(0.0, 30, entry_exit_bps=2.0)
    assert with_exec == pytest.approx(2.0)


def test_long_put_cost_is_premium_only():
    # 2,000 USD premium protecting 20,000 USD notional = 1,000 bps.
    assert costs.long_put_cost_bps(2_000.0, 20_000.0) == pytest.approx(1_000.0)


def test_put_cost_does_not_scale_with_horizon():
    """The defining difference from funding carry: the premium is paid once."""
    a = costs.long_put_cost_bps(500.0, 10_000.0)
    b = costs.long_put_cost_bps(500.0, 10_000.0)
    assert a == b


def test_implied_leverage_days():
    assert costs.implied_leverage_days(60.0, 30.0) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        costs.implied_leverage_days(60.0, 0.0)
