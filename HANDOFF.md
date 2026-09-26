# HANDOFF — options

AUTO-COMMIT: safe — read-only; no keys, no credentials, nothing signed. The only network access
is a public unauthenticated feed (the quotes adapter and the venue probe).

**What it is:** protective puts for your own positions (spot + margin, across accounts), decided
here and executed nowhere. Independent of any bot.

## Status

Green, and **priced from a live venue** (2026-09-26). All tests pass. `data/coverage.json` is
published and real; `data/quotes.json` is the measurement behind it (from `apps/derive_quotes`).

**The venue exists:** Derive **v2** (`api.lyra.finance`, Derive Chain) trades live today —
ETH-PERP settles continuously on-chain and the 181d put ladder is quoted two-sided. Derive **v3**
(`api.derive.xyz`) is the pre-launch zk stack with an empty production book; it stays unlisted
until it trades. Without fresh quotes the plan still reports **no venue available** — correct,
and the point of the design.

## Decisions already made (do not re-derive)

- **Options only.** A perp-short mechanism was built and then deliberately removed: it belongs to
  the MM bot's world, and hedging a swing position with a perp short is a different trade (caps
  upside, carries liquidation risk), not a cheaper version of the same one. Do not re-add it here
  without an explicit ask.
- **The mechanism IS the venue.** Derive and Lighter options sell the same instrument; they differ
  in price and availability. So the comparison is venue-vs-venue on premium in bps of protected
  notional, and `name` is the venue.
- **Cover must outlast the position.** `min_tenor_days` (180) refuses puts too short to protect the
  thing they are bought for. This is what rules out rolling short-dated puts, which look cheap per
  contract and are expensive per day of cover.
- **Cost/day is in the artifact** so a pricier long-dated put can be compared honestly against a
  cheap short one.
- **Exposure is an input, not a fetch.** `--exposure-usd` (+ `--label`). Your positions span
  accounts; sourcing that number is the caller's job, and a decision layer that fetches its own
  inputs cannot be tested.
- **Gate closed by default; live-but-unquoted is still unavailable.** Both asserted by tests.
- **The venue is v2, not v3 — for now.** `api.lyra.finance` (v2 · Derive Chain) is live and
  quoted; `api.derive.xyz/v3` is pre-launch with an empty production book and stays unlisted
  until its book fills. Re-gate with `ops/derive_book_probe.mjs`; a non-empty production book is
  the trigger to re-point the adapter.
- **The adapter measures; it never trades.** Buying remains a human action — this repo holds no
  keys, and that is a design, not a gap.
- **Premiums come from measurements, never from policy.** policy's `premium_bps` is a placeholder
  that keeps a venue unquoted; only a fresh `data/quotes.json` can price a plan, and it expires
  (`quotes_max_age_hours`).
- **Rules 1–3 copied from `lighter-core`** with its AST-based boundary guard.

## Blockers

- **Execution is unexercised.** No order has been placed from this stack — the repo holds no keys
  and never trades, by design. Buying the put is a human action on the venue's own interface.
- **Only the 181d series is quoted** (272d and 363d empty at measurement, 2026-09-26). Re-run the
  adapter before acting on any plan; it re-measures rather than assumes.
- **Derive v3 is pre-launch.** Production orderbook verified empty on 2026-09-26 (testnet has a
  book as positive control). Re-check with `ops/derive_book_probe.mjs`. **A non-empty production
  book is the trigger to re-point the adapter at v3.**
- **No backup yet:** `pAulseperformance/options` does not exist on GitHub; the remote is wired but
  unpushable until it does.

## Open items

1. **Re-measure before acting on any plan:** `PYTHONPATH=apps uv run --with websockets --with
   pyyaml --no-project python -m derive_quotes`, then run coverage with `--quotes data/quotes.json`.
