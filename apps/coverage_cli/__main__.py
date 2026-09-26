"""coverage_cli — ask what the insurance should be, and publish the answer.

Read-only. Produces data/coverage.json, the artifact other projects consume.

Exposure is an INPUT, not a fetch. Your positions live across several accounts (spot and margin)
and the authoritative number is whichever one you point at this; a decision layer that goes and
fetches its own inputs is a decision layer that cannot be tested.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))  # apps may import packages (rule 1)

from options_core import build_mechanisms, plan_hedge  # noqa: E402

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
    ap.add_argument("--exposure-usd", type=float, required=True,
                    help="total exposure to insure, in USD (sum across accounts as you see fit)")
    ap.add_argument("--label", default=None,
                    help="what this exposure is (e.g. 'spot ETH', 'margin BTC'), recorded")
    ap.add_argument("--burned-off", action="store_true",
                    help="a policy trigger fires; the correct amount of insurance is zero")
    ap.add_argument("--write", default=None, help="path to write coverage.json")
    ap.add_argument("--json", action="store_true", help="print the artifact to stdout")
    args = ap.parse_args(argv)

    policy = load_policy(Path(args.policy))
    mechanisms = build_mechanisms(policy)
    plan = plan_hedge(
        exposure_usd=args.exposure_usd,
        policy=policy,
        mechanisms=mechanisms,
        burned_off=args.burned_off,
    )

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "produced_by": "options/coverage_cli",
        "read_only": True,
        "subject": {
            "label": args.label or "(unlabelled exposure)",
            "exposure_usd": round(args.exposure_usd, 2),
        },
        "policy": {
            "target_ratio": policy.get("target_ratio"),
            "min_tenor_days": policy.get("min_tenor_days"),
            "max_premium_bps": policy.get("max_premium_bps"),
        },
        "plan": plan.as_dict(),
    }

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
