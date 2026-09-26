"""derive_quotes — the first venue adapter: a read-only reader of a live options book.

The decision layer is venue-independent; this app is the venue-specific half. It answers one
question with measurements — *what does the protective put actually cost right now* — and
publishes the answer as data (data/quotes.json). It never trades: buying stays a human action.
"""
import sys
from pathlib import Path

# The package is imported before __main__ runs, so the path bootstrap must live here: an app may
# import packages (rule 1), but only once `packages/` is on sys.path.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages"))

from .quote import (  # noqa: E402,F401
    QuoteRow,
    best_levels,
    build_artifact,
    parse_instrument,
    rows_from_snapshots,
    select_protective_put,
)

__all__ = ["QuoteRow", "best_levels", "build_artifact", "parse_instrument",
           "rows_from_snapshots", "select_protective_put"]
