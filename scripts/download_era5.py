from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from datetime import datetime, timedelta
from pathlib import Path
import time
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

TIME_FORMAT = "%Y-%m-%d_%H:%M:%S"
PRESSURE_DATASET = "reanalysis-era5-pressure-levels"
SINGLE_DATASET = "reanalysis-era5-single-levels"
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 2
DEFAULT_MAX_WORKERS = 4
CHUNK_SIZE = 1024 * 1024
DEFAULT_PRESSURE_LEVELS = [
    "1",
    "2",
    "3",
    "5",
    "7",
    "10",
    "20",
    "30",
    "50",
    "70",
    "100",
    "125",
    "150",
    "175",
    "200",
    "225",
    "250",
    "300",
    "350",
    "400",
    "450",
    "500",
    "550",
    "600",
    "650",
    "700",
    "750",
    "775",
    "800",
    "825",
    "850",
    "875",
    "900",
    "925",
    "950",
    "975",
    "1000",
]
DEFAULT_PRESSURE_VARIABLES = [
    "geopotential",
    "relative_humidity",
    "temperature",
    "u_component_of_wind",
    "v_component_of_wind",
]
DEFAULT_SINGLE_VARIABLES = [
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "2m_dewpoint_temperature",
    "2m_temperature",
    "geopotential",
    "land_sea_mask",
    "mean_sea_level_pressure",
    "sea_ice_cover",
    "sea_surface_temperature",
    "skin_temperature",
    "snow_depth",
    "soil_temperature_level_1",
    "soil_temperature_level_2",
    "soil_temperature_level_3",
    "soil_temperature_level_4",
    "surface_pressure",
    "volumetric_soil_water_layer_1",
    "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3",
    "volumetric_soil_water_layer_4",
]


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


def _group_times_by_day(valid_times: list[datetime]) -> list[tuple[datetime, list[datetime]]]:
    grouped: list[tuple[datetime, list[datetime]]] = []
    current_day: datetime | None = None
    bucket: list[datetime] = []

    for valid_time in valid_times:
        day = valid_time.replace(hour=0, minute=0, second=0, microsecond=0)
        if current_day is None or day != current_day:
            if current_day is not None:
                grouped.append((current_day, bucket))
            current_day = day
            bucket = [valid_time]
        else:
            bucket.append(valid_time)

    if current_day is not None:
        grouped.append((current_day, bucket))
    return grouped


