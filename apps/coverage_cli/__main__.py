"""coverage_cli — ask what the insurance leg should be, and publish the answer.

Read-only. Produces data/coverage.json, which is the artifact every other project consumes.
Deliberately takes exposure as an INPUT rather than reading wallets itself: sourcing exposure is
each consumer's job (the dashboard knows the positions, the bot knows the book), and a decision
layer that fetches its own inputs is a decision layer that cannot be tested.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))  # apps may import packages (rule 1)

from hedge_core import build_mechanisms, plan_hedge  # noqa: E402

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
                    help="current spot exposure to hedge, in USD")
    ap.add_argument("--horizon-days", type=float, default=None,
                    help="override the policy horizon for the cost comparison")
    ap.add_argument("--burned-off", action="store_true",
                    help="a policy trigger fires; the correct hedge is currently zero")
    ap.add_argument("--write", default=None, help="path to write coverage.json")
    ap.add_argument("--json", action="store_true", help="print the artifact to stdout")
    args = ap.parse_args(argv)

    policy = load_policy(Path(args.policy))
    mechanisms = build_mechanisms(policy)
    plan = plan_hedge(
        exposure_usd=args.exposure_usd,
        policy=policy,
        mechanisms=mechanisms,
        horizon_days=args.horizon_days,
        burned_off=args.burned_off,
    )

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "produced_by": "hedge-core/coverage_cli",
        "read_only": True,
        "policy": {
            "target_ratio": policy.get("target_ratio"),
            "horizon_days": args.horizon_days or policy.get("horizon_days"),
            "max_cost_bps": policy.get("max_cost_bps"),
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
