from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from download_era5 import (
        build_manifest as build_era5_manifest,
        download_manifest as download_era5_manifest,
    )
    from download_fnl import (
        build_manifest as build_fnl_manifest,
        download_manifest as download_fnl_manifest,
    )
    from download_gfs import (
        build_manifest as build_gfs_manifest,
        download_manifest as download_gfs_manifest,
    )
    from project_state import (
        clear_error,
        load_project,
        posix_path,
        record_error,
        register_artifact,
        save_project,
        transition,
    )
    from spec_utils import normalize_spec
except ImportError:  # pragma: no cover
    from .download_era5 import (
        build_manifest as build_era5_manifest,
        download_manifest as download_era5_manifest,
    )
    from .download_fnl import (
        build_manifest as build_fnl_manifest,
        download_manifest as download_fnl_manifest,
    )
    from .download_gfs import (
        build_manifest as build_gfs_manifest,
        download_manifest as download_gfs_manifest,
    )
    from .project_state import (
        clear_error,
        load_project,
        posix_path,
        record_error,
        register_artifact,
        save_project,
        transition,
    )
    from .spec_utils import normalize_spec


DOWNLOAD_HANDLERS = {
    "gfs": {
        "build_manifest": build_gfs_manifest,
        "download_manifest": download_gfs_manifest,
        "script_name": "download_gfs.sh",
    },
    "fnl": {
        "build_manifest": build_fnl_manifest,
        "download_manifest": download_fnl_manifest,
        "script_name": "download_fnl.sh",
    },
    "era5": {
        "build_manifest": build_era5_manifest,
        "download_manifest": download_era5_manifest,
        "script_name": "download_era5.sh",
    },
}
REQUEST_RUNTIME_KEYS = {
    "attempts",
    "error",
    "exists",
    "existing_size_bytes",
    "local_path",
    "replacement_reason",
    "size_bytes",
    "status",
}


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path | str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def ensure_supported_source(source: str) -> str:
    normalized = str(source).lower()
    if normalized not in DOWNLOAD_HANDLERS:
        raise NotImplementedError(
            f"wrf-data currently supports only {', '.join(sorted(DOWNLOAD_HANDLERS))}, received: {source}"
        )
    return normalized


def _stable_request_view(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_request_view(item)
            for key, item in sorted(value.items())
            if key not in REQUEST_RUNTIME_KEYS
        }
    if isinstance(value, list):
        return [_stable_request_view(item) for item in value]
    return value


def load_existing_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_request_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(manifest, dict):
        return {}
    requests = manifest.get("requests")
    if not isinstance(requests, list):
        return {}

    indexed: dict[str, dict[str, Any]] = {}
    for item in requests:
        if isinstance(item, dict) and item.get("file_name"):
            indexed[str(item["file_name"])] = item
    return indexed


def requests_are_compatible(request_item: dict[str, Any], previous_item: dict[str, Any]) -> bool:
    return _stable_request_view(request_item) == _stable_request_view(previous_item)


def determine_interval_hours(
    raw_spec: dict[str, Any],
    spec: dict[str, Any],
    base_state: dict[str, Any],
    interval_hours: int | None,
) -> int:
    if interval_hours is not None:
        effective_interval = int(interval_hours)
    else:
        explicit_seconds = None
        raw_timing = raw_spec.get("timing")
        if isinstance(raw_timing, dict) and raw_timing.get("forcing_interval_seconds") is not None:
            explicit_seconds = raw_timing.get("forcing_interval_seconds")
        if explicit_seconds is None:
            raw_share = raw_spec.get("wps", {}).get("share") if isinstance(raw_spec.get("wps"), dict) else None
            if isinstance(raw_share, dict) and raw_share.get("interval_seconds") is not None:
                explicit_seconds = raw_share.get("interval_seconds")

        if explicit_seconds is None:
            interval_seconds = spec.get("timing", {}).get("forcing_interval_seconds")
        else:
            interval_seconds = explicit_seconds

        if interval_seconds is None:
            effective_interval = int(base_state["data_source"].get("interval_hours") or 3)
        else:
            effective_seconds = int(interval_seconds)
            if effective_seconds <= 0:
                raise ValueError("forcing_interval_seconds must be positive")
            if effective_seconds % 3600 != 0:
                raise ValueError(
                    "wrf-data currently requires forcing_interval_seconds to be a whole number of hours"
                )
            effective_interval = effective_seconds // 3600

    if effective_interval <= 0:
        raise ValueError("interval_hours must be positive")
    return effective_interval


