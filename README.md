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

**Exposure can be measured, not typed.** `apps/portfolio_reader` reads the real accounts —
Ethereum L1 balances over a public RPC, Lighter accounts over their public API — and publishes
`data/portfolio.json`: what the portfolio is *worth* (`totals`) beside what a drop actually
*reaches* (`exposure` — net crypto per asset; stablecoins and offsetting long/short pairs across
venues contribute zero). `coverage_cli --portfolio` sizes the plan from that artifact and records
the provenance in `subject.source`. An **incomplete read is refused, never priced**: when any
account fails, the artifact says so (`complete: false`) and the CLI stops, because understating
the position is the one failure insurance must never have.

## Layout

```
packages/options_contracts/  coverage.schema.json + quotes.schema.json + portfolio.schema.json — the "lego studs"
packages/options_core/       the decision engine (costs, venues, plan, quote overlay)
apps/coverage_cli/           asks "what should the insurance be?" and publishes data/coverage.json
apps/derive_quotes/          reads the live Derive v2 book, read-only -> data/quotes.json
apps/portfolio_reader/       reads YOUR positions (L1 balances + Lighter accounts) -> data/portfolio.json
config/policy.yml            every number is a config value, not a constant
data/coverage.json           the published artifact other tools consume
data/quotes.json             the measurement it is priced from
data/portfolio.json          the measured positions the plan is priced against
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
# 0. read the actual positions (read-only: public RPC + public venue APIs — writes data/portfolio.json)
PYTHONPATH=apps uv run --with "lighter-sdk @ git+https://github.com/elliottech/lighter-python.git" \
    --no-project python -m portfolio_reader

# 1. measure the live book (public, read-only — writes data/quotes.json)
PYTHONPATH=apps uv run --with websockets --with pyyaml --no-project python -m derive_quotes

# 2. decide, priced by that measurement and sized by the measured positions
PYTHONPATH=apps uv run --with pyyaml --no-project python -m coverage_cli \
    --policy config/policy.yml --portfolio data/portfolio.json --quotes data/quotes.json \
    --write data/coverage.json
# (or size it by hand: --exposure-usd 4860 --label "spot + margin, all accounts" — exactly one of the two)

uv run --with pytest --with pyyaml --with jsonschema --no-project python -m pytest tests/ -q
```

## Consuming this

Read `data/coverage.json` (the decision) and `data/quotes.json` (the measurement behind it).
Do not import this repo's code.

Integration points, in the order they are worth doing:

1. **Trading dashboard** — DONE (2026-09-26, extended 2026-09-27): the *Portfolio* card and its
   `/portfolio` page render the measured positions and the plan together — `GET /api/portfolio`
   (`portfolio.view/1`) merges this repo's `data/portfolio.json` + `data/coverage.json`, each with
   its own freshness. `GET /api/options-coverage` (`options.coverage.view/1`) remains the
   plan-only contract. Pricing is marked **EXPIRED** past the quote window rather than shown as
   live cover; the notes say *why* cover is missing, which is more useful on a dashboard than
   insurance that silently does not exist.
2. **Scanners / research pipelines** — read the artifact for context ("what is currently insured")
   instead of re-deriving it.

## Venue risk

Derive Chain is OP-stack with a centralized operator. Before any funding, read
`docs/venue-risk.md` (checked 2026-09-27): L2BEAT rates the chain *below Stage 0* — the fault-proof
system is deployed but not functional, upgrades are instant with no exit window (CRITICALs),
data is on Celestia with no DA bridge, and proposers/challengers are permissioned. The venue's own
block states keep the **withdraw route open** even for restricted/compliance-blocked accounts, and
the chain's forced-inclusion path lets a censored user exit via Ethereum L1 (≤12h to force, then
~3.5–7d to claim). Sizing rule: premium + small buffer only — never park size on Derive Chain.
**Update 2026-09-27:** the V2→V3 upgrade is now at a **live vote** (closes Oct 4) — V3 moves
custody to Ethereum L1 contracts and supersedes most of this analysis; see §V3 in the dossier.

## What is still open

- **Positions are as wide as the reader.** It covers the L1 wallet + both Lighter accounts today.
  On 2026-09-27 it caught a real move within a day of being built: 1.71 ETH left the tracked wallet
  on 2026-09-26 18:47 UTC (through two relay wallets into a service hot wallet — likely an
  exchange/bridge sweep). Money moved elsewhere stops being covered until its account is added.
- **Execution is unexercised.** The venue is live and quoted; this repo has never placed an order
  (by design — no keys). Buying the put is a human action on the venue's own interface.
- **Only the 181d series is quoted.** 272d and 363d were empty at measurement; the adapter
  re-measures each run, so the tenor follows the book rather than a guess.
- **The long-dated book is intermittent, not persistent.** Fully quoted 2026-09-26 12:15 UTC
  (16 strikes two-sided) and empty two-sided by 13:53 UTC the same day (the maker left; the perp
  kept trading). Re-measure at action time — the plan publishes "no venue available" itself when
  the book is gone, which is the honest answer.
- **v3 re-gate — now an event with a calendar.** The "DIP: Launch Derive V3" Snapshot vote is live
  (2026-09-24 → Oct 4, ~99% For); on approval: ≥14-day notice, then automatic V2→V3 migration and
  Derive Chain wind-down (deposits move to Ethereum L1). Re-run `ops/derive_book_probe.mjs` and
  port the adapter at launch — a non-empty production book is the trigger to re-point.
- `premium_bps` in policy stays `0.0` **on purpose**: it keeps a venue unquoted unless
  `data/quotes.json` is fresh. Never put a guessed premium there.
