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
    # The venue's own total covers the STABLECOIN balance + perp PnL; the account ALSO holds a
    # little ETH, which is its own value and is added on top (see the holdings test below).
    assert a["venue_total_usd"] == pytest.approx(1104.50)
    assert a["usd_total"] == pytest.approx(round(1104.497139 + 0.0001 * 2700.0, 2))
    assert a["usd_total_source"] == "venue+holdings"
    lit = [p for p in a["positions"] if p["kind"] == "perp"][0]
    assert (lit["side"], lit["amount"]) == ("long", pytest.approx(1.57))
    assert [p for p in a["positions"] if p["kind"] == "cash"][0]["asset"] == "USDC"
    assert any("1 of 2 markets nonzero" in n for n in a["notes"])
    # With open positions the venue total and the notional are different quantities by
    # construction — the note explains the relationship instead of "reconciling" it.
    assert any("collateral + unrealized PnL" in n for n in a["notes"])


def test_non_stable_holdings_are_value_the_venue_total_never_reports():
    """Measured on the real accounts: total_asset_value = stablecoin balance + perp uPnL, exactly.

    So an ETH or LIT holding is EXTRA value — the staking pool reports collateral 0 while holding
    113.5M LIT. Publishing the venue's figure alone hid six figures of a real position.
    """
    raw = {
        "account_index": 728660, "l1_address": WALLET,
        "total_asset_value": "20898.076373999997", "collateral": "22839.335445",
        "available_balance": "12923.75",
        "assets": [
            {"symbol": "ETH", "balance": "0.00000000", "locked_balance": "0.00000000",
             "margin_balance": "41.16107287", "margin_mode": "enabled"},
            {"symbol": "LIT", "balance": "4563.88554199", "locked_balance": "4563.88000000",
             "margin_balance": "0.00000000", "margin_mode": "disabled"},
            {"symbol": "USDC", "balance": "0.000000", "locked_balance": "1744.165700",
             "margin_balance": "22839.33544582509", "margin_mode": "enabled"},
            {"symbol": "UNI", "balance": "0.00000000", "locked_balance": "30.48000000",
             "margin_balance": "0.00000000", "margin_mode": "disabled"},
        ],
        "positions": [{"symbol": "LIT", "sign": 1, "position": "4289.32",
                       "position_value": "18774.782572", "unrealized_pnl": "-817.397793",
                       "avg_entry_price": "4.0000"}],
    }
    a = core.normalize_lighter(raw, 728660, "https://mainnet.zklighter.elliot.ai",
                               "lighter-mainnet", "Lighter · mainnet · main account", RATES)
    eth = [p for p in a["positions"] if p["asset"] == "ETH"][0]
    lit_bal = [p for p in a["positions"] if p["asset"] == "LIT" and p["kind"] == "balance"][0]
    assert eth["usd"] == pytest.approx(round(41.16107287 * 2700.0, 2))
    assert a["usd_total"] == pytest.approx(round(20898.076374 + eth["usd"] + lit_bal["usd"], 2))
    assert a["usd_total"] > a["venue_total_usd"] * 5         # the venue reported a fifth of it
    assert a["usd_total_source"] == "venue+holdings"
    assert any("covers the stablecoin balance and perp PnL only" in n for n in a["notes"])
    # A holding that only ever shows in locked_balance is NOT read — and says so.
    assert any("UNI appears only in the venue's locked_balance" in n for n in a["notes"])


def test_the_venue_gap_check_also_runs_when_positions_are_open():
    """It used to be skipped for any account with perps, so a mis-read figure could hide there."""
    raw = {
        "l1_address": WALLET, "total_asset_value": "900.0", "collateral": "1000.0",
        "available_balance": "1000.0",
        "assets": [{"symbol": "USDC", "balance": "0.0", "margin_balance": "1000.0"}],
        "positions": [{"symbol": "ETH", "sign": 1, "position": "1.0",
                       "position_value": "2700.0", "unrealized_pnl": "0.0"}],
    }
    a = core.normalize_lighter(raw, 1, "https://x", "lighter-mainnet", "L", RATES)
    assert any("vs venue total $900.00" in n for n in a["notes"])


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


def pool_account(operator_shares: int = 42219319, total_shares: int = 68985816,
                 label: str = "Lighter · pool · Christ is King") -> dict:
    """A public pool under the same L1 address — an account whose equity is NOT all the owner's."""
    raw = {
        "account_index": 281474976498676, "l1_address": WALLET, "account_type": 2,
        "name": "Christ is King",
        "description": "A veteran operated discretionary vault.",
        "total_asset_value": "92761.851378", "collateral": "93405.253959",
        "available_balance": "79765.570414",
        "pool_info": {
            "operator_fee": "10.0000", "total_shares": total_shares,
            "operator_shares": operator_shares, "annual_percentage_yield": 131.8144727858707,
            "sharpe_ratio": 4.885713132260648,
            "share_prices": [{"timestamp": 1, "share_price": 0.0013563525266991117}],
        },
        "assets": [{"symbol": "USDC", "balance": "0.000000", "margin_balance": "93405.253959"}],
        "positions": [{"symbol": "LIT", "sign": 1, "position": "3538.77",
                       "avg_entry_price": "4.0000", "position_value": "15608.098962",
                       "unrealized_pnl": "144.9", "liquidation_price": "0"}],
    }
    return core.normalize_lighter(raw, 281474976498676, "https://mainnet.zklighter.elliot.ai",
                                  "lighter-mainnet", label, RATES, "pool")


