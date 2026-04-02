from __future__ import annotations

import argparse
import json
from pathlib import Path

SUPPORTED_PRODUCTS = {
    "accumulated_precipitation",
    "t2",
    "wind10m",
    "h500",
    "storm_track",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WRF output plotting scaffold")
    parser.add_argument("--wrfout", required=True)
    parser.add_argument("--product", required=True, choices=sorted(SUPPORTED_PRODUCTS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    wrfout_path = Path(args.wrfout)
    output_path = Path(args.out)

    if not wrfout_path.exists():
        raise SystemExit(f"Missing wrfout file: {wrfout_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "wrfout": str(wrfout_path),
        "product": args.product,
        "output": str(output_path),
        "note": "Scaffold mode only validates paths. Plot implementation will be added later.",
    }
    if not args.dry_run:
        output_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

