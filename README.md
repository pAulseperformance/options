# options

**Protective puts for your own positions, as a decision rather than an order.**

Read-only. No keys. This repo recommends insurance; it never buys it.

Covers spot and margin exposure across accounts, independent of any bot. The market maker's book
is the market maker's business.

## Why this exists

A long-dated put is the right shape of insurance for a swing position: you pay once, the maximum
loss is the premium you already paid, and you keep the upside. A perp short does the opposite —
it caps your upside and carries liquidation risk — which is why hedging a swing position with one
is a different trade, not a cheaper version of the same one.

The hard part is not placing the order. It is **deciding how much cover, on which venue, for which
tenor, at what price — and knowing when not to buy.** That decision is venue-independent, so it
lives here, and venue-specific execution lands as an adapter when a venue actually exists.

**As of 2026-09-26 no venue can execute this.** Derive V3 mainnet is pre-launch with a
verified-empty production orderbook (testnet has a book; production does not — see
`ops/derive_book_probe.mjs`). Lighter options do not exist yet. Deribit is a **data source** for
gamma walls, not a venue.

So this repo ships the part that is true regardless: the policy, the cost comparison, and the plan.

## Layout

```
packages/options_contracts/  coverage.schema.json — the "lego studs"; consumers read this
packages/options_core/       the decision engine (costs, venues, plan)
apps/coverage_cli/           asks "what should the insurance be?" and publishes data/coverage.json
config/policy.yml            every number is a config value, not a constant
data/coverage.json           the published artifact other tools consume
ops/derive_book_probe.mjs    the venue gate — 12s, no credentials
tests/                       the decision rules, incl. the import-boundary guard
```

There are deliberately **no venue adapters yet.** An adapter that cannot execute is scaffolding,
and scaffolding reads as progress.

## The one rule that matters

**A plan never silently covers nothing.**

If no venue can sell the insurance, `uncovered_usd` equals the full exposure and `notes` names
every venue and its reason. A hedge that reports success while covering nothing is the failure
this repo exists to prevent — and it is the default failure of anything that just returns a list.

Two supporting rules, both enforced by tests:

- **The gate is closed by default.** `live=False` unless explicitly set, so a venue must be proven
  before it can be selected.
- **Live but unquoted is still unavailable.** A `premium_bps` of `0.0` placeholder must never win a
  cheapest-venue comparison it did not earn.

## Rules (enforced by `tests/test_import_boundaries.py`)

1. `apps/*` may import `packages/*`. `packages/*` may **never** import `apps/*`.
2. `apps/a` may not import `apps/b`.
3. Apps communicate through **data** (versioned JSON), never through code.

Copied deliberately from `lighter-core`, which learned them the hard way.

## Running

```sh
PYTHONPATH=apps uv run --with pyyaml --no-project python -m coverage_cli \
    --policy config/policy.yml --exposure-usd 4860 --label "spot ETH" \
    --write data/coverage.json

uv run --with pytest --with pyyaml --no-project python -m pytest tests/ -q
```

## Consuming this

Read `data/coverage.json`. Do not import this repo's code.

Integration points, in the order they are worth doing:

1. **Trading dashboard** — render `plan.coverage_pct`, `plan.uncovered_usd` and the `notes`. The
   notes are the honest part: they say *why* cover is missing, which is more useful on a dashboard
   than insurance that silently does not exist.
2. **Scanners / research pipelines** — read the artifact for context ("what is currently insured")
   instead of re-deriving it.

## What still has to be filled in by hand

`premium_bps` on every venue is `0.0` and `tenor_days` is a guess at the quarterly ladder. Both are
**placeholders, not measurements**, and the second is only harmless because the first keeps every
venue gated off. They must come from a real quote before any plan is acted on. This is flagged in
HANDOFF.md next to the gate.
