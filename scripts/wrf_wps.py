from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from namelist_parser import read_namelist
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
    from .namelist_parser import read_namelist
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

TIME_FORMAT = "%Y-%m-%d_%H:%M:%S"
STEP_CODE_MAP = {
    "geogrid": "GEOGRID_FAILED",
    "link_grib": "LINK_GRIB_FAILED",
    "ungrib": "UNGRIB_FAILED",
    "metgrid": "METGRID_FAILED",
}
VTABLE_BY_SOURCE = {
    "gfs": "Vtable.GFS",
    "fnl": "Vtable.GFS",
    "era5": "Vtable.ECMWF",
}
REPO_SUPPORT_ROOT = Path(__file__).resolve().parents[1] / "third_party" / "wps-support"
SUPPORT_TARGETS = {
    "GEOGRID.TBL": Path("geogrid") / "GEOGRID.TBL",
    "METGRID.TBL": Path("metgrid") / "METGRID.TBL",
    "Vtable": Path("Vtable"),
}


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def _scalar(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, list):
        return value[0] if value else default
    return value


def determine_interval_hours(
    state: dict[str, Any],
    namelist: dict[str, dict[str, Any]],
) -> int:
    interval_seconds = _scalar(namelist.get("share", {}).get("interval_seconds"))
    if interval_seconds is not None:
        return max(1, int(interval_seconds) // 3600)
    return int(state["data_source"].get("interval_hours") or 3)


def collect_forcing_files(state: dict[str, Any]) -> list[Path]:
    forcing_files: list[Path] = []
    for raw_path in state["artifacts"].get("forcing_files", []):
        path = Path(raw_path)
        if path.exists() and path.stat().st_size > 0 and path not in forcing_files:
            forcing_files.append(path)

    manifest_path = state["artifacts"].get("data_manifest")
    if manifest_path and Path(manifest_path).exists():
        manifest = load_json(manifest_path)
        for request in manifest.get("requests", []):
            local_path = request.get("local_path")
            if not local_path:
                continue
            path = Path(local_path)
            if path.exists() and path.stat().st_size > 0 and path not in forcing_files:
                forcing_files.append(path)

    return forcing_files


def expected_met_em_paths(
    spec: dict[str, Any],
    namelist: dict[str, dict[str, Any]],
    wps_dir: Path,
    *,
    interval_hours: int,
) -> list[Path]:
    normalized = normalize_spec(spec)
    domain_count = int(_scalar(namelist.get("share", {}).get("max_dom"), len(normalized["domains"])))
    valid_times = build_valid_times(normalized["timing"]["start_time"], normalized["timing"]["end_time"], interval_hours)
    expected: list[Path] = []
    for domain_index in range(1, domain_count + 1):
        for valid_time in valid_times:
            expected.append(
                wps_dir / f"met_em.d{domain_index:02d}.{valid_time.strftime(TIME_FORMAT)}.nc"
            )
    return expected


def build_output_inventory(paths: list[Path]) -> dict[str, Any]:
    existing_files: list[str] = []
    missing_files: list[str] = []
    for path in paths:
        if path.exists() and path.stat().st_size >= 0:
            existing_files.append(posix_path(path))
        else:
            missing_files.append(posix_path(path))

    return {
        "existing_files": existing_files,
        "missing_files": missing_files,
        "existing_count": len(existing_files),
        "missing_count": len(missing_files),
        "complete": len(missing_files) == 0,
    }


def _first_existing_path(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _source_vtable_override(overrides: dict[str, Any], data_source: str, expected_name: str) -> str | None:
    by_source = overrides.get("vtable_by_source")
    if isinstance(by_source, dict):
        value = by_source.get(data_source)
        if value:
            return str(value)

    value = overrides.get(f"vtable_{data_source}")
    if value:
        return str(value)

    generic = overrides.get("vtable")
    if generic and Path(str(generic)).name == expected_name:
        return str(generic)
    return None


def resolve_support_sources(
    config: dict[str, Any],
    wps_root: Path,
    data_source: str,
) -> dict[str, Path]:
    overrides = config.get("wps_tables", {})
    source = str(data_source).lower()
    vtable_name = VTABLE_BY_SOURCE.get(source)
    if vtable_name is None:
        raise NotImplementedError(f"Unsupported Vtable mapping for data source: {data_source}")

    support_root = Path(config.get("wps_support_dir") or REPO_SUPPORT_ROOT)
    geogrid_default = wps_root / "geogrid" / "GEOGRID.TBL.ARW"
    metgrid_default = wps_root / "metgrid" / "METGRID.TBL.ARW"
    vtable_default = wps_root / "ungrib" / "Variable_Tables" / vtable_name
    geogrid_override = overrides.get("geogrid")
    metgrid_override = overrides.get("metgrid")
    vtable_override = _source_vtable_override(overrides, source, vtable_name)

    return {
        "GEOGRID.TBL": Path(geogrid_override) if geogrid_override else _first_existing_path(
            geogrid_default,
            support_root / "GEOGRID.TBL.ARW",
        ),
        "METGRID.TBL": Path(metgrid_override) if metgrid_override else _first_existing_path(
            metgrid_default,
            support_root / "METGRID.TBL.ARW",
        ),
        "Vtable": Path(vtable_override) if vtable_override else _first_existing_path(
            vtable_default,
            support_root / vtable_name,
        ),
    }


def resolve_wps_executable(config: dict[str, Any], wps_root: Path, name: str) -> Path:
    candidate_dirs: list[Path] = [wps_root, wps_root / "bin"]
    explicit_bin = config.get("wps_bin_dir")
    if explicit_bin:
        candidate_dirs.append(Path(explicit_bin))

    seen: set[str] = set()
    for directory in candidate_dirs:
        directory_key = posix_path(directory)
        if directory_key in seen:
            continue
        seen.add(directory_key)
        for candidate in (directory / name, directory / f"{name}.exe"):
            if candidate.is_file():
                return candidate

    base_dir = candidate_dirs[0] if candidate_dirs else wps_root
    return base_dir / name


def build_commands(
    config: dict[str, Any],
    wps_root: Path,
    forcing_files: list[Path],
) -> dict[str, list[str]]:
    return {
        "geogrid": [str(resolve_wps_executable(config, wps_root, "geogrid"))],
        "link_grib": [
            str(resolve_wps_executable(config, wps_root, "link_grib.csh")),
            *[str(path.resolve()) for path in forcing_files],
        ],
        "ungrib": [str(resolve_wps_executable(config, wps_root, "ungrib"))],
        "metgrid": [str(resolve_wps_executable(config, wps_root, "metgrid"))],
    }


def build_step_logs(log_dir: Path) -> dict[str, Path]:
    return {
        "geogrid": log_dir / "wrf-wps-geogrid.log",
        "link_grib": log_dir / "wrf-wps-link-grib.log",
        "ungrib": log_dir / "wrf-wps-ungrib.log",
        "metgrid": log_dir / "wrf-wps-metgrid.log",
    }


def build_plan(
    project_root: Path,
    wps_root: Path,
    work_dir: Path,
    forcing_files: list[Path],
    support_sources: dict[str, Path],
    support_inventory: dict[str, Any],
    expected_outputs: list[Path],
    output_inventory: dict[str, Any],
    commands: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "project_root": posix_path(project_root),
        "wps_root": posix_path(wps_root),
        "work_dir": posix_path(work_dir),
        "forcing_files": [posix_path(path) for path in forcing_files],
        "forcing_count": len(forcing_files),
        "support_files": {
            name: posix_path(source)
            for name, source in support_sources.items()
        },
        "missing_support_files": support_inventory["missing_files"],
        "expected_met_em_files": [posix_path(path) for path in expected_outputs],
        "existing_met_em_files": output_inventory["existing_files"],
        "missing_met_em_files": output_inventory["missing_files"],
        "commands": commands,
    }


def write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def write_step_log(
    log_path: Path,
    command: list[str],
    cwd: Path,
    returncode: int,
    output: str,
) -> None:
    lines = [
        f"cwd={posix_path(cwd)}",
        f"command={json.dumps(command, ensure_ascii=True)}",
        f"returncode={returncode}",
        "[output]",
        output if output else "(no output)",
    ]
    write_log(log_path, lines)


def combine_output(completed: subprocess.CompletedProcess[str]) -> str:
    parts = [part for part in (completed.stdout.strip(), completed.stderr.strip()) if part]
    return "\n".join(parts)


def support_target_path(work_dir: Path, name: str) -> Path:
    relative = SUPPORT_TARGETS.get(name, Path(name))
    return work_dir / relative


def wps_output_has_error(output: str) -> bool:
    normalized = output.lower()
    return "error:" in normalized or "fatal" in normalized


def stage_support_files(
    support_sources: dict[str, Path],
    work_dir: Path,
) -> dict[str, str]:
    staged: dict[str, str] = {}
    for name, source in support_sources.items():
        target = support_target_path(work_dir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        staged[name] = posix_path(target)
    return staged


def build_support_inventory(support_sources: dict[str, Path]) -> dict[str, Any]:
    existing_files: list[str] = []
    missing_files: list[str] = []
    for source in support_sources.values():
        if source.exists():
            existing_files.append(posix_path(source))
        else:
            missing_files.append(posix_path(source))
    return {
        "existing_files": existing_files,
        "missing_files": missing_files,
        "complete": len(missing_files) == 0,
    }


def update_project_for_wps(
    state: dict[str, Any],
    output_inventory: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    state["execution"]["dry_run"] = dry_run
    clear_error(state)
    state["artifacts"]["met_em_files"] = []
    for path in output_inventory["existing_files"]:
        register_artifact(state, "met_em_files", path)

    if output_inventory["complete"]:
        transition(state, "wps_ready", current_step="wrf-wps", allow_retry=True)
    else:
        state["status"] = "data_ready"
        state["current_step"] = "wrf-wps"
    return state


def _failure(
    base_state: dict[str, Any],
    project_json_path: Path,
    main_log_path: Path,
    *,
    code: str,
    message: str,
    log_path: Path,
    main_log_lines: list[str],
) -> None:
    state = deepcopy(base_state)
    record_error(
        state,
        "wrf-wps",
        code,
        message,
        posix_path(log_path),
    )
    save_project(state, project_json_path)
    write_log(main_log_path, main_log_lines + [f"error_code={code}", f"error_message={message}"])
    raise RuntimeError(message)


def prepare_wps(
    project_name: str,
    *,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
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
    spec = load_json(spec_path)
    config = load_json(config_path)

    work_dir = Path(base_state["paths"]["wps_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(base_state["paths"]["log_dir"])
    main_log_path = log_dir / "wrf-wps.log"
    step_logs = build_step_logs(log_dir)
    namelist_path = Path(base_state["artifacts"].get("namelist_wps") or (work_dir / "namelist.wps"))
    if not namelist_path.exists():
        raise FileNotFoundError(f"Missing namelist.wps: {namelist_path}")

    namelist = read_namelist(namelist_path)
    interval_hours = determine_interval_hours(base_state, namelist)
    forcing_files = collect_forcing_files(base_state)
    wps_root = Path(config["wps_dir"])
    support_sources = resolve_support_sources(config, wps_root, normalize_spec(spec)["data_source"])
    support_inventory = build_support_inventory(support_sources)
    expected_outputs = expected_met_em_paths(
        spec,
        namelist,
        work_dir,
        interval_hours=interval_hours,
    )
    output_inventory = build_output_inventory(expected_outputs)
    commands = build_commands(config, wps_root, forcing_files)
    plan = build_plan(
        project_root,
        wps_root,
        work_dir,
        forcing_files,
        support_sources,
        support_inventory,
        expected_outputs,
        output_inventory,
        commands,
    )

    preview_state = deepcopy(base_state)
    update_project_for_wps(preview_state, output_inventory, dry_run=dry_run)

    if dry_run:
        return {
            "dry_run": True,
            "project": preview_state,
            "plan": plan,
        }

    main_log_lines = [
        f"wrf-wps project={project_name}",
        f"work_dir={posix_path(work_dir)}",
        f"wps_root={posix_path(wps_root)}",
        f"namelist={posix_path(namelist_path)}",
        f"forcing_count={len(forcing_files)}",
        f"expected_met_em_count={len(expected_outputs)}",
        f"existing_met_em_count={output_inventory['existing_count']}",
        f"missing_met_em_count={output_inventory['missing_count']}",
    ]

    if output_inventory["complete"]:
        state = deepcopy(base_state)
        update_project_for_wps(state, output_inventory, dry_run=False)
        save_project(state, project_json_path)
        write_log(
            main_log_path,
            main_log_lines + ["reused_existing_outputs=true"],
        )
        return {
            "dry_run": False,
            "project": state,
            "log_path": posix_path(main_log_path),
            "plan": plan,
        }

    if not forcing_files:
        _failure(
            base_state,
            project_json_path,
            main_log_path,
            code="FORCING_MISSING",
            message="No forcing files are available for wrf-wps",
            log_path=main_log_path,
            main_log_lines=main_log_lines,
        )

    missing_commands = [
        command[0]
        for command in commands.values()
        if not Path(command[0]).exists()
    ]
    if missing_commands:
        _failure(
            base_state,
            project_json_path,
            main_log_path,
            code="WPS_BINARY_MISSING",
            message=f"Missing WPS executables: {', '.join(missing_commands)}",
            log_path=main_log_path,
            main_log_lines=main_log_lines,
        )

    if not support_inventory["complete"]:
        _failure(
            base_state,
            project_json_path,
            main_log_path,
            code="WPS_SUPPORT_MISSING",
            message=f"Missing WPS support files: {', '.join(support_inventory['missing_files'])}",
            log_path=main_log_path,
            main_log_lines=main_log_lines,
        )

    staged_support = stage_support_files(support_sources, work_dir)
    main_log_lines.extend(
        [
            f"staged_{name}={target}"
            for name, target in staged_support.items()
        ]
    )

    for step_name, command in commands.items():
        completed = subprocess.run(
            command,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        output = combine_output(completed)
        write_step_log(step_logs[step_name], command, work_dir, completed.returncode, output)
        if completed.returncode != 0 or wps_output_has_error(output):
            if completed.returncode != 0:
                message = f"{step_name} failed with exit code {completed.returncode}"
            else:
                message = f"{step_name} reported an error; see {posix_path(step_logs[step_name])}"
            _failure(
                base_state,
                project_json_path,
                main_log_path,
                code=STEP_CODE_MAP[step_name],
                message=message,
                log_path=step_logs[step_name],
                main_log_lines=main_log_lines,
            )

    output_inventory = build_output_inventory(expected_outputs)
    state = deepcopy(base_state)
    update_project_for_wps(state, output_inventory, dry_run=False)
    save_project(state, project_json_path)

    main_log_lines.extend(
        [
            "reused_existing_outputs=false",
            f"final_existing_met_em_count={output_inventory['existing_count']}",
            f"final_missing_met_em_count={output_inventory['missing_count']}",
        ]
    )
    write_log(main_log_path, main_log_lines)

    if not output_inventory["complete"]:
        record_error(
            state,
            "wrf-wps",
            "METGRID_INCOMPLETE",
            f"{output_inventory['missing_count']} met_em files are still missing after metgrid",
            posix_path(step_logs["metgrid"]),
        )
        save_project(state, project_json_path)
        raise RuntimeError("WPS preprocessing incomplete; see wrf-wps logs")

    return {
        "dry_run": False,
        "project": state,
        "log_path": posix_path(main_log_path),
        "step_logs": {name: posix_path(path) for name, path in step_logs.items()},
        "plan": plan,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WPS preprocessing for a WRF project")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--config", default="config/wrf_env.json")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = prepare_wps(
        args.project_name,
        runs_dir=args.runs_dir,
        config_path=args.config,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
