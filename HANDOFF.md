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
(`api.derive.xyz`) is the zkVM-on-L1 successor — production book still empty, **launch vote live
until Oct 4** (see tail). **The adapter now reads both deployments automatically — ported and
testnet-validated 2026-09-29 (see tail); it switches to v3 the moment v3 quotes.** Without fresh quotes the plan still
reports **no venue available** — correct, and the point of the design.

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
- **The adapter measures both deployments — v3 first, v2 fallback (ported 2026-09-29).** v2
  (`api.lyra.finance`) is live and quoted today; v3 (`api.derive.xyz/v3`) is staged with an empty
  production book, and is measured automatically the moment it quotes — no manual re-point. The
  manual gate remains `ops/derive_book_probe.mjs` (testnet as positive control).
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
- **Derive v3 is staged live but not trading (vote stage).** Production books were empty at the
  last re-probe (2026-09-29 — catalogue + WS staged live, zero quotes; testnet has a book as
  positive control), and the "DIP: Launch Derive V3" Snapshot
  vote is live (2026-09-24 → Oct 4, ~99% For at reading). On approval: ≥14-day notice, then
  automatic V2→V3 migration + Derive Chain wind-down. Re-check with `ops/derive_book_probe.mjs`.
  **The adapter is already ported (auto-selects v3 the moment its book quotes) — nothing manual
  is left but watching the alerts at migration.**

## Open items

1. **Re-measure before acting on any plan:** `PYTHONPATH=apps uv run --with websockets --with
   pyyaml --no-project python -m derive_quotes`, then run coverage with `--quotes data/quotes.json`.
2. **Rail the dashboard** — DONE (2026-09-26): *Options Insurance* card + `GET /api/options-coverage`
   (`options.coverage.view/1`) on the trading dashboard read `data/coverage.json`; the card says
   EXPIRED past the quote window and never executes.
3. **v3 at migration:** the reader is ported (2026-09-29 — auto v3-first, testnet-validated); the
   book watch alerts if the published deployment moves. Re-run `ops/derive_book_probe.mjs` for a
   manual check whenever wanted.
4. **Keep pushing:** remote `origin` = `pAulseperformance/options` (exists — verified 2026-09-26);
   push `main` after each session. A local-only commit is not a backup.
5. **DRV (the token) — entry decision deferred by Paul Mendes (2026-09-28): revisit after the V3
   migration.** The reminder rides `ops/v3_watch.py` — a heads-up line on the vote-close and
   migration-notice alerts, the full reminder (+ live price) when the venue starts trading. Sketch:
   small spot only (no DRV perp on Derive/HL/Lighter), pilot + ladder $0.36 / $0.32, no chase above
   $0.50; levels were read 2026-09-28 (post-pump range $0.375–0.509 — re-derive live before acting).

## Provenance

- Venue research: `brain/inbox/2026-09-26/20260926T034500-f8c5b239-derive-v3-options-integration-research.md`
- Exposure basis: **1.8011419346783952 ETH** of spot on `0xe83ECEe6ad078a64F641BdA924c841Fd0F7D58f9` — the verified-so-spot from the 2026-09-25 hedge-sizing run (`trading-dashboard/research/hedge_sizing_2026-09-25.md`), re-verified live 2026-09-26 (balance unchanged; ≈$4,836 at $2,684.92). The artifact's $4,860 = the same balance at that run's ~$2,696 price. Margin accounts were ≈empty, so the input is effectively spot-only.
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

**Railed (same session, second pass):** the trading dashboard now reads this artifact as its own
contract — `GET /api/options-coverage` (`options.coverage.view/1`) + an *Options Insurance* card
(coverage %, dollars uninsured, the measured instrument, the notes; EXPIRED past the quote window).
Dashboard tests 262 green (10 new), the card SSR-verified in all four states, live at
`http://100.108.43.24:8899` since 2026-09-26 ~13:05 UTC.

**Then the book vanished (same day, 13:53 UTC).** A re-measure — same adapter, same policy, ~1.5h
after the deploy-time scan — found **zero two-sided strikes** across the 181d ladder (16/16 quoted
at 12:15; two scans 40 s apart agree; a few one-sided asks remain, sizes 0.5–60; ETH-PERP is still
two-sided, so the venue is alive and it is the options MAKER that left). The plan correctly
republished as **NO VENUE AVAILABLE — uninsured**, reason attached:
`derive: the last quote run found no qualifying long-dated put`.
**The lesson for this file: Derive v2's long-dated options book is intermittent, not persistent** —
measure at action time, and buying is possible only while the maker is quoting. (The 12:15 "covered"
artifact stays in history as exactly the kind of confident-but-gone quote this repo exists to
catch.)

