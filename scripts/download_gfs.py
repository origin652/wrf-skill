from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from datetime import datetime, timedelta
from http import client as http_client
from pathlib import Path
import time
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

TIME_FORMAT = "%Y-%m-%d_%H:%M:%S"
AWS_BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 2
DEFAULT_MAX_WORKERS = 4
CHUNK_SIZE = 1024 * 1024


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT)


def build_valid_times(start: str, end: str, interval_hours: int) -> list[datetime]:
    start_dt = parse_time(start)
    end_dt = parse_time(end)
    if end_dt < start_dt:
        raise ValueError("end must not be earlier than start")
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")

    current = start_dt
    valid_times: list[datetime] = []
    while current <= end_dt:
        valid_times.append(current)
        current += timedelta(hours=interval_hours)
    return valid_times


def build_manifest(
    *,
    start: str,
    end: str,
    interval_hours: int,
    resolution: str,
    cycle_hour: int | None = None,
    base_url: str = AWS_BASE_URL,
) -> dict[str, Any]:
    normalized_base_url = base_url.rstrip("/")
    valid_times = build_valid_times(start, end, interval_hours)
    first_time = valid_times[0]
    cycle = first_time.hour if cycle_hour is None else cycle_hour
    cycle_time = first_time.replace(hour=cycle, minute=0, second=0)
    if cycle_time > first_time:
        cycle_time -= timedelta(days=1)

    requests: list[dict[str, Any]] = []
    for valid_time in valid_times:
        forecast_hour = int((valid_time - cycle_time).total_seconds() // 3600)
        file_name = f"gfs.t{cycle:02d}z.pgrb2.{resolution}.f{forecast_hour:03d}"
        remote_path = f"gfs.{cycle_time:%Y%m%d}/{cycle:02d}/atmos/{file_name}"
        requests.append(
            {
                "valid_time": valid_time.strftime(TIME_FORMAT),
                "forecast_hour": forecast_hour,
                "file_name": file_name,
                "remote_path": remote_path,
                "url": f"{normalized_base_url}/{remote_path}",
            }
        )

    return {
        "source": "gfs",
        "start_time": start,
        "end_time": end,
        "interval_hours": interval_hours,
        "resolution": resolution,
        "cycle_hour": cycle,
        "base_url": normalized_base_url,
        "requests": requests,
    }


def write_manifest(manifest: dict[str, Any], output_path: Path | str) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    return target


def _backoff_seconds(attempt_index: int) -> float:
    return min(2 ** attempt_index, 5)


ProgressCallback = Callable[[dict[str, Any]], None]


def _summarize_results(results: list[dict[str, Any] | None], total_requests: int) -> dict[str, Any]:
    completed_results = [item for item in results if item is not None]
    downloaded_count = sum(1 for item in completed_results if item["status"] == "downloaded")
    skipped_count = sum(1 for item in completed_results if item["status"] == "skipped")
    failed_count = sum(1 for item in completed_results if item["status"] == "failed")
    total_bytes = sum(int(item.get("size_bytes", 0)) for item in completed_results if item["status"] != "failed")
    completed_count = len(completed_results)
    return {
        "downloaded_count": downloaded_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "total_requests": total_requests,
        "completed_count": completed_count,
        "remaining_count": total_requests - completed_count,
        "complete": completed_count == total_requests and failed_count == 0,
        "total_bytes": total_bytes,
    }


def _stream_response_to_path(response: Any, target_path: Path, *, mode: str) -> None:
    with target_path.open(mode) as handle:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            handle.write(chunk)


def _response_status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    if status is not None:
        return int(status)
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        return getcode()
    return None


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is not None:
        value = headers.get(name)
        if value is not None:
            return value
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        return getheader(name)
    return None


def _download_one(
    request_item: dict[str, Any],
    data_dir: Path,
    *,
    timeout: int,
    retries: int,
    overwrite: bool,
) -> dict[str, Any]:
    local_path = data_dir / request_item["file_name"]
    local_path.parent.mkdir(parents=True, exist_ok=True)
    size_bytes = local_path.stat().st_size if local_path.exists() else 0

    if local_path.exists() and size_bytes > 0 and not overwrite:
        record = dict(request_item)
        record.update(
            {
                "local_path": local_path.as_posix(),
                "exists": True,
                "size_bytes": size_bytes,
                "status": "skipped",
                "attempts": 0,
            }
        )
        return record

    temp_path = local_path.with_suffix(local_path.suffix + ".part")
    if overwrite and temp_path.exists():
        temp_path.unlink()
    last_error = ""
    for attempt in range(retries + 1):
        try:
            resume_from = temp_path.stat().st_size if temp_path.exists() else 0
            request = urllib_request.Request(request_item["url"])
            if resume_from > 0:
                request.add_header("Range", f"bytes={resume_from}-")

            with urllib_request.urlopen(request, timeout=timeout) as response:
                status = _response_status(response)
                content_range = _response_header(response, "Content-Range")
                supports_resume = status == 206 or (
                    content_range is not None and content_range.startswith(f"bytes {resume_from}-")
                )
                if resume_from > 0 and not supports_resume:
                    temp_path.unlink()
                    with urllib_request.urlopen(request_item["url"], timeout=timeout) as full_response:
                        _stream_response_to_path(full_response, temp_path, mode="wb")
                else:
                    _stream_response_to_path(response, temp_path, mode="ab" if resume_from > 0 else "wb")

            downloaded_size = temp_path.stat().st_size if temp_path.exists() else 0
            if downloaded_size <= 0:
                raise ValueError("Downloaded file is empty")
            temp_path.replace(local_path)

            record = dict(request_item)
            record.update(
                {
                    "local_path": local_path.as_posix(),
                    "exists": True,
                    "size_bytes": downloaded_size,
                    "status": "downloaded",
                    "attempts": attempt + 1,
                }
            )
            return record
        except (
            OSError,
            ValueError,
            http_client.IncompleteRead,
            urllib_error.URLError,
            urllib_error.HTTPError,
        ) as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(_backoff_seconds(attempt))

    record = dict(request_item)
    record.update(
        {
            "local_path": local_path.as_posix(),
            "exists": local_path.exists() and local_path.stat().st_size > 0,
            "size_bytes": local_path.stat().st_size if local_path.exists() else 0,
            "status": "failed",
            "attempts": retries + 1,
            "error": last_error,
        }
    )
    return record


def download_manifest(
    manifest: dict[str, Any],
    data_dir: Path | str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    max_workers: int = DEFAULT_MAX_WORKERS,
    overwrite: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    target_dir = Path(data_dir)
    requests = manifest["requests"]
    results: list[dict[str, Any] | None] = [None] * len(requests)

    def emit_progress(record: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        progress_callback({
            "record": dict(record),
            "summary": _summarize_results(results, len(requests)),
        })

    if max_workers <= 1:
        for index, item in enumerate(requests):
            results[index] = _download_one(
                item,
                target_dir,
                timeout=timeout,
                retries=retries,
                overwrite=overwrite,
            )
            emit_progress(results[index])
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(
                    _download_one,
                    item,
                    target_dir,
                    timeout=timeout,
                    retries=retries,
                    overwrite=overwrite,
                ): index
                for index, item in enumerate(requests)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                results[index] = future.result()
                emit_progress(results[index])

    final_results = [item for item in results if item is not None]
    return {
        "requests": final_results,
        "summary": _summarize_results(results, len(requests)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or stage GFS forcing requests")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval-hours", type=int, default=3)
    parser.add_argument("--resolution", default="0p25")
    parser.add_argument("--cycle-hour", type=int)
    parser.add_argument("--out", required=True, help="Manifest JSON output path")
    parser.add_argument("--data-dir", help="Directory to write downloaded GRIB files into")
    parser.add_argument("--base-url", default=AWS_BASE_URL)
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
        resolution=args.resolution,
        cycle_hour=args.cycle_hour,
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
                "data_dir": str(Path(args.data_dir)),
                "download": download_result["summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
