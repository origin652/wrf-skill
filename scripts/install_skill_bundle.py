from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from skill_bundle import BUNDLE_ROOT_NAME, install_bundle, repo_root
except ImportError:  # pragma: no cover
    from .skill_bundle import BUNDLE_ROOT_NAME, install_bundle, repo_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the packaged WRF skill bundle into a target directory.")
    parser.add_argument(
        "--source-root",
        default=repo_root().as_posix(),
        help="Bundle root or source repository root. Defaults to the current repo.",
    )
    parser.add_argument("--target", required=True, help="Target directory to install into.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite bundled files already present in the target directory.",
    )
    parser.add_argument(
        "--bundle-name",
        default=BUNDLE_ROOT_NAME,
        help="Reserved for compatibility with future bundle variants.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = install_bundle(Path(args.source_root), Path(args.target), force=args.force)
    payload["bundle_name"] = args.bundle_name
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
