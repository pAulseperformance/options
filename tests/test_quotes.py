"""The quote rules, tested offline against a recorded scan (tests/fixtures/).

The fixtures are two real scans of the live Derive v2 book, ~2.5 minutes apart, exactly as the
adapter saw them — so these tests pin behaviour to the venue's actual wire shape, not a
hand-written ideal of it.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "apps"))

from derive_quotes.quote import (  # noqa: E402
    best_levels, build_artifact, catalogue_instruments, parse_instrument, pick_deployment,
    rows_from_snapshots, select_protective_put,
)

FIXTURES = ROOT / "tests" / "fixtures"
POLICY = {"min_tenor_days": 180, "strike_otm_pct": 10.0}


def load_fixture(name: str):
    """Rebuild wire-shaped snapshots from a recorded scan (bid/ask/size per instrument)."""
    doc = json.loads((FIXTURES / name).read_text())
    snapshots = {}
    for row in doc["rows"]:
        if row["instrument"].endswith("-PERP"):
            continue
        snapshots[row["instrument"]] = {
            "bids": [[str(row["bid"]), str(row["bid_size"])]] if row["bid"] else [],
            "asks": [[str(row["ask"]), str(row["ask_size"])]] if row["ask"] else [],
        }
    observed = datetime.fromisoformat(doc["fetched_at"].replace("Z", "+00:00"))
    return snapshots, doc["spot"], observed


def test_parse_instrument_roundtrip():
    base, expiry, strike, kind = parse_instrument("ETH-20270326-2400-P")
    assert base == "ETH"
    assert expiry == datetime(2027, 3, 26, tzinfo=timezone.utc)
    assert strike == 2400.0
    assert kind == "P"


def test_parse_instrument_rejects_non_options():
    with pytest.raises(ValueError):
        parse_instrument("ETH-PERP")
    with pytest.raises(ValueError):
        parse_instrument("garbage")


def test_book_top_is_derived_from_prices_not_array_order():
    """A stale level must never be served as the touch, whatever order it arrives in."""
    bid, ask = best_levels(
        {"bids": [["100", "1"], ["200", "2"]], "asks": [["300", "2"], ["250", "1"]]}
    )
    assert bid == (200.0, 2.0)
    assert ask == (250.0, 1.0)


def test_fixture_prices_the_put_in_bps_of_notional():
    snapshots, spot, observed = load_fixture("derive_v2_ws_scan_1.json")
    rows = {r.instrument: r for r in rows_from_snapshots(snapshots, observed)}
    put = rows["ETH-20270326-2400-P"]
    assert put.ask == 243.6
    assert put.premium_bps(spot) == pytest.approx(905.92, abs=0.05)
    # without catalogue times the expiry is read from the name (00:00 UTC); the venue's real
    # expiry is 08:00 UTC, so this under-states the tenor by the time-of-day — conservative.
    assert put.tenor_days == pytest.approx(180.50, abs=0.02)
    assert put.cost_per_day_bps(spot) == pytest.approx(5.02, abs=0.02)


def test_catalogue_expiry_times_override_the_name():
    """The adapter passes the venue's real expiry instants; the tenor must use them."""
    snapshots, spot, observed = load_fixture("derive_v2_ws_scan_1.json")
    expiry_times = {"ETH-20270326-2400-P": datetime(2027, 3, 26, 8, 0, tzinfo=timezone.utc)}
    rows = rows_from_snapshots(snapshots, observed, expiry_times)
    put = next(r for r in rows if r.instrument == "ETH-20270326-2400-P")
    assert put.tenor_days == pytest.approx(180.83, abs=0.02)


def test_selection_takes_the_strike_nearest_ten_percent_below_spot():
    snapshots, spot, observed = load_fixture("derive_v2_ws_scan_1.json")
    rows = rows_from_snapshots(snapshots, observed)
    sel = select_protective_put(rows, POLICY, spot, min_tradable_size=0.1)
    assert sel is not None
    # target = 2688.985 * 0.9 = 2420.09 -> the 2400 strike is nearest of the quoted ladder
    assert sel["instrument"] == "ETH-20270326-2400-P"
    assert sel["premium_bps"] == pytest.approx(905.92, abs=0.05)
    assert sel["basis"] == "ask"
    assert sel["tenor_days"] >= 180


def test_dust_and_one_sided_quotes_do_not_qualify():
    """The 271d ladder carries a single 0.01-size ask — not a market. It must never win."""
    snapshots, spot, observed = load_fixture("derive_v2_ws_scan_1.json")
    rows = rows_from_snapshots(snapshots, observed)
    sel = select_protective_put(rows, {"min_tenor_days": 250, "strike_otm_pct": 10.0}, spot,
                                min_tradable_size=0.1)
    assert sel is None


def test_no_two_sided_long_dated_quote_is_none():
    snapshots, spot, observed = load_fixture("derive_v2_ws_scan_1.json")
    rows = rows_from_snapshots(snapshots, observed)
    sel = select_protective_put(rows, {"min_tenor_days": 300, "strike_otm_pct": 10.0}, spot,
                                min_tradable_size=0.1)
    assert sel is None


def test_the_two_recorded_scans_agree():
    """Evidence discipline: two real observations; a one-off quote must not become a fact."""
    s1, spot1, _ = load_fixture("derive_v2_ws_scan_1.json")
    s2, spot2, _ = load_fixture("derive_v2_ws_scan_2.json")
    assert set(s1) == set(s2)
    for name in s1:
        if name.startswith("ETH-20270326"):
            assert s1[name] == s2[name], f"{name} moved between scans — re-tell the story"
    assert abs(spot1 - spot2) < 5.0


