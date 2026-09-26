"""The decision engine. The load-bearing test is the last one: unavailable insurance must SAY so.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

import pytest  # noqa: E402

from options_core import PutVenue, plan_hedge  # noqa: E402
from options_core.mechanisms import Availability, build_mechanisms  # noqa: E402

POLICY = {
    "target_ratio": 0.5,
    "min_tenor_days": 180,
    "triggers": ["re-evaluate when the protected position is closed"],
}


def test_no_exposure_is_a_note_not_a_plan():
    plan = plan_hedge(0.0, POLICY, [])
    assert plan.legs == []
    assert not plan.is_covered
    assert any("nothing to insure" in n for n in plan.notes)


def test_burned_off_is_a_decision_and_is_recorded():
    """A trigger firing is a real decision. It must not look like 'we forgot'."""
    plan = plan_hedge(5_000.0, POLICY, [], burned_off=True)
    assert plan.burned_off is True
    assert plan.legs == []
    assert plan.covered_usd == 0.0
    assert plan.uncovered_usd == 0.0, "a burned-off position has no uncovered exposure to report"


def test_live_venue_is_selected_and_sized():
    derive = PutVenue(venue="derive", premium_bps=800.0, tenor_days=270, live=True)
    plan = plan_hedge(5_000.0, POLICY, [derive])
    assert plan.is_covered
    assert len(plan.legs) == 1
    assert plan.legs[0].venue == "derive"
    assert plan.legs[0].instrument == "long_put"
    assert plan.legs[0].notional_usd == pytest.approx(2_500.0)
    assert plan.legs[0].cost_usd == pytest.approx(200.0)   # 8% of 2,500
    assert plan.coverage_pct == pytest.approx(50.0)


def test_venue_is_unavailable_by_default():
    """The gate is the default, not an opt-out. Asserted so flipping it is always deliberate."""
    assert PutVenue(venue="derive", premium_bps=800.0).availability().ok is False
    assert PutVenue(venue="lighter_options", premium_bps=800.0).availability().ok is False


def test_live_but_unquoted_is_still_unavailable():
    """A zero placeholder must never win a cheapest-venue comparison it did not earn."""
    venue = PutVenue(venue="derive", premium_bps=0.0, live=True)
    assert venue.availability().ok is False
    assert "no real quote" in venue.availability().reason


def test_tenor_shorter_than_policy_minimum_is_refused():
    short = PutVenue(venue="derive", premium_bps=100.0, tenor_days=30, live=True)
    plan = plan_hedge(5_000.0, POLICY, [short])
    assert not plan.is_covered
    assert any("shorter than the policy's 180d" in n for n in plan.notes)


def test_cheapest_venue_wins_and_the_loser_is_reported():
    cheap = PutVenue(venue="derive", premium_bps=600.0, tenor_days=270, live=True)
    dear = PutVenue(venue="lighter_options", premium_bps=900.0, tenor_days=270, live=True)
    plan = plan_hedge(5_000.0, POLICY, [dear, cheap])
    assert plan.legs[0].venue == "derive"
    assert plan.legs[0].cost_bps == pytest.approx(600.0)


def test_policy_ceiling_declines_and_says_why():
    dear = PutVenue(venue="derive", premium_bps=1500.0, tenor_days=270, live=True)
    plan = plan_hedge(5_000.0, {**POLICY, "max_premium_bps": 1000.0}, [dear])
    assert not plan.is_covered
    assert plan.uncovered_usd == pytest.approx(5_000.0)
    assert any("ceiling" in n for n in plan.notes)


def test_unavailable_venue_must_state_a_reason():
    """An empty reason is how 'we cannot buy insurance' silently becomes 'we are insured'."""
    with pytest.raises(ValueError):
        Availability(ok=False, reason="   ")


def test_no_venue_leaves_exposure_explicitly_uncovered():
    """THE test. If nothing can sell the insurance, the plan must say so — never return a quiet
    empty leg list a consumer could read as 'nothing needed'."""
    derive = PutVenue(venue="derive", premium_bps=0.0, live=False,
                      note="V3 mainnet pre-launch: production orderbook verified empty")
    lighter = PutVenue(venue="lighter_options", premium_bps=0.0, live=False,
                       note="Lighter options do not exist yet")
    plan = plan_hedge(5_000.0, POLICY, [derive, lighter])

    assert not plan.is_covered
    assert plan.uncovered_usd == pytest.approx(5_000.0), "uninsured exposure must be visible"
    joined = " ".join(plan.notes)
    assert "NO VENUE AVAILABLE" in joined
    assert "derive" in joined and "lighter_options" in joined
    assert "book verified empty" in joined, "the venue's own reason must survive to the plan"


def test_triggers_are_carried_onto_every_plan():
    plan = plan_hedge(5_000.0, POLICY, [])
    assert plan.triggers == ["re-evaluate when the protected position is closed"]


def test_build_mechanisms_from_policy():
    policy = {"venues": [
        {"kind": "put_venue", "venue": "derive", "live": False, "premium_bps": 0.0,
         "tenor_days": 270},
        {"kind": "put_venue", "venue": "lighter_options", "live": False, "premium_bps": 0.0},
    ]}
    mechs = build_mechanisms(policy)
    assert [m.name for m in mechs] == ["derive", "lighter_options"]
    assert all(m.availability().ok is False for m in mechs)

    with pytest.raises(ValueError):
        build_mechanisms({"venues": [{"kind": "nonsense", "venue": "x"}]})


def test_cost_per_day_is_reported_for_tenor_comparison():
    """The artifact carries cost/day so a longer, pricier put can be compared honestly."""
    venue = PutVenue(venue="derive", premium_bps=900.0, tenor_days=300, live=True)
    plan = plan_hedge(5_000.0, POLICY, [venue])
    assert plan.legs[0].cost_per_day_bps == pytest.approx(3.0)
    assert plan.as_dict()["legs"][0]["cost_per_day_bps"] == pytest.approx(3.0)
