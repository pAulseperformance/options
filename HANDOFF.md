# HANDOFF — options

AUTO-COMMIT: safe — read-only decision layer, no keys, no credentials, no network calls.

**What it is:** protective puts for your own positions (spot + margin, across accounts), decided
here and executed nowhere. Independent of any bot.

## Status

Scaffolded and green (2026-09-26). All tests pass. `data/coverage.json` is published and real.

Currently reports **no venue available** — correct, and the point of the design.

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
- **Rules 1–3 copied from `lighter-core`** with its AST-based boundary guard.

## Blockers

- **No executable options venue.** Derive V3 mainnet pre-launch (production orderbook verified
  empty on 2026-09-26 via the public WS feed, with testnet as a positive control — testnet has a
  book, production does not). Lighter options do not exist. Re-check with
  `ops/derive_book_probe.mjs`. **A non-empty production book is the trigger to build the first
  adapter.**

## Open items

1. **`premium_bps` is `0.0` on every venue** and `tenor_days: 270` is a guess at the quarterly
   ladder. Placeholders, not measurements — harmless only because every venue is gated off. Fill
   from a real quote before acting on any plan.
2. **Rail the dashboard** on `data/coverage.json` (integration point #1 in the README).
3. **Re-run the venue gate** when Derive announces V3 mainnet.
4. **Unverified:** whether Derive V2 (`api.lyra.finance`) has live long-dated liquidity. It serves
   the same instrument catalog with real fees (1bp maker / 3bp taker) but its public data surface
   did not expose depth. If V2 is live, it may be tradeable today.

## Provenance

- Venue research: `brain/inbox/2026-09-26/20260926T034500-f8c5b239-derive-v3-options-integration-research.md`
- Pattern: `lighter-core` (`apps/` vs `packages/`, data-not-code boundaries).
- Naming/scope correction (2026-09-26): originally `hedge-core`, framed as joining the MM bot's
  hedge leg. Wrong on both counts — this is options insurance for your own positions.