2. **Rail the dashboard** on `data/coverage.json` (integration point #1 in the README).
3. **Re-run the v3 venue gate** (`ops/derive_book_probe.mjs`) when Derive announces v3 mainnet.
4. **Create the GitHub remote** `pAulseperformance/options` (private, exact name — the remote is
   already wired there) so the repo has a backup.

## Provenance

- Venue research: `brain/inbox/2026-09-26/20260926T034500-f8c5b239-derive-v3-options-integration-research.md`
- Pattern: `lighter-core` (`apps/` vs `packages/`, data-not-code boundaries).
- Naming/scope correction (2026-09-26): originally `hedge-core`, framed as joining the MM bot's
  hedge leg. Wrong on both counts — this is options insurance for your own positions.
- Book scans + fixtures: `tests/fixtures/derive_v2_ws_scan_{1,2}.json` (2026-09-26 12:03 / 12:05 UTC).
- v2↔v3 endpoint mapping: `docs.derive.xyz/migrating/breaking-changes.md` — v2 = `api.lyra.finance`,
  v3 = `api.derive.xyz/v3`.
- Pricing sanity reference: Deribit public API (book summary + index), 2026-09-26 12:05 UTC.

---

# 2026-09-26 — the venue was live all along (v2, not v3); the plan is priced from a real book

The v3 gate answered the wrong question: v3's production book is still empty, but the venue that
trades TODAY is **v2** — `api.lyra.finance`, on Derive Chain. Verified live, read-only:

| check | result |
|---|---|
| ETH-PERP trades | 100 trades over 1.6 h, newest ~2 min old, `tx_status: settled`, real `tx_hash` |
| on-chain proof | `0x6184aec88a1433af49a1ae27cd9199171032c4c189d11a2974d3b099f0889d64` resolves on `explorer.derive.xyz` (Derive Chain) |
| 181d put ladder | two-sided at 8/8 strikes probed (1800–3000), uniform size 13.4, spreads ~2–3% of premium |
| 272d / 363d ladders | **empty** (one 0.01-size dust ask at 272d/2500 — not a market) |
| pricing sanity | asks ≈ Deribit `26MAR27` asks +1.3–5.5%; Deribit spot 2687.28 vs Derive 2688.7–2689.0 |
| stability | two scans 2.5 min apart, identical quotes |

Measured board (2026-09-26 12:03–12:06 UTC, spot ≈ 2689; ask = what a buyer pays):

| strike | ask | bps of notional | ≈ bps/day (181d) |
|---|---|---|---|
| 1800 | 77.4 | 288 | 1.6 |
| 2000 | 116.2 | 432 | 2.4 |
| 2200 | 170.8 | 635 | 3.5 |
| 2400 | 243.6 | 906 | 5.0 |
| 2500 | 287.0 | 1067 | 5.9 |
| 2600 | 335.2 | 1247 | 6.9 |
| 2800 | 444.8 | 1654 | 9.1 |
| 3000 | 570.3 | 2121 | 11.7 |

**What changed in the repo** (this session):

- `apps/derive_quotes/` — the first venue adapter: a read-only reader of the public book. Pure
  logic (parse / price / select) is tested offline against two recorded scans; the socket layer
  writes `data/quotes.json` (schema: `packages/options_contracts/quotes.schema.json`), carrying
  the whole observed ladder plus the policy-selected put.
- `options_core.overlay_quotes()` — a plan may use a measurement only while fresh
  (`quotes_max_age_hours`); stale, unreadable, or missing quotes leave the venue exactly as policy
  wrote it. The placeholder can never stand in for a measurement.
- `coverage_cli --quotes` — prices the plan and records provenance in a top-level `quotes` block.
- `config/policy.yml` — `derive` is `live: true` (evidence above); new keys `asset`,
  `strike_otm_pct` (10% OTM by choice — you self-insure the first 10%), `quotes_max_age_hours`.
  The `premium_bps: 0.0` is a placeholder **by design**; only a fresh quotes file prices a plan.

**Not done, deliberately:** no order has been placed anywhere (no keys, by design), and 272d/363d
coverage awaits a quoted book — the adapter re-measures rather than guessing. v3 stays unlisted;
when its mainnet launches, `ops/derive_book_probe.mjs` is the gate and re-pointing `ENDPOINTS` in
`apps/derive_quotes/__main__.py` is the adapter change.
