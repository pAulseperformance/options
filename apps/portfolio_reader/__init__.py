"""portfolio_reader — your actual positions, across accounts, as one measured artifact.

The decision layer takes exposure as an INPUT (that design keeps it testable). This app is the
caller's half of that contract: it reads the real accounts — read-only, public data only — and
publishes data/portfolio.json, which `coverage_cli --portfolio` can price against.

Nothing here signs, custodies or moves anything, and no private endpoint is touched: balances
come from an Ethereum L1 public RPC, accounts from Lighter's public API, prices from a public
spot feed.

Two different numbers come out of this app, deliberately:

  totals.usd_total    what the portfolio is WORTH — every account, cash and all, with a
                      public pool counted at the operator's OWN share (its full equity is
                      published on the account, and the depositors' part as delegated_usd).
  exposure.usd        what a price drop REACHES — net crypto per asset across ALL accounts,
                      a pool's legs scaled to that same share.

The planner consumes the second. Stablecoins contribute nothing to it (cash does not fall), and
a long on one venue against a short on another nets out (that is what the pair is for). Netting
happens per asset across accounts because that is how a price move actually reaches a portfolio.
"""
import sys
from pathlib import Path

# The package is imported before __main__ runs, so the path bootstrap must live here: an app may
# import packages (rule 1), but only once `packages/` is on sys.path.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages"))

from .portfolio import (  # noqa: E402,F401
    STABLES,
    build_artifact,
    exposure_of,
    failed_account,
    normalize_l1,
    normalize_lighter,
    placeholder_derive,
    totals_of,
)

__all__ = ["STABLES", "build_artifact", "exposure_of", "failed_account", "normalize_l1",
           "normalize_lighter", "placeholder_derive", "totals_of"]
