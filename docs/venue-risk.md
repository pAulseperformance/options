# Venue risk: Derive Chain — compliance blocks, chain safety, and whether you can always exit

Checked 2026-09-27 by Paul Mendes's request. Question set: (1) does the region/VPN restriction
actually hold up, (2) what does L2BEAT say about Derive Chain, (3) **if an account gets banned,
can the funds come off Derive Chain** (the "lighter-exit" exercise, applied to Derive).

Sources (all quotes verbatim): L2BEAT project page `l2beat.com/scaling/projects/derive` (risk data
as of 2026-08), Derive docs API error catalog `docs.derive.xyz/reference/error-codes` and
`docs.derive.xyz/error-codes.md`, ToS `derive.xyz/terms-of-use`. Nothing here is legal advice;
it is the published record.

## TL;DR

- **Exit is possible at all three layers, each with a named caveat.** The venue's own error-code
  catalog keeps the *withdraw* route open in its blocked states; the chain supports a forced exit
  through Ethereum L1; the residual risk is operator power (instant upgrades), not a trap door.
- **The honest headline is the chain rating:** L2BEAT says Derive Chain *"is not even a Stage 0
  project"* — the fault-proof system is deployed but not functional, upgrades are instant with no
  exit window, data lives on Celestia with no DA bridge, and proposers/challengers are permissioned.
- **Sizing rule that follows:** money on Derive Chain should be *premium + small buffer* — money
  you are fine taking days to exit at operator trust. The 1.8 ETH stays on L1.
- Nothing of ours is on Derive Chain yet — this is pre-funding diligence.

## 1. What "banned" actually means (venue layer)

Three distinct block states exist in the API error catalog
(`docs.derive.xyz/reference/error-codes`):

| code | state | consequence (verbatim) |
|---|---|---|
| 16000 | restricted region | "You are in a restricted region that violates our terms of service. **You may withdraw funds any time but deposits, transfers, orders are blocked**" |
| 16001 | compliance disable | "Account is disabled due to compliance violations, please contact support to enable it." |
| 16002 | OFAC block | "Account is blocked due to OFAC compliance violations." |

So the designed behaviour of a region block is: **trading stops, withdrawal stays open.**
16001 is a support conversation; only OFAC (16002, wallet-screening driven) reads as a hard block.
Enforcement levers observed: IP/region detection (16000), wallet screening (16002), manual
compliance (16001). There is no KYC in the self-custody flow.

ToS access conditions: US persons/residents/citizens/tax residents, Australian tax residents,
Ontario residents, Restricted/Sanctioned Persons are excluded. The anonymity clause:
> "you do not and will not use a virtual private network, proxy, remote desktop, relay,
> anonymisation service, false location information, misleading onboarding information, privacy
> tool, anonymisation tool or technique, or any other method to circumvent or attempt to
> circumvent geolocation, jurisdictional, sanctions, eligibility, access, wallet screening,
> compliance, or other restrictions that apply to the Application"

and a reservation of rights: "we may apply changes to, replace, suspend, restrict, or discontinue
any part, function, feature, market, instrument, transaction type ... API endpoint ... at any
time". Read together: the clause targets circumventing restrictions *that apply to you*; its
letter is very broad (any anonymity tooling), but **no public record of VPN-specific enforcement
was found** — the documented machinery is the three codes above. Practical note: a VPN exit that
*lands* in a restricted region (e.g. a US IP) will simply draw the 16000 region block on actions
— that is the geoblock working, and another exit fixes it. (Paul Mendes is not a US person,
confirmed 2026-09-27, so the US exclusion is not the binding constraint.)

## 2. Chain layer (L2BEAT, the load-bearing analysis)

Derive Chain = OP Stack rollup, chain ID 957, TVS $135.76M. L2BEAT's risk summary values:

- **Stage: "Derive is not even a Stage 0 project."** Requirement for available node software is
  under review.
- **State validation: Fraud proofs (INT) — deployed but NOT functional.** "The dispute game
  commits to an op-program release that predates the Jovian hardfork active on the chain, so it
  cannot derive current blocks and no dispute can be resolved correctly by execution. Security
  relies entirely on the permissioned proposer and challengers." Also: "Only one entity is
  currently allowed to propose and submit challenges, as only permissioned games are currently
  allowed."