def _build_request(
    *,
    kind: str,
    dataset: str,
    variables: list[str],
    date: datetime,
    valid_times: list[datetime],
    times: list[str],
    pressure_levels: list[str] | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    date_token = date.strftime("%Y%m%d")
    file_name = f"era5.{kind}.{date_token}.grib"
    request_payload: dict[str, Any] = {
        "product_type": "reanalysis",
        "variable": variables,
        "year": f"{date:%Y}",
        "month": f"{date:%m}",
        "day": f"{date:%d}",
        "time": times,
        "data_format": "grib",
    }
    if pressure_levels:
        request_payload["pressure_level"] = pressure_levels

    record = {
        "date": f"{date:%Y-%m-%d}",
        "valid_times": [valid_time.strftime(TIME_FORMAT) for valid_time in valid_times],
        "times": list(times),
        "kind": kind,
        "dataset": dataset,
        "file_name": file_name,
        "request": request_payload,
    }
    if base_url is not None:
        remote_path = f"{kind}/{file_name}"
        record["remote_path"] = remote_path
        record["url"] = f"{base_url}/{remote_path}"
    return record


def build_manifest(
    *,
    start: str,
    end: str,
    interval_hours: int,
    base_url: str | None = None,
    pressure_dataset: str = PRESSURE_DATASET,
    single_dataset: str = SINGLE_DATASET,
) -> dict[str, Any]:
    valid_times = build_valid_times(start, end, interval_hours)
    normalized_base_url = base_url.rstrip("/") if base_url else None
    requests: list[dict[str, Any]] = []

    for day, times_for_day in _group_times_by_day(valid_times):
        time_tokens = [f"{valid_time:%H}:00" for valid_time in times_for_day]
        requests.append(
            _build_request(
                kind="pressure",
                dataset=pressure_dataset,
                variables=list(DEFAULT_PRESSURE_VARIABLES),
                date=day,
                valid_times=times_for_day,
                times=time_tokens,
                pressure_levels=list(DEFAULT_PRESSURE_LEVELS),
                base_url=normalized_base_url,
            )
        )
        requests.append(
            _build_request(
                kind="single",
                dataset=single_dataset,
                variables=list(DEFAULT_SINGLE_VARIABLES),
                date=day,
                valid_times=times_for_day,
                times=time_tokens,
                base_url=normalized_base_url,
            )
        )

    payload: dict[str, Any] = {
        "source": "era5",
        "transport": "url" if normalized_base_url else "cdsapi",
        "start_time": start,
        "end_time": end,
        "interval_hours": interval_hours,
        "pressure_dataset": pressure_dataset,
        "single_dataset": single_dataset,
        "pressure_levels": list(DEFAULT_PRESSURE_LEVELS),
        "pressure_variables": list(DEFAULT_PRESSURE_VARIABLES),
        "single_variables": list(DEFAULT_SINGLE_VARIABLES),
        "requests": requests,
    }
    if normalized_base_url:
        payload["base_url"] = normalized_base_url
    return payload


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def _download_url_to_path(url: str, target_path: Path, *, timeout: int) -> None:
    with urllib_request.urlopen(url, timeout=timeout) as response, target_path.open("wb") as handle:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            handle.write(chunk)


def _download_cdsapi_to_path(
    dataset: str,
    request_payload: dict[str, Any],
    target_path: Path,
    *,
    timeout: int,
) -> None:
    try:
        import cdsapi
    except ImportError as exc:  # pragma: no cover - exercised only in real ERA5 environments
        raise RuntimeError(
            "ERA5 downloads require the cdsapi package; install it and configure ~/.cdsapirc"
        ) from exc

    client = cdsapi.Client(timeout=timeout, quiet=True, progress=False)
    client.retrieve(dataset, request_payload, str(target_path))


def _cleanup_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _download_one(
    request_item: dict[str, Any],
    data_dir: Path,
    *,
    timeout: int,
    retries: int,
    overwrite: bool,
    transport: str,
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
    last_error = ""
    for attempt in range(retries + 1):
        try:
            _cleanup_paths([temp_path])
            if transport == "url":
                _download_url_to_path(request_item["url"], temp_path, timeout=timeout)
            elif transport == "cdsapi":
                _download_cdsapi_to_path(
                    request_item["dataset"],
                    request_item["request"],
                    temp_path,
                    timeout=timeout,
                )
            else:
                raise ValueError(f"Unsupported ERA5 transport: {transport}")

            downloaded_size = temp_path.stat().st_size if temp_path.exists() else 0
            if downloaded_size <= 0:
                raise ValueError("Downloaded ERA5 file is empty")
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
        except (OSError, ValueError, RuntimeError, urllib_error.URLError, urllib_error.HTTPError) as exc:
            last_error = str(exc)
            _cleanup_paths([temp_path])
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
    transport = str(manifest.get("transport") or "cdsapi").lower()

    def emit_progress(record: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "record": dict(record),
                "summary": _summarize_results(results, len(requests)),
            }
        )

    if max_workers <= 1:
        for index, item in enumerate(requests):
            results[index] = _download_one(
                item,
                target_dir,
                timeout=timeout,
                retries=retries,
                overwrite=overwrite,
                transport=transport,
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
                    transport=transport,
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
    parser = argparse.ArgumentParser(description="Plan or stage ERA5 forcing requests")
    parser.add_argument("--manifest", help="Existing manifest JSON path to execute")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--interval-hours", type=int, default=3)
    parser.add_argument("--out", help="Manifest JSON output path")
    parser.add_argument("--data-dir", help="Directory to write downloaded GRIB files into")
    parser.add_argument("--base-url")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.manifest:
        target = Path(args.manifest)
        manifest = load_json(target)
    else:
        if not args.start or not args.end or not args.out:
            raise SystemExit("--start, --end, and --out are required unless --manifest is set")
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
    target = write_manifest(manifest, target)

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
