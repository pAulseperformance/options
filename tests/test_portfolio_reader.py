"""portfolio_reader: the rules that make "entire portfolio" honest — no network involved.

The interesting arithmetic is netting: a LIT long against a LIT short is $0.00 of exposure
however large both legs are; stablecoins are value but not exposure; and an account that
failed must poison `complete` instead of quietly contributing zero.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "apps"))

from portfolio_reader import portfolio as core  # noqa: E402

WALLET = "0xe83ECEe6ad078a64F641BdA924c841Fd0F7D58f9"
RATES = {"ETH": 2700.0, "BTC": 60000.0}
ETH_HELD = 1.8011419346783952


def l1_account(eth: float = ETH_HELD) -> dict:
    return core.normalize_l1(WALLET, {"ETH": eth, "WETH": 0.0, "USDC": 0.0, "USDT": 0.0,
                                      "WBTC": 0.0}, RATES, ["WETH", "USDC", "USDT", "WBTC"])


def lighter_account(sign: int = 1, label: str = "Lighter · mainnet",
                    eth_dust: str = "0.00010000") -> dict:
    raw = {
        "account_index": 281474976483780, "l1_address": WALLET,
        "total_asset_value": "1104.497139", "collateral": "1106.603906",
        "available_balance": "1078.02",
        "assets": [
            {"symbol": "ETH", "balance": "0.00000000", "margin_balance": eth_dust},
            {"symbol": "USDC", "balance": "0.000000", "margin_balance": "1106.603906329141"},
        ],
        "positions": [
            {"symbol": "ETH", "sign": 1, "position": "0.0000"},
            {"symbol": "LIT", "sign": sign, "position": "1.57", "avg_entry_price": "4.8462",
             "position_value": "7.550444", "unrealized_pnl": "-0.058058",
             "liquidation_price": "0"},
        ],
    }
    return core.normalize_lighter(raw, 281474976483780, "https://x", "lighter-mainnet", label,
                                  RATES)


def bank(accounts, at: str = "2026-09-27T00:00:00+00:00") -> dict:
    return core.build_artifact(accounts,
                               {"source": "test", "fetched_at": at, "rates": RATES}, at)


def test_amounts_keep_full_precision_and_tokens_checked_are_recorded():
    a = l1_account()
    assert a["ok"] is True
    eth = [p for p in a["positions"] if p["asset"] == "ETH"][0]
    assert eth["amount"] == pytest.approx(ETH_HELD)          # 1.8 rounds; the artifact must not
    assert eth["usd"] == pytest.approx(round(ETH_HELD * 2700.0, 2))
    assert any("WETH, USDC, USDT, WBTC checked" in n for n in a["notes"])


def test_lighter_position_signs_and_the_cash_split():
    a = lighter_account()
    assert a["usd_total"] == pytest.approx(1104.50)          # the venue's own number
    assert a["usd_total_source"] == "venue"
    lit = [p for p in a["positions"] if p["kind"] == "perp"][0]
    assert (lit["side"], lit["amount"]) == ("long", pytest.approx(1.57))
    assert [p for p in a["positions"] if p["kind"] == "cash"][0]["asset"] == "USDC"
    assert any("1 of 2 markets nonzero" in n for n in a["notes"])
    # With open positions the venue total and the notional are different quantities by
    # construction — the note explains the relationship instead of "reconciling" it.
    assert any("collateral + unrealized PnL" in n for n in a["notes"])


def test_a_balances_only_account_reports_a_material_gap_against_the_venue():
    raw = {
        "l1_address": WALLET, "total_asset_value": "900.0", "collateral": "1000.0",
        "available_balance": "1000.0",
        "assets": [{"symbol": "USDC", "balance": "0.0", "margin_balance": "1000.0"}],
        "positions": [],
    }
    a = core.normalize_lighter(raw, 1, "https://x", "lighter-mainnet", "L", RATES)
    assert a["usd_total"] == pytest.approx(900.0)            # the venue's number wins…
    assert any("vs venue total $900.00" in n for n in a["notes"])   # …and the gap is said


def test_the_offsetting_pair_carries_zero_exposure():
    exp = core.exposure_of([l1_account(), lighter_account(1, "main"), lighter_account(-1, "rh")],
                           RATES)
    expected_eth = (ETH_HELD + 0.0001 + 0.0001) * 2700.0
    eth = [c for c in exp["components"] if c["asset"] == "ETH"][0]
    assert eth["net_amount"] == pytest.approx(ETH_HELD + 0.0002, abs=1e-9)
    assert exp["usd"] == pytest.approx(round(expected_eth, 2))
    lit = [c for c in exp["components"] if c["asset"] == "LIT"][0]
    assert lit["usd"] == 0.0
    assert any("net 0" in n for n in exp["notes"])


def test_a_net_short_is_counted_as_zero_and_says_why():
    exp = core.exposure_of([lighter_account(-1, eth_dust="0.00000000")], RATES)
    lit = [c for c in exp["components"] if c["asset"] == "LIT"][0]
    assert lit["net_amount"] < 0
    assert lit["note"].startswith("net short")
    assert exp["usd"] == 0.0


def test_stables_are_value_but_never_exposure():
    totals = core.totals_of([lighter_account()])
    assert totals["cash_usd"] == pytest.approx(1106.60)
    exp = core.exposure_of([lighter_account()], RATES)
    assert all(c["asset"] != "USDC" for c in exp["components"])


def test_a_failed_account_poisons_complete_and_is_never_a_zero():
    art = bank([l1_account(), core.failed_account("lighter-mainnet", "Lighter · mainnet",
                                                  "TimeoutError: read timed out")])
    assert art["complete"] is False
    assert any(n.startswith("INCOMPLETE") for n in art["notes"])
    assert any("unreadable — excluded from exposure" in n for n in art["exposure"]["notes"])


def test_the_derive_placeholder_never_counts_as_an_account():
    art = bank([l1_account(), core.placeholder_derive()])
    assert art["complete"] is True
    assert art["totals"]["displayed_accounts"] == 1
    assert art["totals"]["usd_total"] == pytest.approx(round(ETH_HELD * 2700.0, 2))


def test_a_nonzero_holding_without_a_price_fails_the_account_honestly():
    a = core.normalize_l1(WALLET, {"ETH": 1.0, "WETH": 0.0, "USDC": 0.0, "USDT": 0.0,
                                   "WBTC": 0.0}, {}, ["WETH", "USDC", "USDT", "WBTC"])
    assert a["ok"] is False
    assert "no price for ETH" in a["error"]
    assert a["usd_total"] is None


def test_num_parses_venue_strings_and_rejects_junk():
    assert core.num("1106.603906329141") == pytest.approx(1106.603906329141)
    assert core.num("junk") is None
    assert core.num(None, 0.0) == 0.0


def test_a_built_artifact_matches_the_published_schema():
    jsonschema = pytest.importorskip("jsonschema")
    art = bank([l1_account(), lighter_account(1), lighter_account(-1, "Lighter · RH chain"),
                core.placeholder_derive()])
    schema = json.loads((ROOT / "packages" / "options_contracts" / "portfolio.schema.json")
                        .read_text())
    jsonschema.validate(art, schema)