- **Data availability: External (Celestia), no DA bridge.** "Proof construction and state
  derivation fully rely on data that is posted on Celestia." Consequences: "Funds can be lost if
  the sequencer posts an unavailable transaction root (CRITICAL)" and "if the data is not
  available on the external provider (CRITICAL)". Lyra switched to Celestia 2024-01-16.
- **Exit window (upgrades): None.** "There is no window for users to exit in case of an unwanted
  upgrade since contracts are instantly upgradable." → "Funds can be stolen if a contract receives
  a malicious code upgrade. There is no delay on code upgrades (CRITICAL)."
- **Proposer failure: Cannot withdraw.** "Only the whitelisted proposers can publish state roots
  on L1, so in the event of failure the withdrawals are frozen."
- **Sequencer failure: forced inclusion works.** "In the event of a sequencer failure, users can
  force transactions to be included in the project's chain by sending them to L1. There can be up
  to a 12h delay on this operation." (Operator section: "anyone can submit their transactions
  [on the host chain] ... allows the users to circumvent censorship by interacting with the smart
  contract on the host chain directly." — OptimismPortal2 `depositTransaction`.)
- **Operator: centralized.** "The operator is the only entity that can propose blocks." + MEV
  frontrunning risk.

**Exit mechanics measured by L2BEAT (Withdrawals section):** regular exit = initiate on L2 →
prove inclusion on L1 (can be proven before settlement) → after the challenge period (funds
"available ... after 3d 12h"; also "a 7d period has to pass before it becomes actionable") submit
an L1 claim with a merkle proof. **Budget ≈ half a week to a week for a normal exit.**

**Who can press the red buttons:** "Conduit Multisig 1" — a 4-of-11 multisig — "Can upgrade
**with no delay**" every core contract (OptimismPortal2, L1StandardBridge, DisputeGameFactory,
SystemConfig, SuperchainConfig, ... ProxyAdmins) and is "Allowed to pause withdrawals". It can
also update the sequencer/batcher addresses. 73 upgrades on current proxies; last one 1mo14d ago.
A separate EOA is the batch submitter.

**Bridging:** "Value secured breakdown" shows ~99.9% of TVS arrived via the **external Socket
bridge** (wstETH $32.3M, USDC $32.0M, WETH $24.5M, wBTC $16.8M, cbBTC $12.6M, ...); only ~$125K
ETH is canonical. The import path is a third-party bridge; the export paths are the ones above.

## 3. So — can funds come off Derive Chain if banned?

Layered answer, worst case last:

1. **Region/compliance block (16000/16001): withdraw stays open by design** — the venue blocks
   *actions*, not exits. This is the venue's own documented behaviour.
2. **Venue stonewalling + chain censorship:** OP-stack forced inclusion — force the withdrawal
   transaction (or any tx) through L1 via OptimismPortal2, ≤12h delay, then the standard
   prove-and-claim (≈3.5–7d) to land on L1. This works *without the operator's cooperation*.
3. **The residual risks (named, not hypothetical):** (a) a malicious instant upgrade — no delay,
   no exit window is guaranteed (L2BEAT CRITICAL: funds can be stolen); (b) proposers stop or are
   coerced — L1 withdrawals freeze (no state roots → nothing to prove against); (c) a full OFAC
   block (16002) is the one state that reads as funds-held; (d) protocol-level: withdrawals pause
   during an insolvent auction (error 11034 `withdrawals_blocked_insolvent_auction`).
4. **Therefore:** Derive Chain is a "trust the operator while you're on it" chain with no
   independent verification in practice (broken proofs, one permissioned proposer, instant
   upgrades). It is fine for **insurance-sized money** (premium + buffer, ~$100–300, exited in
   days if needed); it is *not* a place to park size. Keep the L1 stack on L1.

For comparison: L2BEAT at least grades Lighter as a Stage 0 appchain; Derive's page says it "is
not even a Stage 0 project". Neither is somewhere to park size; both are fine for what they're
being used for at small size.

## Re-check triggers

- Derive v3 mainnet launch — v3 moves deposits/withdrawals to **Ethereum L1** (docs: withdraw to
  "an L1 recipient address"), which removes most of this layer; re-run this diligence at migration.
- Any L2BEAT status change (Stage, disputes, DA) — the page is versioned (last config update seen
  2026-08-27).
- Before any funding above insurance size.