def test_a_plain_account_is_entirely_the_owners_and_declares_no_delegation():
    a = lighter_account()
    assert a["owned_fraction"] == 1.0 and a["pool"] is None and a["role"] == "book"
    assert "delegated_usd" not in core.totals_of([a])


def test_a_pool_is_counted_at_the_operators_share_never_its_full_equity():
    """The account reports its real size; the TOTALS report his slice, and the gap is named."""
    a = pool_account()
    assert a["role"] == "pool" and a["pool"]["name"] == "Christ is King"
    assert a["usd_total"] == pytest.approx(92761.85)          # the pool's own equity, untouched
    f = 42219319 / 68985816
    assert a["owned_fraction"] == pytest.approx(round(f * 100, 4) / 100)
    t = core.totals_of([a])
    assert t["usd_total"] == pytest.approx(round(92761.85 * f, 2))        # what is HIS…
    assert t["usd_total_gross"] == pytest.approx(92761.85)                # …vs the pool's size
    assert t["delegated_usd"] == pytest.approx(round(92761.85 * (1 - f), 2))
    assert any("operator holds" in n and "depositors" in n for n in a["notes"])


def test_pool_legs_reach_exposure_at_the_share_and_the_note_says_which():
    a = pool_account()
    exp = core.exposure_of([a], RATES)
    lit = [c for c in exp["components"] if c["asset"] == "LIT"][0]
    assert lit["net_amount"] == pytest.approx(3538.77 * a["owned_fraction"])
    assert any("operator's share" in n and "not your exposure" in n for n in exp["notes"])


def test_a_pool_with_no_share_table_is_flagged_rather_than_assumed():
    a = pool_account(operator_shares=0, total_shares=0)
    assert a["owned_fraction"] == 1.0                          # nothing better is known
    assert a["pool"]["operator_share_pct"] is None
    assert any("OVERSTATES" in n for n in a["notes"])          # so the artifact says so


def test_the_artifact_names_the_pool_and_the_depositors_slice():
    art = bank([l1_account(), pool_account()])
    assert art["complete"] is True
    named = [n for n in art["notes"] if "other people's capital" in n]
    assert named and "Christ is King" in named[0] and "depositors'" in named[0]


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
                pool_account(), core.placeholder_derive()])
    schema = json.loads((ROOT / "packages" / "options_contracts" / "portfolio.schema.json")
                        .read_text())
    jsonschema.validate(art, schema)


def test_a_lit_stake_is_a_row_of_its_own_and_counts_toward_the_account():
    """Staked LIT is custody the POOL holds, so it is in no balance and in no venue total."""
    raw = {
        "account_index": 728660, "l1_address": WALLET,
        "total_asset_value": "21678.50", "collateral": "22838.518828",
        "available_balance": "13156.16",
        "assets": [{"symbol": "USDC", "balance": "0.000000", "locked_balance": "1744.165700",
                    "margin_balance": "22838.518828380131"}],
        "positions": [{"symbol": "LIT", "sign": 1, "position": "4289.32",
                       "position_value": "18774.782572", "unrealized_pnl": "-1160.02",
                       "avg_entry_price": "4.5677"}],
        "shares": [{"public_pool_index": 281474976624800, "shares_amount": 381277497,
                    "principal_amount": "3955.55998641"}],
    }
    a = core.normalize_lighter(raw, 728660, "https://mainnet.zklighter.elliot.ai",
                               "lighter-mainnet", "L", RATES)
    staked = [p for p in a["positions"] if p["kind"] == "staked"]
    assert len(staked) == 1
    assert staked[0]["asset"] == "LIT"
    assert staked[0]["amount"] == pytest.approx(3955.55998641)
    assert staked[0]["price_usd"] == pytest.approx(18774.782572 / 4289.32)   # the venue's own mark
    assert staked[0]["usd"] == pytest.approx(round(3955.55998641 * staked[0]["price_usd"], 2))
    # The stake sits OUTSIDE the venue's own total, so it belongs in the VALUE — never a footnote
    # with the money missing from the number above it.
    assert a["usd_total"] == pytest.approx(round(21678.50 + staked[0]["usd"], 2))
    assert a["usd_total"] > a["venue_total_usd"]


def test_an_unpriceable_stake_is_named_and_skipped_rather_than_poisoning_the_artifact():
    raw = {
        "account_index": 728660, "l1_address": WALLET,
        "total_asset_value": "100.0", "collateral": "100.0",
        "assets": [{"symbol": "USDC", "balance": "0.000000", "margin_balance": "100.0"}],
        "positions": [],
        "shares": [{"public_pool_index": 281474976624800, "principal_amount": "42.5"}],
    }
    a = core.normalize_lighter(raw, 728660, "https://x", "lighter-mainnet", "L", {"ETH": 2700.0})
    assert a["ok"] is True                     # a missing price must NOT fail the account
    assert [p for p in a["positions"] if p["kind"] == "staked"] == []
    assert any("no LIT price on this account" in n for n in a["notes"])
