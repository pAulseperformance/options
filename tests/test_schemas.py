"""The published artifacts validate against their published contracts — lego studs stay studs."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "apps"))

from derive_quotes.quote import build_artifact, rows_from_snapshots, select_protective_put  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def test_published_coverage_artifact_matches_its_schema():
    schema = json.loads((ROOT / "packages" / "options_contracts" / "coverage.schema.json")
                        .read_text())
    artifact = json.loads((ROOT / "data" / "coverage.json").read_text())
    jsonschema.validate(artifact, schema)


def test_published_portfolio_artifact_matches_its_schema():
    schema = json.loads((ROOT / "packages" / "options_contracts" / "portfolio.schema.json")
                        .read_text())
    artifact = json.loads((ROOT / "data" / "portfolio.json").read_text())
    jsonschema.validate(artifact, schema)


def test_a_generated_quotes_artifact_matches_its_schema():
    doc = json.loads((FIXTURES / "derive_v2_ws_scan_1.json").read_text())
    snapshots, observed = {}, datetime.fromisoformat(doc["fetched_at"].replace("Z", "+00:00"))
    for row in doc["rows"]:
        if row["instrument"].endswith("-PERP"):
            continue
        snapshots[row["instrument"]] = {
            "bids": [[str(row["bid"]), str(row["bid_size"])]] if row["bid"] else [],
            "asks": [[str(row["ask"]), str(row["ask_size"])]] if row["ask"] else [],
        }
    rows = rows_from_snapshots(snapshots, observed)
    sel = select_protective_put(rows, {"min_tenor_days": 180, "strike_otm_pct": 10.0},
                                doc["spot"], min_tradable_size=0.1)
    artifact = build_artifact(
        venue="derive", deployment="v2 · Derive Chain (production)", endpoint="wss://x",
        asset="ETH", fetched_at=observed, spot=doc["spot"], spot_source="ETH-PERP mid",
        policy_inputs={"min_tenor_days": 180}, rows=rows, selected=sel, notes=["n"],
    )
    schema = json.loads((ROOT / "packages" / "options_contracts" / "quotes.schema.json")
                        .read_text())
    jsonschema.validate(artifact, schema)
