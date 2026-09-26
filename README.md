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

**The venue exists.** Derive's **v2** deployment — `api.lyra.finance` on Derive Chain — is live
and trading (verified 2026-09-26: ETH-PERP trades settling on-chain every few minutes, and the
181-day put ladder quoted two-sided). `apps/derive_quotes` reads that book (public, read-only)
and publishes `data/quotes.json`; `coverage_cli --quotes` prices the plan from it. Derive **v3**
(`api.derive.xyz`) is a pre-launch zk stack with a verified-empty production orderbook — not
listed until it trades (`ops/derive_book_probe.mjs` is the v3 gate). Lighter options do not exist
yet. Deribit remains a **data source** for gamma walls, not a venue.

So this repo ships the decision, the measurement, and the plan — and never the order.

## Layout

```
packages/options_contracts/  coverage.schema.json + quotes.schema.json — the "lego studs"
packages/options_core/       the decision engine (costs, venues, plan, quote overlay)
apps/coverage_cli/           asks "what should the insurance be?" and publishes data/coverage.json
apps/derive_quotes/          reads the live Derive v2 book, read-only -> data/quotes.json
config/policy.yml            every number is a config value, not a constant
data/coverage.json           the published artifact other tools consume
data/quotes.json             the measurement it is priced from
ops/derive_book_probe.mjs    the v3 venue gate — 12s, no credentials
tests/                       the decision rules and the quote rules, incl. the import-boundary guard
```

The first venue adapter is `apps/derive_quotes` — and it exists only because the venue does: it
reads a public book and publishes measurements (`data/quotes.json`). It places nothing; buying
stays a human action. An adapter that cannot execute is scaffolding, and scaffolding reads as
progress — so there is still exactly one.

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
- **A measurement expires.** Quotes older than `quotes_max_age_hours` are ignored and the venue
  returns to "unquoted" — a plan is never priced off a stale book.

## Rules (enforced by `tests/test_import_boundaries.py`)

1. `apps/*` may import `packages/*`. `packages/*` may **never** import `apps/*`.
2. `apps/a` may not import `apps/b`.
3. Apps communicate through **data** (versioned JSON), never through code.

Copied deliberately from `lighter-core`, which learned them the hard way.

## Running

```sh
# 1. measure the live book (public, read-only — writes data/quotes.json)
PYTHONPATH=apps uv run --with websockets --with pyyaml --no-project python -m derive_quotes

# 2. decide, priced by that measurement
PYTHONPATH=apps uv run --with pyyaml --no-project python -m coverage_cli \
    --policy config/policy.yml --quotes data/quotes.json \
    --exposure-usd 4860 --label "spot + margin, all accounts" \
    --write data/coverage.json

uv run --with pytest --with pyyaml --with jsonschema --no-project python -m pytest tests/ -q
```

## Consuming this

Read `data/coverage.json` (the decision) and `data/quotes.json` (the measurement behind it).
Do not import this repo's code.

Integration points, in the order they are worth doing:

1. **Trading dashboard** — DONE (2026-09-26): the dashboard's *Options Insurance* card renders
   `plan.coverage_pct`, `plan.uncovered_usd`, the measured instrument and the `notes`, served at
   `GET /api/options-coverage` (contract `options.coverage.view/1`) straight from this artifact —
   own endpoint, freshness window taken from this repo's own `quotes_max_age_hours`, and the
   pricing marked **EXPIRED** past it rather than shown as live cover. The notes are the honest
   part: they say *why* cover is missing, which is more useful on a dashboard than insurance that
   silently does not exist.
2. **Scanners / research pipelines** — read the artifact for context ("what is currently insured")
   instead of re-deriving it.

## What is still open

- **Execution is unexercised.** The venue is live and quoted; this repo has never placed an order
  (by design — no keys). Buying the put is a human action on the venue's own interface.
- **Only the 181d series is quoted.** 272d and 363d were empty at measurement; the adapter
  re-measures each run, so the tenor follows the book rather than a guess.
- **The long-dated book is intermittent, not persistent.** Fully quoted 2026-09-26 12:15 UTC
  (16 strikes two-sided) and empty two-sided by 13:53 UTC the same day (the maker left; the perp
  kept trading). Re-measure at action time — the plan publishes "no venue available" itself when
  the book is gone, which is the honest answer.
- **v3 re-gate.** When Derive v3 mainnet launches, re-run `ops/derive_book_probe.mjs`; a non-empty
  production book is the trigger to re-point the adapter.
- `premium_bps` in policy stays `0.0` **on purpose**: it keeps a venue unquoted unless
  `data/quotes.json` is fresh. Never put a guessed premium there.