def build_inventory(
    manifest: dict[str, Any],
    data_dir: Path,
    *,
    previous_manifest: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    requests: list[dict[str, Any]] = []
    existing_files: list[str] = []
    missing_files: list[str] = []
    replacement_files: list[str] = []
    previous_requests = build_request_index(previous_manifest)
    previous_requests_known = previous_manifest is not None and isinstance(previous_manifest.get("requests"), list)

    for request in manifest["requests"]:
        local_path = data_dir / request["file_name"]
        size_bytes = local_path.stat().st_size if local_path.exists() else 0
        file_exists = local_path.exists() and size_bytes > 0
        request_status = str(request.get("status") or "").lower()
        replacement_reason: str | None = None

        if request_status == "failed":
            replacement_reason = str(request.get("replacement_reason") or "download_failed")
        elif overwrite and file_exists:
            replacement_reason = "overwrite_requested"
        elif file_exists and previous_requests_known:
            previous_request = previous_requests.get(str(request["file_name"]))
            if previous_request is None or not requests_are_compatible(request, previous_request):
                replacement_reason = "manifest_changed"

        reusable = file_exists and replacement_reason is None
        record = deepcopy(request)
        record["local_path"] = posix_path(local_path)
        record["exists"] = reusable
        record["size_bytes"] = size_bytes if file_exists else 0
        if replacement_reason is not None:
            record["replacement_reason"] = replacement_reason
            if file_exists:
                record["existing_size_bytes"] = size_bytes

        requests.append(record)
        if reusable:
            existing_files.append(record["local_path"])
        else:
            missing_files.append(record["local_path"])
        if file_exists and replacement_reason is not None:
            replacement_files.append(record["local_path"])

    return {
        "requests": requests,
        "existing_files": existing_files,
        "missing_files": missing_files,
        "existing_count": len(existing_files),
        "missing_count": len(missing_files),
        "replacement_files": replacement_files,
        "replacement_count": len(replacement_files),
        "overwrite_required": bool(overwrite or replacement_files),
        "complete": len(missing_files) == 0,
    }


def summarize_existing_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    total_bytes = sum(int(item.get("size_bytes", 0)) for item in inventory["requests"] if item.get("exists"))
    return {
        "downloaded_count": 0,
        "skipped_count": inventory["existing_count"],
        "failed_count": 0,
        "total_requests": inventory["existing_count"] + inventory["missing_count"],
        "completed_count": inventory["existing_count"],
        "remaining_count": inventory["missing_count"],
        "complete": inventory["missing_count"] == 0,
        "total_bytes": total_bytes,
    }


def combine_download_summaries(
    inventory: dict[str, Any],
    download_summary: dict[str, Any],
) -> dict[str, Any]:
    existing_summary = summarize_existing_inventory(inventory)
    return {
        "downloaded_count": int(download_summary.get("downloaded_count", 0)),
        "skipped_count": existing_summary["skipped_count"] + int(download_summary.get("skipped_count", 0)),
        "failed_count": int(download_summary.get("failed_count", 0)),
        "total_requests": existing_summary["total_requests"],
        "completed_count": existing_summary["skipped_count"] + int(download_summary.get("completed_count", 0)),
        "remaining_count": int(download_summary.get("remaining_count", 0)),
        "complete": bool(download_summary.get("complete", False) and int(download_summary.get("remaining_count", 0)) == 0),
        "total_bytes": existing_summary["total_bytes"] + int(download_summary.get("total_bytes", 0)),
    }


def enrich_manifest(
    project_name: str,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    *,
    data_dir: Path,
    download_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = deepcopy(manifest)
    enriched["project_name"] = project_name
    enriched["data_dir"] = posix_path(data_dir)
    enriched["requests"] = inventory["requests"]
    enriched["summary"] = {
        "existing_count": inventory["existing_count"],
        "missing_count": inventory["missing_count"],
        "replacement_count": inventory["replacement_count"],
        "overwrite_required": inventory["overwrite_required"],
        "complete": inventory["complete"],
    }
    if download_summary is not None:
        enriched["download"] = download_summary
    return enriched


def merge_download_results(
    manifest: dict[str, Any],
    download_result: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(manifest)
    result_map = {
        item["file_name"]: item
        for item in download_result["requests"]
    }
    merged["requests"] = [
        {**request, **result_map.get(request["file_name"], {})}
        for request in merged["requests"]
    ]
    return merged


def write_download_script(manifest: dict[str, Any], script_path: Path) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f'mkdir -p "{posix_path(script_path.parent)}"',
        "",
    ]
    source = str(manifest.get("source") or "").lower()
    missing_requests = [item for item in manifest["requests"] if not item["exists"]]
    overwrite_required = any(item.get("replacement_reason") for item in manifest["requests"])
    if not missing_requests:
        lines.append(f'echo "All planned {source.upper()} files are already present."')
    elif source in {"gfs", "fnl"}:
        for request in missing_requests:
            lines.append(
                f'curl -fL -C - "{request["url"]}" -o "{request["local_path"]}.part"'
            )
            lines.append(
                f'mv "{request["local_path"]}.part" "{request["local_path"]}"'
            )
    elif source == "era5":
        downloader_path = Path(__file__).resolve().parent / "download_era5.py"
        manifest_path = script_path.parent / "data_manifest.json"
        command_parts = [
            f'python3 "{posix_path(downloader_path)}"',
            f'--manifest "{posix_path(manifest_path)}"',
            f'--data-dir "{posix_path(script_path.parent)}"',
        ]
        if overwrite_required:
            command_parts.append("--overwrite")
        lines.append(" \\\n  ".join(command_parts))
    else:
        raise NotImplementedError(f"Unsupported download script source: {source}")

    script_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def append_log_lines(log_path: Path, lines: list[str]) -> None:
    if not lines:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(f"{line.rstrip()}\n")


def format_progress_line(progress: dict[str, Any]) -> str:
    record = progress["record"]
    summary = progress["summary"]
    line = (
        "progress "
        f"completed={summary['completed_count']}/{summary['total_requests']} "
        f"remaining={summary['remaining_count']} "
        f"downloaded={summary['downloaded_count']} "
        f"skipped={summary['skipped_count']} "
        f"failed={summary['failed_count']} "
        f"file={record['file_name']} "
        f"status={record['status']} "
        f"attempts={record.get('attempts', 0)} "
        f"size_bytes={record.get('size_bytes', 0)}"
    )
    if record.get("error"):
        line += f" error={record['error']}"
    return line


def build_plan(
    project_root: Path,
    manifest_path: Path,
    download_script_path: Path,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project_root": posix_path(project_root),
        "manifest_path": posix_path(manifest_path),
        "download_script": posix_path(download_script_path),
        "existing_count": inventory["existing_count"],
        "missing_count": inventory["missing_count"],
        "replacement_count": inventory["replacement_count"],
        "overwrite_required": inventory["overwrite_required"],
        "complete": inventory["complete"],
        "missing_files": inventory["missing_files"],
        "replacement_files": inventory["replacement_files"],
    }


def update_project_for_data(
    state: dict[str, Any],
    manifest_path: Path,
    inventory: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    state["execution"]["dry_run"] = dry_run
    clear_error(state)
    register_artifact(state, "data_manifest", posix_path(manifest_path))
    state["artifacts"]["forcing_files"] = []
    for path in inventory["existing_files"]:
        register_artifact(state, "forcing_files", path)

    if inventory["complete"]:
        transition(state, "data_ready", current_step="wrf-data", allow_retry=True)
    else:
        state["status"] = "configured"
        state["current_step"] = "wrf-data"
    return state


def prepare_data(
    project_name: str,
    *,
    runs_dir: Path | str = "runs",
    resolution: str = "0p25",
    cycle_hour: int | None = None,
    interval_hours: int | None = None,
    base_url: str | None = None,
    timeout: int = 60,
    retries: int = 2,
    max_workers: int = 4,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    project_root = runs_dir / project_name
    project_json_path = project_root / "project.json"
    spec_path = project_root / "simulation_spec.json"

    if not project_json_path.exists():
        raise FileNotFoundError(f"Missing project.json: {project_json_path}")
    if not spec_path.exists():
        raise FileNotFoundError(f"Missing simulation spec: {spec_path}")

    base_state = load_project(project_json_path)
    raw_spec = load_json(spec_path)
    spec = normalize_spec(raw_spec)
    source = ensure_supported_source(spec["data_source"])

    effective_interval = determine_interval_hours(raw_spec, spec, base_state, interval_hours)
    data_dir = Path(base_state["paths"]["data_dir"])
    manifest_path = data_dir / "data_manifest.json"
    download_script_path = data_dir / DOWNLOAD_HANDLERS[source]["script_name"]
    log_path = Path(base_state["paths"]["log_dir"]) / "wrf-data.log"

    build_manifest = DOWNLOAD_HANDLERS[source]["build_manifest"]
    download_manifest = DOWNLOAD_HANDLERS[source]["download_manifest"]
    if source == "gfs":
        manifest = build_manifest(
            start=spec["timing"]["start_time"],
            end=spec["timing"]["end_time"],
            interval_hours=effective_interval,
            resolution=resolution,
            cycle_hour=cycle_hour,
            base_url=base_url or "https://noaa-gfs-bdp-pds.s3.amazonaws.com",
        )
    else:
        manifest = build_manifest(
            start=spec["timing"]["start_time"],
            end=spec["timing"]["end_time"],
            interval_hours=effective_interval,
            base_url=base_url,
        )

    previous_manifest = load_existing_manifest(manifest_path)
    inventory = build_inventory(
        manifest,
        data_dir,
        previous_manifest=previous_manifest,
        overwrite=overwrite,
    )
    enriched_manifest = enrich_manifest(
        project_name,
        manifest,
        inventory,
        data_dir=data_dir,
    )
    state = deepcopy(base_state)
    state["data_source"]["type"] = spec["data_source"]
    state["data_source"]["start_time"] = spec["timing"]["start_time"]
    state["data_source"]["end_time"] = spec["timing"]["end_time"]
    state["data_source"]["interval_hours"] = effective_interval
    update_project_for_data(state, manifest_path, inventory, dry_run=dry_run)

    plan = build_plan(project_root, manifest_path, download_script_path, inventory)

    if dry_run:
        return {
            "dry_run": True,
            "project": state,
            "manifest": enriched_manifest,
            "plan": plan,
        }

    data_dir.mkdir(parents=True, exist_ok=True)
    dump_json(manifest_path, enriched_manifest)
    write_download_script(enriched_manifest, download_script_path)
    save_project(state, project_json_path)

    start_log_lines = [
        f"wrf-data project={project_name}",
        f"source={spec['data_source']}",
        f"start={spec['timing']['start_time']}",
        f"end={spec['timing']['end_time']}",
        f"interval_hours={effective_interval}",
        f"transport={manifest.get('transport', 'url')}",
        f"existing_count={inventory['existing_count']}",
        f"missing_count={inventory['missing_count']}",
        f"replacement_count={inventory['replacement_count']}",
        f"overwrite_enabled={inventory['overwrite_required']}",
        f"manifest={posix_path(manifest_path)}",
        f"download_script={posix_path(download_script_path)}",
        f"max_workers={max_workers}",
    ]
    if source == "gfs":
        start_log_lines.append(f"resolution={resolution}")
    if manifest.get("base_url"):
        start_log_lines.append(f"base_url={manifest['base_url']}")
    if manifest.get("pressure_dataset") and manifest.get("single_dataset"):
        start_log_lines.append(f"pressure_dataset={manifest['pressure_dataset']}")
        start_log_lines.append(f"single_dataset={manifest['single_dataset']}")
    start_log_lines.append("phase=starting")
    write_log(log_path, start_log_lines)
    print(
        f"wrf-data start project={project_name} total_requests={len(manifest['requests'])} "
        f"missing={inventory['missing_count']} replacements={inventory['replacement_count']}",
        flush=True,
    )

    def on_progress(progress: dict[str, Any]) -> None:
        line = format_progress_line(progress)
        append_log_lines(log_path, [line])
        print(line, flush=True)

    pending_manifest = deepcopy(manifest)
    pending_manifest["requests"] = [
        {
            key: value
            for key, value in request.items()
            if key not in REQUEST_RUNTIME_KEYS
        }
        for request in inventory["requests"]
        if not request["exists"]
    ]

    if pending_manifest["requests"]:
        pending_result = download_manifest(
            pending_manifest,
            data_dir,
            timeout=timeout,
            retries=retries,
            max_workers=max_workers,
            overwrite=inventory["overwrite_required"],
            progress_callback=on_progress,
        )
    else:
        pending_result = {"requests": [], "summary": {}}

    download_summary = combine_download_summaries(inventory, pending_result["summary"])
    merged_manifest = merge_download_results(manifest, pending_result)
    inventory = build_inventory(
        merged_manifest,
        data_dir,
        previous_manifest=manifest,
    )
    enriched_manifest = enrich_manifest(
        project_name,
        merged_manifest,
        inventory,
        data_dir=data_dir,
        download_summary=download_summary,
    )
    state = deepcopy(base_state)
    state["data_source"]["type"] = spec["data_source"]
    state["data_source"]["start_time"] = spec["timing"]["start_time"]
    state["data_source"]["end_time"] = spec["timing"]["end_time"]
    state["data_source"]["interval_hours"] = effective_interval
    update_project_for_data(state, manifest_path, inventory, dry_run=False)
    plan = build_plan(project_root, manifest_path, download_script_path, inventory)

    dump_json(manifest_path, enriched_manifest)
    write_download_script(enriched_manifest, download_script_path)
    save_project(state, project_json_path)
    append_log_lines(
        log_path,
        [
            "phase=finished",
            f"downloaded_count={download_summary['downloaded_count']}",
            f"skipped_count={download_summary['skipped_count']}",
            f"failed_count={download_summary['failed_count']}",
            f"complete={inventory['complete']}",
            f"existing_count={inventory['existing_count']}",
            f"missing_count={inventory['missing_count']}",
            f"replacement_count={inventory['replacement_count']}",
        ],
    )

    if not inventory["complete"]:
        record_error(
            state,
            "wrf-data",
            "DOWNLOAD_INCOMPLETE",
            f"{inventory['missing_count']} forcing files are still missing after download",
            posix_path(log_path),
        )
        save_project(state, project_json_path)
        raise RuntimeError("Forcing download incomplete; see wrf-data.log and data_manifest.json")

    return {
        "dry_run": False,
        "project": state,
        "manifest_path": posix_path(manifest_path),
        "download_script": posix_path(download_script_path),
        "log_path": posix_path(log_path),
        "download": download_summary,
        "plan": plan,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare forcing data for a WRF project")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--resolution", default="0p25")
    parser.add_argument("--cycle-hour", type=int)
    parser.add_argument("--interval-hours", type=int)
    parser.add_argument("--base-url")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = prepare_data(
        args.project_name,
        runs_dir=args.runs_dir,
        resolution=args.resolution,
        cycle_hour=args.cycle_hour,
        interval_hours=args.interval_hours,
        base_url=args.base_url,
        timeout=args.timeout,
        retries=args.retries,
        max_workers=args.max_workers,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
