# HANDOFF — hedge-core

AUTO-COMMIT: safe — read-only decision layer, no keys, no credentials, no network calls.

## Status

**Scaffolded and green (2026-09-26).** 19 tests pass. `data/coverage.json` is published and real.

The decision engine works and is venue-agnostic. It currently selects **`perp_short`** (the
mechanism that already exists) and reports `long_put` as unavailable with the venue's own reason.

## What this repo is

The insurance leg expressed as a **decision** rather than an exchange integration. Given spot
exposure + a policy + a set of mechanisms, it answers: *which mechanism, how much, at what cost,
and if none — why not.*

The load-bearing rule, enforced by tests: **a plan never silently covers nothing.** If nothing can
execute, `uncovered_usd` equals full exposure and `notes` names every unavailable mechanism and its
reason. A hedge that reports success while covering nothing is the failure this repo exists to stop.

## Decisions already made (do not re-derive)

- **Two mechanisms, one interface.** Perp short (funding carry, caps upside, liquidation risk) vs
  long put (premium, keeps upside, no liquidation). Costed in one unit — bps of notional — so they
  are comparable. That comparison is the product.
- **Exposure is an input, not a fetch.** The CLI takes `--exposure-usd`. Sourcing exposure is each
  consumer's job; a decision layer that fetches its own inputs cannot be tested.
- **No venue adapters yet.** An adapter that cannot execute is scaffolding that reads as progress.
  Adapters land when their venue does.
- **`venue_live` defaults to False for options.** The gate is the default, not an opt-out, and a
  test asserts it — so flipping it is always a deliberate edit.
- **Rules 1–3 copied from `lighter-core`**, including the AST-based boundary test. Same reasoning:
  a convention nobody tests decays.

## Blockers

- **No executable options venue.** Derive V3 mainnet is pre-launch; production orderbook verified
  empty on every instrument on 2026-09-26 via the public WS feed (with testnet as a positive
  control — testnet has a book, production does not). Re-check with `ops/derive_book_probe.mjs`.
- `long_put.premium_bps` is `0.0` in config and must be set **from a real quote, never guessed.**
  Until it is, the put's cost comparison is not meaningful — which is fine today only because the
  mechanism is gated off anyway.

## Next actions

1. **Wire the dashboard** to render `data/coverage.json` (integration point #1 in the README).
2. **Point the MM bot's hedge leg** at `plan.legs[].notional_usd` so one number governs both
   instead of a hardcoded ratio.
3. **Re-run the venue gate** when Derive announces V3 mainnet. A non-empty production book is the
   trigger to build the first real adapter.
4. Refresh `funding_bps_per_day` from a live funding read before acting on any plan — it is `0.0`
   in config and that is a placeholder, not a measurement.

## Provenance

- Sizing and triggers: `trading-dashboard/research/hedge_sizing_2026-09-25.md` (kanban `t_61054c5b`,
  done) — 50% ratio, short 0.90 ETH, first liquidation +37.2%, funding +$0.70/day, trim on a daily
  close above $3,200.
- Venue research: `brain/inbox/2026-09-26/20260926T034500-f8c5b239-derive-v3-options-integration-research.md`
- Pattern: `lighter-core` (`apps/` vs `packages/`, data-not-code boundaries, lego studs).
