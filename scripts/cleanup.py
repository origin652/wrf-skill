#!/usr/bin/env python3
"""Clean up temporary directories and orphaned files in the runs/ directory."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Any

try:
    from utils import load_json, repo_root
except ImportError:
    from .utils import load_json, repo_root


def is_temp_directory(path: Path) -> bool:
    """Check if directory appears to be a temporary directory."""
    name = path.name
    # Match patterns like tmp*, temp*, or UUID-like names
    return (
        name.startswith("tmp")
        or name.startswith("temp")
        or name.startswith("_")
        or (len(name) > 8 and all(c in "0123456789abcdef_-" for c in name.lower()))
    )


def is_stale_directory(path: Path, max_age_hours: float = 24.0) -> bool:
    """Check if directory hasn't been modified recently."""
    try:
        mtime = path.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        return age_hours > max_age_hours
    except (OSError, PermissionError):
        return False


def has_project_json(path: Path) -> bool:
    """Check if directory contains a valid project.json."""
    project_file = path / "project.json"
    if not project_file.exists():
        return False
    try:
        data = load_json(project_file)
        return isinstance(data, dict) and "project_name" in data
    except Exception:
        return False


def find_cleanable_directories(
    runs_dir: Path,
    *,
    include_temp: bool = True,
    include_stale: bool = False,
    max_age_hours: float = 24.0,
) -> list[Path]:
    """Find directories that can be safely cleaned."""
    cleanable = []

    if not runs_dir.exists():
        return cleanable

    for item in runs_dir.iterdir():
        if not item.is_dir():
            continue

        # Skip .gitkeep and hidden directories
        if item.name.startswith("."):
            continue

        # Check if it's a valid project directory
        if has_project_json(item):
            # Valid project, only clean if stale and requested
            if include_stale and is_stale_directory(item, max_age_hours):
                cleanable.append(item)
        elif include_temp and is_temp_directory(item):
            # Temp directory without valid project
            cleanable.append(item)

    return cleanable


def clean_directory(path: Path, *, dry_run: bool = False) -> bool:
    """Remove directory and its contents."""
    if dry_run:
        print(f"[DRY RUN] Would remove: {path}")
        return True

    try:
        shutil.rmtree(path)
        print(f"Removed: {path}")
        return True
    except PermissionError:
        print(f"Permission denied: {path}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error removing {path}: {e}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Clean up temporary and stale directories in runs/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what would be cleaned
  python3 scripts/cleanup.py --dry-run

  # Clean temporary directories only
  python3 scripts/cleanup.py

  # Clean temp and stale directories (older than 48 hours)
  python3 scripts/cleanup.py --include-stale --max-age 48
        """,
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Path to runs directory (default: <repo>/runs)",
    )
    parser.add_argument(
        "--include-stale",
        action="store_true",
        help="Also clean stale project directories",
    )
    parser.add_argument(
        "--max-age",
        type=float,
        default=24.0,
        help="Maximum age in hours for stale directories (default: 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleaned without actually removing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )

    args = parser.parse_args(argv)

    runs_dir = args.runs_dir or (repo_root() / "runs")

    if not runs_dir.exists():
        print(f"Runs directory does not exist: {runs_dir}")
        return 0

    cleanable = find_cleanable_directories(
        runs_dir,
        include_temp=True,
        include_stale=args.include_stale,
        max_age_hours=args.max_age,
    )

    if not cleanable:
        print("No directories to clean.")
        return 0

    print(f"Found {len(cleanable)} director{'y' if len(cleanable) == 1 else 'ies'} to clean:")
    for path in cleanable:
        print(f"  - {path.name}")

    if not args.force and not args.dry_run:
        response = input("\nProceed with cleanup? [y/N]: ")
        if response.lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 0

    success_count = 0
    for path in cleanable:
        if clean_directory(path, dry_run=args.dry_run):
            success_count += 1

    if args.dry_run:
        print(f"\n[DRY RUN] Would clean {success_count}/{len(cleanable)} directories")
    else:
        print(f"\nCleaned {success_count}/{len(cleanable)} directories")

    return 0 if success_count == len(cleanable) else 1


if __name__ == "__main__":
    raise SystemExit(main())
