"""The decision engine. The load-bearing test is the last one: an unavailable hedge must SAY so.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

import pytest  # noqa: E402

from hedge_core import LongPutMechanism, PerpShortMechanism, plan_hedge  # noqa: E402
from hedge_core.mechanisms import Availability, build_mechanisms  # noqa: E402

POLICY = {"target_ratio": 0.5, "horizon_days": 30, "triggers": ["close > 3200 -> 0"]}


def test_no_exposure_is_a_note_not_a_hedge():
    plan = plan_hedge(0.0, POLICY, [])
    assert plan.legs == []
    assert not plan.is_covered
    assert any("no exposure" in n for n in plan.notes)


def test_burned_off_is_a_decision_and_is_recorded():
    """A trigger firing is a real decision. It must not look like 'we forgot'."""
    plan = plan_hedge(5_000.0, POLICY, [], burned_off=True)
    assert plan.burned_off is True
    assert plan.legs == []
    assert plan.covered_usd == 0.0
    assert plan.uncovered_usd == 0.0, "a burned-off hedge has no uncovered exposure to report"


def test_perp_short_selected_when_it_is_the_only_option():
    mech = PerpShortMechanism(funding_bps_per_day=0.1, entry_exit_bps=2.0)
    plan = plan_hedge(5_000.0, POLICY, [mech])
    assert plan.is_covered
    assert len(plan.legs) == 1
    assert plan.legs[0].mechanism == "perp_short"
    assert plan.legs[0].notional_usd == pytest.approx(2_500.0)
    assert plan.legs[0].caps_upside is True
    assert plan.coverage_pct == pytest.approx(50.0)


def test_long_put_selected_when_perp_unavailable():
    perp = PerpShortMechanism(0.0, venue="lighter", venue_live=False)
    put = LongPutMechanism(premium_bps=800.0, venue_live=True)
    plan = plan_hedge(5_000.0, POLICY, [perp, put])
    assert plan.legs[0].mechanism == "long_put"
    assert plan.legs[0].caps_upside is False
    assert any("unavailable" in n for n in plan.notes)


def test_long_put_is_unavailable_by_default():
    """The gate is the default, not an opt-out: no venue, no hedge. Asserted so that flipping it
    is always a deliberate edit."""
    assert LongPutMechanism(premium_bps=40.0).availability().ok is False


def test_cheapest_available_mechanism_wins():
    # 30 days of funding at +2 bps/day = 60 bps + 2 exec = 62 bps, vs a 40 bps put.
    perp = PerpShortMechanism(funding_bps_per_day=2.0, entry_exit_bps=2.0)
    put = LongPutMechanism(premium_bps=40.0, venue_live=True)
    plan = plan_hedge(5_000.0, POLICY, [perp, put])
    assert plan.legs[0].mechanism == "long_put"
    # Flip the economics: negative funding makes the short an earner.
    cheap_perp = PerpShortMechanism(funding_bps_per_day=-2.0, entry_exit_bps=2.0)
    plan2 = plan_hedge(5_000.0, POLICY, [cheap_perp, put])
    assert plan2.legs[0].mechanism == "perp_short"
    assert plan2.legs[0].cost_bps < 0


def test_policy_ceiling_declines_and_says_why():
    put = LongPutMechanism(premium_bps=900.0, venue_live=True)
    plan = plan_hedge(5_000.0, {**POLICY, "max_cost_bps": 100.0}, [put])
    assert not plan.is_covered
    assert plan.uncovered_usd == pytest.approx(5_000.0)
    assert any("ceiling" in n for n in plan.notes)


def test_unavailable_mechanism_must_state_a_reason():
    """An empty reason is how 'we cannot hedge' silently becomes 'we are hedged'."""
    with pytest.raises(ValueError):
        Availability(ok=False, reason="   ")


def test_no_mechanism_leaves_exposure_explicitly_uncovered():
    """THE test. If nothing can execute, the plan must say so — never return a quiet empty leg
    list that a consumer could read as 'nothing needed'."""
    perp = PerpShortMechanism(0.0, venue="lighter", venue_live=False)
    put = LongPutMechanism(premium_bps=500.0, venue="derive", venue_live=False,
                           venue_note="V3 mainnet pre-launch: book verified empty")
    plan = plan_hedge(5_000.0, POLICY, [perp, put])

    assert not plan.is_covered
    assert plan.uncovered_usd == pytest.approx(5_000.0), "unhedged exposure must be visible"
    joined = " ".join(plan.notes)
    assert "NO MECHANISM AVAILABLE" in joined
    assert "lighter" in joined and "derive" in joined
    assert "book verified empty" in joined, "the venue's own reason must survive to the plan"


def test_triggers_are_carried_onto_every_plan():
    plan = plan_hedge(5_000.0, POLICY, [PerpShortMechanism(0.1)])
    assert plan.triggers == ["close > 3200 -> 0"]


def test_build_mechanisms_from_policy():
    policy = {"mechanisms": [
        {"kind": "perp_short", "venue": "lighter", "venue_live": True,
         "funding_bps_per_day": 0.0, "entry_exit_bps": 2.0},
        {"kind": "long_put", "venue": "derive", "venue_live": False, "premium_bps": 0.0},
    ]}
    mechs = build_mechanisms(policy)
    assert [m.name for m in mechs] == ["perp_short", "long_put"]
    assert mechs[0].availability().ok is True
    assert mechs[1].availability().ok is False

    with pytest.raises(ValueError):
        build_mechanisms({"mechanisms": [{"kind": "nonsense"}]})