def test_pump_ignores_channels_outside_the_accepted_set():
    """A live perp update must not leak into the option collection (or complete it early)."""
    import importlib
    import time

    dm = importlib.import_module("derive_quotes.__main__")

    class FakeWS:
        def __init__(self, frames):
            self.frames = list(frames)

        def recv(self, timeout=None):
            if not self.frames:
                raise TimeoutError()
            return self.frames.pop(0)

    perp_frame = json.dumps({
        "method": "subscription",
        "params": {"channel": "orderbook.ETH-PERP.1.10",
                   "data": {"bids": [["2688", "1"]], "asks": [["2689", "1"]]}},
    })
    option_frame = json.dumps({
        "method": "subscription",
        "params": {"channel": "orderbook.ETH-20270326-2400-P.1.10",
                   "data": {"bids": [["229.9", "13.4"]], "asks": [["243.6", "13.4"]]}},
    })
    books: dict = {}
    dm._pump(FakeWS([perp_frame, option_frame, perp_frame]),
             books, time.monotonic() + 1.0, want=1, accept={"ETH-20270326-2400-P"})
    assert list(books) == ["ETH-20270326-2400-P"]


def test_artifact_carries_the_ladder_the_selection_and_provenance():
    snapshots, spot, observed = load_fixture("derive_v2_ws_scan_1.json")
    rows = rows_from_snapshots(snapshots, observed)
    sel = select_protective_put(rows, POLICY, spot, min_tradable_size=0.1)
    art = build_artifact(
        venue="derive", deployment="v2 · Derive Chain (production)", endpoint="wss://x",
        asset="ETH", fetched_at=observed, spot=spot, spot_source="ETH-PERP mid",
        policy_inputs={"min_tenor_days": 180}, rows=rows, selected=sel, notes=["n"],
    )
    assert art["selected"]["instrument"] == "ETH-20270326-2400-P"
    assert art["read_only"] is True
    ladder = {r["instrument"] for r in art["ladder"]}
    assert "ETH-20270924-3000-P" in ladder   # the empty 362d board is still evidence
    assert art["fetched_at"].endswith("Z")


# --- v3 deployment: the port's fixtures (recorded 2026-09-29) ----------------------------------

V3_TESTNET = "derive_v3_testnet_ws_scan_1.json"
V3_PROD = "derive_v3_prod_ws_scan_1.json"


def test_catalogue_normalizes_v2_and_v3_shapes():
    """v2: a bare list. v3: {instruments, pagination} under result. One shape downstream."""
    assert catalogue_instruments({"result": [{"instrument_name": "ETH-1"}]}) == \
        [{"instrument_name": "ETH-1"}]
    assert catalogue_instruments({"result": {"instruments": [{"instrument_name": "ETH-2"}],
                                             "pagination": {"num_pages": 1}}}) == \
        [{"instrument_name": "ETH-2"}]
    assert catalogue_instruments({}) == []


def test_pick_deployment_ladder():
    """A buyable put beats a mere market; a market beats an empty-but-functional venue; ties keep
    the caller's preference order (v3 first in production)."""
    put_beats_market = [{"key": "v3", "selected": None, "genuine": 2, "spot_ok": True},
                        {"key": "v2", "selected": {"instrument": "X"}, "genuine": 3, "spot_ok": True}]
    assert pick_deployment(put_beats_market) == 1
    both_selected = [{"key": "v3", "selected": {"instrument": "A"}, "genuine": 0, "spot_ok": True},
                     {"key": "v2", "selected": {"instrument": "B"}, "genuine": 0, "spot_ok": True}]
    assert pick_deployment(both_selected) == 0
    only_functional = [{"key": "v3", "selected": None, "genuine": 0, "spot_ok": False},
                       {"key": "v2", "selected": None, "genuine": 0, "spot_ok": True}]
    assert pick_deployment(only_functional) == 1
    assert pick_deployment([{"key": "v3", "selected": None, "genuine": 0, "spot_ok": False}]) is None


def test_v3_testnet_fixture_prices_the_put_in_bps_of_notional():
    snapshots, spot, observed = load_fixture(V3_TESTNET)
    rows = {r.instrument: r for r in rows_from_snapshots(snapshots, observed)}
    put = rows["ETH-20270326-3000-P"]
    assert put.ask == 544.0
    assert put.premium_bps(spot) == pytest.approx(2004.2, abs=0.5)


def test_v3_selection_takes_lowest_cost_per_day_of_cover():
    """360d at ~1306 bps beats 269d at ~1084 bps on the honest comparator (cost/day)."""
    snapshots, spot, observed = load_fixture(V3_TESTNET)
    expiry_times = {"ETH-20270924-2400-P": datetime(2027, 9, 24, 8, 0, tzinfo=timezone.utc)}
    rows = rows_from_snapshots(snapshots, observed, expiry_times)
    sel = select_protective_put(rows, POLICY, spot, min_tradable_size=0.1)
    assert sel is not None
    assert sel["instrument"] == "ETH-20270924-2400-P"
    assert sel["premium_bps"] == pytest.approx(1305.7, abs=0.5)
    assert sel["tenor_days"] == pytest.approx(360.0, abs=0.05)
    assert sel["basis"] == "ask"


def test_v3_prod_fixture_is_staged_empty_and_yields_nothing():
    """The staged deployment serves empty books — that is a measurement ('unquoted'), not an
    error, and it must never qualify for selection."""
    snapshots, spot, observed = load_fixture(V3_PROD)
    rows = rows_from_snapshots(snapshots, observed)
    assert rows and all(not r.two_sided for r in rows)
    assert spot is None  # no perp book on the staged venue either
    sel = select_protective_put(rows, POLICY, 2714.23, min_tradable_size=0.1)
    assert sel is None
