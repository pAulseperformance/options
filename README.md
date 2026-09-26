# hedge-core

The insurance leg, as a **decision layer** rather than an exchange integration.

**Read-only, no keys.** This repo decides *what the hedge should be*. It never places an order.

## Why this exists

A hedge is not one product. It is a **choice between mechanisms** with different cost structures:

| mechanism | cost | upside | risk |
|---|---|---|---|
| **perp short** | funding (can be income) | capped | liquidation, margin |
| **long put** | premium, paid once and known | kept | none beyond premium |

The stack already contains half of this. `t_61054c5b` locked a **perp-short** sizing (50% ratio,
short 0.90 ETH, first liquidation +37.2%, funding +$0.70/day, trigger: daily close above $3,200 cuts
to 0%). That is mechanism #1, already measured and already live.

Options insurance is mechanism #2, and as of 2026-09-26 **there is no venue that can execute it** —
Derive V3 production has an empty orderbook on every instrument (see `ops/derive_book_probe.mjs`).

So this repo builds the part that is *true regardless of venue*: **the policy, the cost comparison,
and the plan.** When a venue goes live it becomes an adapter, not a rewrite.

## Layout

```
packages/hedge_contracts/   versioned schemas (the "lego studs") — consumers read these
packages/hedge_core/        the venue-agnostic decision engine
apps/coverage_cli/          asks "what should the leg be now?" and publishes data/coverage.json
config/                     policy.yml — every number is a config value, not a constant
data/                       coverage.json — the published artifact other tools consume
ops/                        derive_book_probe.mjs — the venue gate, 12s, no credentials
tests/                      19 tests, incl. the import-boundary guard
```

There are deliberately **no venue adapters yet**. An adapter that cannot execute is scaffolding, and
scaffolding reads as progress. Mechanism #1 (perp short) is already live in the stack and its
sizing is locked in `trading-dashboard/research/hedge_sizing_2026-09-25.md`; mechanism #2 (long put)
has no venue. Each adapter lands when its venue does, gated on `ops/`.

## Rules (enforced by `tests/test_import_boundaries.py`)

1. `apps/*` may import `packages/*`. `packages/*` may **never** import `apps/*`.
2. `apps/a` may not import `apps/b`.
3. Apps communicate through **data** (versioned JSON), never through code.

These are copied deliberately from `lighter-core`, which learned them the hard way: three tools had
already drifted into three independent clients before anyone decided they shouldn't.

## Consuming this from another project

Read `data/coverage.json`. Do not import this repo's code from another repo — the artifact is the
interface. That is what keeps the dashboard, the MM bot, scanners and research pipelines able to use
the same answer without coupling to each other.

## What "available" means

A mechanism reports `(ok, reason)`. When a venue cannot execute, the plan says so **explicitly**
rather than silently returning an empty hedge — a hedge that reports success while covering nothing
is the failure mode this repo exists to prevent.

## Running

```sh
PYTHONPATH=apps uv run --with pyyaml --no-project python -m coverage_cli \
    --policy config/policy.yml --exposure-usd 4860 --write data/coverage.json

uv run --with pytest --with pyyaml --no-project python -m pytest tests/ -q
```

## Consuming this today

`data/coverage.json` is the interface. Three integration points, in the order they are worth doing:

1. **Trading dashboard** — render `plan.coverage_pct`, `plan.legs[].mechanism` and the `notes`.
   The notes are the honest part: they say *why* a hedge is missing, which is more useful on a
   dashboard than a hedge that silently does not exist.
2. **MM bot** — the bot already runs a hedge leg. Point it at `plan.legs[].notional_usd` instead of
   a hardcoded ratio so one number governs both.
3. **Scanners / research** — read the artifact for context ("what is currently hedged") rather than
   re-deriving it.

Rule 3 applies to all three: **read the JSON, do not import this repo's code.** That is what keeps
these consumers independent of each other.