**Measured positions + the portfolio rail (2026-09-27).** `apps/portfolio_reader` publishes
`data/portfolio.json` from the real accounts — L1 balances via a public RPC, Lighter via the
official SDK against both deployments — read-only, public data only. The artifact keeps two
numbers apart on purpose: `totals` = what the portfolio is worth ($2,493.91 across L1 $247.56 +
Lighter mainnet $1,104.25 + RH $1,142.10), `exposure` = what a drop reaches ($247.83 — net ETH;
the LIT ±1.57 and CASHCAT ±308.4 cross-venue pairs net to 0 and are excluded, each with a note
naming both legs). `coverage_cli --portfolio` now sizes the plan from it (`subject.source` records
the provenance); an incomplete artifact is refused, never priced. Dashboard: the *Portfolio* card
+ `/portfolio` page read both artifacts as `portfolio.view/1` (`GET /api/portfolio`) — the
coverage-only card was superseded and removed; `/api/options-coverage` still stands. Tests: 60
options + 282 dashboard; live + verified 2026-09-27 ~08:55 UTC.
**The wallet moved while this was being built:** the tracked L1 wallet went 1.8011 → 0.0911 ETH on
2026-09-26 18:47 UTC (1.71 ETH → two relay wallets → a service hot wallet holding 11k+ ETH that
pays out continuously — an exchange/bridge sweep pattern; not traced further on purpose).
`0x10362f47ffb1f2e18db580e8d8a6f355002898093a856f41d7e77bd150e45584` is the hop out of the
tracked wallet. Whether that money is "sold", "parked elsewhere", or "should be tracked" is a
question for Paul Mendes — the reader follows the accounts it is given, and nothing else.

**Venue-risk diligence (2026-09-27): `docs/venue-risk.md`.** The question was "can funds come off
Derive Chain if an account gets banned?" — answer: yes at three layers, each with a named caveat.
(1) *Venue:* compliance/region blocks are documented API states (error codes 16000/16001/16002)
and the region block explicitly keeps the withdraw route open — "You may withdraw funds any time
but deposits, transfers, orders are blocked" [16000]. (2) *Chain:* OP-stack forced inclusion lets
any transaction (including initiating a withdrawal) be pushed through Ethereum L1 — "up to a 12h
delay" — and a normal exit (initiate on L2, prove, claim on L1) lands after the challenge period
(≥3d12h; budget ~a week). (3) *Residual:* L2BEAT says Derive Chain "is not even a Stage 0
project" — the fault-proof system is deployed but NOT functional (dispute game commits to an
op-program release predating the Jovian hardfork), contracts are instantly upgradable with no exit
window (CRITICAL), DA is Celestia with no DA bridge (CRITICAL), proposers/challengers are
permissioned (one entity), and a Conduit 4-of-11 multisig can upgrade everything with no delay and
pause withdrawals. **Sizing rule adopted: money on Derive Chain = premium + buffer only, never
parked size; the ETH stays on L1.** ToS: US/AU/Ontario excluded; the anonymity-tool clause is
broad ("VPN, proxy ... privacy tool, anonymisation tool or technique") but the documented
enforcement surface is region detection (IP), wallet screening (OFAC), and manual compliance — no
VPN-specific enforcement record found; Paul Mendes is not a US person (2026-09-27).

