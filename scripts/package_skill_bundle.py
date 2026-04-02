from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from skill_bundle import BUNDLE_ROOT_NAME, create_bundle_archive, repo_root
except ImportError:  # pragma: no cover
    from .skill_bundle import BUNDLE_ROOT_NAME, create_bundle_archive, repo_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package the WRF skill bundle for distribution.")
    parser.add_argument(
        "--source-root",
        default=repo_root().as_posix(),
        help="Source repository root. Defaults to the current repo.",
    )
    parser.add_argument(
        "--output",
        default=(repo_root() / "dist" / f"{BUNDLE_ROOT_NAME}.tar.gz").as_posix(),
        help="Output archive path.",
    )
    parser.add_argument(
        "--bundle-name",
        default=BUNDLE_ROOT_NAME,
        help="Top-level folder name inside the archive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = create_bundle_archive(
        Path(args.source_root),
        Path(args.output),
        bundle_name=args.bundle_name,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
