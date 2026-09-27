"""coverage_cli — ask what the insurance should be, and publish the answer.

Read-only. Produces data/coverage.json, the artifact other projects consume.

Exposure is an INPUT, not a fetch — and so are quotes. `--quotes` points at a measurement from
the venue adapter (apps/derive_quotes -> data/quotes.json); while that measurement is fresh it
prices the plan, and when it is stale or missing the venue simply stays unquoted. A decision
layer that fetches its own inputs is a decision layer that cannot be tested.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))  # apps may import packages (rule 1)

from options_core import build_mechanisms, overlay_quotes, plan_hedge  # noqa: E402

SCHEMA_VERSION = 1


def load_policy(path: Path) -> dict:
    text = path.read_text()
    if path.suffix in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment guard
            raise SystemExit(
                "PyYAML is required to read a YAML policy. Install it, or point --policy at a "
                "JSON file with the same keys."
            ) from exc
        return yaml.safe_load(text)
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="coverage_cli", description=__doc__)
    ap.add_argument("--policy", default=str(ROOT / "config" / "policy.yml"))
    ap.add_argument("--exposure-usd", type=float, default=None,
                    help="total exposure to insure, in USD (sum across accounts as you see fit)")
    ap.add_argument("--portfolio", default=None,
                    help="path to a portfolio artifact (data/portfolio.json) — reads the exposure "
                         "and label from measured positions instead of a hand-typed number; "
                         "refused when the artifact is incomplete or shows no net long exposure")
    ap.add_argument("--label", default=None,
                    help="what this exposure is (e.g. 'spot ETH', 'margin BTC'), recorded")
    ap.add_argument("--burned-off", action="store_true",
                    help="a policy trigger fires; the correct amount of insurance is zero")
    ap.add_argument("--quotes", default=None,
                    help="path to a quotes artifact (data/quotes.json): real premiums, used "
                         "only while fresh per policy's quotes_max_age_hours")
    ap.add_argument("--write", default=None, help="path to write coverage.json")
    ap.add_argument("--json", action="store_true", help="print the artifact to stdout")
    args = ap.parse_args(argv)

    if (args.exposure_usd is None) == (args.portfolio is None):
        print("give exactly one of --exposure-usd or --portfolio", file=sys.stderr)
        return 2

    subject_source = "explicit --exposure-usd"
    exposure_usd, label = args.exposure_usd, args.label
    if args.portfolio:
        try:
            port = json.loads(Path(args.portfolio).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"could not read portfolio file {args.portfolio!r}: {exc}", file=sys.stderr)
            return 2
        if port.get("schema_version") != 1:
            print(f"portfolio artifact schema_version {port.get('schema_version')!r} is not "
                  f"supported (expected 1)", file=sys.stderr)
            return 2
        if not port.get("complete", False):
            bad = [a.get("label") or a.get("venue")
                   for a in port.get("accounts", [])
                   if a.get("read", True) and not a.get("ok")]
            print("portfolio artifact is incomplete — refusing to price against an understated "
                  "exposure; unreadable: " + (", ".join(str(b) for b in bad) or "unknown"),
                  file=sys.stderr)
            return 2
        exposure_usd = (port.get("exposure") or {}).get("usd")
        if exposure_usd is None or exposure_usd <= 0:
            print("portfolio artifact shows no net long exposure to insure", file=sys.stderr)
            return 2
        label = args.label or (port.get("exposure") or {}).get("label")
        subject_source = f"portfolio:{args.portfolio} (fetched_at {port.get('fetched_at')})"

    policy = load_policy(Path(args.policy))
    mechanisms = build_mechanisms(policy)

    quotes_meta = None
    overlay_notes: list[str] = []
    if args.quotes:
        try:
            quotes = json.loads(Path(args.quotes).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"could not read quotes file {args.quotes!r}: {exc}", file=sys.stderr)
            return 2
        mechanisms, overlay_notes, quotes_meta = overlay_quotes(
            mechanisms, quotes, source=args.quotes,
            max_age_hours=policy.get("quotes_max_age_hours", 24),
            now=datetime.now(timezone.utc),
        )

    plan = plan_hedge(
        exposure_usd=exposure_usd,
        policy=policy,
        mechanisms=mechanisms,
        burned_off=args.burned_off,
    )
    if overlay_notes:
        plan = replace(plan, notes=list(plan.notes) + overlay_notes)

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "produced_by": "options/coverage_cli",
        "read_only": True,
        "subject": {
            "label": label or "(unlabelled exposure)",
            "exposure_usd": round(exposure_usd, 2),
            "source": subject_source,
        },
        "policy": {
            "target_ratio": policy.get("target_ratio"),
            "min_tenor_days": policy.get("min_tenor_days"),
            "strike_otm_pct": policy.get("strike_otm_pct"),
            "max_premium_bps": policy.get("max_premium_bps"),
            "quotes_max_age_hours": policy.get("quotes_max_age_hours"),
        },
        "plan": plan.as_dict(),
    }
    if quotes_meta is not None:
        artifact["quotes"] = quotes_meta

    if args.write:
        out = Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(artifact, indent=2) + "\n")
        print(f"wrote {out}")

    if args.json or not args.write:
        print(json.dumps(artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