**V3 vote is live (checked 2026-09-27) — the upgrade this repo has been waiting for has a date.**
"DIP: Launch Derive V3" (forum 2026-09-14) went to Snapshot (`derivexyz.eth`) on 2026-09-24, open
until **Oct 4 01:40 UTC**, running **99.2% For** (79.3M vs 0.65M DRV; 11 voters). V3 = the zkVM
exchange: funds escrowed in **Ethereum L1 contracts**, every batch proof-verified on L1, an escape
hatch (ordered forced processing → 2-week processing duty → permissionless sequencer takeover),
L2Beat Stage 1 as the stated goal. Migration is **automatic** (V2 snapshot → genesis; no redeposit;
positions and session keys carry; trigger/TWAP orders reset; Derive Chain wound down); **deposits
become Ethereum L1** — the "fund a small chain" step disappears, superseding `docs/venue-risk.md`'s
chain analysis (§V3) and softening the sizing rule for post-V3 funding. Earliest migration:
mid-October (vote close + ≥14-day notice). The 30-min book watch doubles as the venue-down alarm
when V2 winds down. Also checked for completeness: **no "V4" exists** (docs full-text, forum
search, news — 2026-09-27); "V3" is the live upgrade, "HIP-4" is Hyperliquid's thing.
New watch: `ops/v3_watch.py` (cron "Derive V3 Watch", daily 9:05) pings on vote close, migration
notice, the v3 API answering (**fired 2026-09-28**), or the v3 venue starting to trade; it also
carries the deferred DRV entry reminder (Open items #5). (The book watch rewrites `data/quotes.json`
+ `data/coverage.json` every 30 min — those two files showing as modified in `git status` between
sessions is expected, not drift.)

**V3 venue is materializing (2026-09-29) — staged live, all books empty, not trading.** The v3 API
started answering; the watch flagged it to Signals exactly once (2026-09-28 09:05, delivered). It is
now serving the full staged surface: 4,240 live instruments — ETH 792, BTC 659, HYPE 564, SOL 439 —
including the long-dated ETH families the insurance plan wants (2027-03-26 / 06-25 / 09-24; puts
across ~1.4k–3.6k strikes; `scheduled_activation` was 2026-09-25 08:00 UTC). But **every book is
empty**: `ops/derive_book_probe.mjs` re-run against v3 (2026-09-29) got 8/8 WS snapshots at 0 bids /
0 asks, ETH-PERP ticker stats are zero (oi 0, trades 0), trade history empty. v2 remains the trading
venue (fills at ~$2,697). Port notes for v3: `public/get_instruments` is gone — use
`get_all_instruments` / `get_all_live_instruments`; option tickers take `expiry_date=YYYYMMDD`;
`tx_status` → `batch_status`; currency data reshaped by risk universes
(docs.derive.xyz/migrating/breaking-changes).

**V3 recon captured (2026-09-29) — the port is now a bounded job.** The v3 WS surface is
**source-compatible with the v2 reader** (same `orderbook.<instr>.1.10` channels, same subscribe
acks, same snapshot frames) — verified against **populated testnet** (`testnet.api.derive.xyz/v3`:
3,568 instruments incl. every long-dated ETH put family; 7/8 probe targets returned books), so the
reader port can be dry-run end-to-end before migration day. `ops/derive_book_probe.mjs` now takes
`WS_URL` / `TARGETS` env overrides. v3 also carries a staged **DRV-USDC** spot instrument (DRV is a
v2 spot currency too). Full recon (catalogue shapes, ticker routes, gotchas) is in skill
`derive-platform` → `references/v2-to-v3-migration.md`. DRV token dossier filed at
`Trading-Vault/Research/Crypto/DRV.md` (open item #5 remains the reminder).

**v3 reader ported (2026-09-29) — the pipeline measures the new venue the day it quotes.** The
adapter change the 2026-09-26 note deferred ("re-pointing ENDPOINTS") is done, and automated:
`apps/derive_quotes` now carries both deployments and a `--deployment` choice (`auto` default =
**v3 first, v2 fallback**). Selection ladder: a deployment with a policy-qualifying put wins; else
any genuine two-sided quote; else a merely functional venue (an empty board is still a published
answer — "unquoted"). If nothing is functional anywhere it fails loud with every reason.
`ops/book_watch.py` went deployment-aware: it alerts when a book returns **or** when the published
deployment moves — "the pipeline now measures v3" is the migration-era signal, with or without
quotes yet (`DERIVE PIPELINE MOVED TO V3` fires once). Evidence: **testnet dry-run end-to-end**
(populated: 35-book long-dated ladder, selected `ETH-20270924-2400-P` @ 1319 bps) and **prod auto**
(v3 correctly skipped — "no ETH-PERP book — venue staged"; v2 published, still no two-sided
long-dated put). Fixtures recorded: `tests/fixtures/derive_v3_{testnet,prod}_ws_scan_1.json`;
suite 85 green. Also fixed a pre-existing red test: the portfolio schema was missing the `locked`
position kind the reader has published since 2026-09-28.
