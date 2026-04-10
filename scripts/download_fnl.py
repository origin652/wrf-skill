from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

try:
    from download_gfs import (
        DEFAULT_MAX_WORKERS,
        DEFAULT_RETRIES,
        DEFAULT_TIMEOUT,
        TIME_FORMAT,
        build_valid_times,
        download_manifest,
        write_manifest,
    )
except ImportError:  # pragma: no cover
    from .download_gfs import (
        DEFAULT_MAX_WORKERS,
        DEFAULT_RETRIES,
        DEFAULT_TIMEOUT,
        TIME_FORMAT,
        build_valid_times,
        download_manifest,
        write_manifest,
    )

RDA_BASE_URL = "https://data.rda.ucar.edu/d083003"
FNL_CYCLE_HOURS = {0, 6, 12, 18}


def _ensure_supported_valid_time(valid_time: datetime) -> None:
    if valid_time.minute != 0 or valid_time.second != 0:
        raise ValueError("FNL forcing times must be aligned to exact hours")
    if valid_time.hour not in FNL_CYCLE_HOURS:
        raise ValueError("FNL forcing times must align to 00/06/12/18 UTC analyses")


def build_manifest(
    *,
    start: str,
    end: str,
    interval_hours: int,
    base_url: str | None = RDA_BASE_URL,
) -> dict[str, Any]:
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    if interval_hours % 6 != 0:
        raise ValueError("FNL forcing requires interval_hours to be a whole multiple of 6")

    normalized_base_url = str(base_url or RDA_BASE_URL).rstrip("/")
    valid_times = build_valid_times(start, end, interval_hours)

    requests: list[dict[str, Any]] = []
    for valid_time in valid_times:
        _ensure_supported_valid_time(valid_time)
        file_name = f"gdas1.fnl0p25.{valid_time:%Y%m%d%H}.f00.grib2"
        remote_path = f"{valid_time:%Y}/{valid_time:%Y%m}/{file_name}"
        requests.append(
            {
                "valid_time": valid_time.strftime(TIME_FORMAT),
                "file_name": file_name,
                "remote_path": remote_path,
                "url": f"{normalized_base_url}/{remote_path}",
            }
        )

    return {
        "source": "fnl",
        "start_time": start,
        "end_time": end,
        "interval_hours": interval_hours,
        "base_url": normalized_base_url,
        "requests": requests,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or stage FNL forcing requests")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval-hours", type=int, default=6)
    parser.add_argument("--out", required=True, help="Manifest JSON output path")
    parser.add_argument("--data-dir", help="Directory to write downloaded GRIB files into")
    parser.add_argument("--base-url", default=RDA_BASE_URL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = build_manifest(
        start=args.start,
        end=args.end,
        interval_hours=args.interval_hours,
        base_url=args.base_url,
    )
    target = write_manifest(manifest, args.out)
    if args.dry_run:
        print(json.dumps({"manifest": str(target), "requests": len(manifest["requests"])}, indent=2))
        return 0

    if not args.data_dir:
        raise SystemExit("--data-dir is required unless --dry-run is set")

    download_result = download_manifest(
        manifest,
        args.data_dir,
        timeout=args.timeout,
        retries=args.retries,
        max_workers=args.max_workers,
        overwrite=args.overwrite,
    )
    manifest["requests"] = download_result["requests"]
    manifest["download"] = download_result["summary"]
    target = write_manifest(manifest, args.out)

    print(
        json.dumps(
            {
                "manifest": str(target),
                "requests": len(manifest["requests"]),
                "data_dir": str(args.data_dir),
                "download": download_result["summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
