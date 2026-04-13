from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from netCDF4 import Dataset
except ImportError:  # pragma: no cover
    Dataset = None

try:
    from local_runtime import (
        LocalRuntimeConfigError,
        SAFE_LOCAL_MODE,
        build_process_env,
        local_runtime_config,
        render_command_template,
        trusted_exec_roots,
        validate_local_runtime_sections,
        validate_rendered_command,
    )
    from namelist_parser import read_namelist, write_namelist
    from project_state import (
        clear_error,
        load_project,
        posix_path,
        record_error,
        register_artifact,
        save_project,
        transition,
    )
except ImportError:  # pragma: no cover
    from .local_runtime import (
        LocalRuntimeConfigError,
        SAFE_LOCAL_MODE,
        build_process_env,
        local_runtime_config,
        render_command_template,
        trusted_exec_roots,
        validate_local_runtime_sections,
        validate_rendered_command,
    )
    from .namelist_parser import read_namelist, write_namelist
    from .project_state import (
        clear_error,
        load_project,
        posix_path,
        record_error,
        register_artifact,
        save_project,
        transition,
    )

EXCLUDED_STAGE_NAMES = {"namelist.input"}
OUTPUT_PREFIXES = ("met_em.", "wrfinput_d", "wrfbdy_d", "wrfout_d", "rsl.")
STEP_CODE_MAP = {
    "real": "REAL_FAILED",
    "wrf": "WRF_FAILED",
}


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)



def _scalar(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, list):
        return value[0] if value else default
    return value



def detect_domain_count(namelist: dict[str, dict[str, Any]]) -> int:
    count = _scalar(namelist.get("domains", {}).get("max_dom"), 1)
    return max(1, int(count))



def build_inventory(paths: list[Path]) -> dict[str, Any]:
    existing_files: list[str] = []
    missing_files: list[str] = []
    for path in paths:
        if path.exists() and path.is_file():
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



def collect_met_em_files(state: dict[str, Any]) -> list[Path]:
    met_em_files: list[Path] = []
    for raw_path in state["artifacts"].get("met_em_files", []):
        path = Path(raw_path)
        if path.exists() and path not in met_em_files:
            met_em_files.append(path)

    wps_dir = Path(state["paths"]["wps_dir"])
    for path in sorted(wps_dir.glob("met_em.d*.nc")):
        if path.is_file() and path not in met_em_files:
            met_em_files.append(path)

    return met_em_files


def _read_met_em_vertical_metadata(met_em_path: Path) -> dict[str, int] | None:
    if Dataset is None:
        return None

    try:
        with Dataset(met_em_path) as dataset:
            num_metgrid_levels = getattr(dataset, "NUM_METGRID_LEVELS", None)
            if num_metgrid_levels is None:
                num_metgrid_levels = getattr(dataset, "BOTTOM-TOP_GRID_DIMENSION", None)
            num_metgrid_soil_levels = getattr(dataset, "NUM_METGRID_SOIL_LEVELS", None)
    except (OSError, RuntimeError, ValueError):
        return None

    payload: dict[str, int] = {}
    if num_metgrid_levels is not None:
        payload["num_metgrid_levels"] = int(num_metgrid_levels)
    if num_metgrid_soil_levels is not None:
        payload["num_metgrid_soil_levels"] = int(num_metgrid_soil_levels)
    return payload or None


def sync_namelist_with_met_em(
    namelist_path: Path,
    namelist: dict[str, dict[str, Any]],
    met_em_files: list[Path],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if not met_em_files:
        return namelist, {}

    metadata = _read_met_em_vertical_metadata(met_em_files[0])
    if not metadata:
        return namelist, {}

    updated = deepcopy(namelist)
    domains = updated.setdefault("domains", {})
    adjustments: dict[str, dict[str, Any]] = {}
    source_path = posix_path(met_em_files[0])

    for key, expected in metadata.items():
        current = _scalar(domains.get(key))
        current_value = None if current is None else int(current)
        if current_value == expected:
            continue
        domains[key] = expected
        adjustments[key] = {"old": current, "new": expected, "source": source_path}

    if adjustments:
        write_namelist(updated, namelist_path)

    return updated, adjustments



def expected_wrfinput_paths(work_dir: Path, domain_count: int) -> list[Path]:
    return [work_dir / f"wrfinput_d{index:02d}" for index in range(1, domain_count + 1)]



def collect_wrfout_files(work_dir: Path, output_dir: Path) -> list[Path]:
    wrfout_files: list[Path] = []
    for candidate_dir in (work_dir, output_dir):
        for path in sorted(candidate_dir.glob("wrfout_d*")):
            if path.is_file() and path not in wrfout_files:
                wrfout_files.append(path)
    return wrfout_files



def discover_source_run_dir(config: dict[str, Any]) -> Path:
    candidates = [Path(config["wrf_dir"]) / "run"]
    explicit = config.get("wrf_run_dir")
    if explicit:
        candidates.append(Path(explicit))

    seen: set[str] = set()
    for candidate in candidates:
        key = posix_path(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    return candidates[0]



def collect_support_files(source_run_dir: Path) -> list[Path]:
    if not source_run_dir.exists():
        return []

    support_files: list[Path] = []
    for path in sorted(source_run_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name in EXCLUDED_STAGE_NAMES:
            continue
        if any(path.name.startswith(prefix) for prefix in OUTPUT_PREFIXES):
            continue
        support_files.append(path)
    return support_files



def stage_files(files: list[Path], target_dir: Path) -> list[str]:
    staged: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in files:
        target = target_dir / source.name
        source_stat = source.stat()
        if target.exists() and target.is_file():
            target_stat = target.stat()
            if (
                source_stat.st_size == target_stat.st_size
                and source_stat.st_mtime_ns == target_stat.st_mtime_ns
            ):
                staged.append(posix_path(target))
                continue
        shutil.copy2(source, target)
        staged.append(posix_path(target))
    return staged


def clear_stale_met_em_files(work_dir: Path, active_files: list[Path]) -> list[str]:
    active_names = {path.name for path in active_files}
    removed: list[str] = []
    for path in sorted(work_dir.glob("met_em.d*.nc")):
        if path.name in active_names:
            continue
        path.unlink()
        removed.append(posix_path(path))
    return removed



def build_runtime_env(
    command: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
    prepend_path: list[str] | None = None,
) -> dict[str, str] | None:
    env = build_process_env(
        command,
        env_overrides=env_overrides,
        prepend_path=prepend_path,
    )
    return env if env != os.environ.copy() else None


def build_commands(work_dir: Path, config: dict[str, Any]) -> tuple[dict[str, list[str]], int]:
    local_config = config.get("local", {})
    np = max(1, int(local_config.get("default_np") or 1))
    mpi_cmd = str(local_config.get("mpi_cmd") or "").strip()
    executable_dir = work_dir.resolve()
    real_command = [str(executable_dir / "real.exe")]
    if np <= 1 or not mpi_cmd:
        wrf_command = [str(executable_dir / "wrf.exe")]
    else:
        wrf_command = [mpi_cmd, "-np", str(np), str(executable_dir / "wrf.exe")]
    return {"real": real_command, "wrf": wrf_command}, np



def write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")



def combine_output(completed: subprocess.CompletedProcess[str]) -> str:
    parts = [part for part in (completed.stdout.strip(), completed.stderr.strip()) if part]
    return "\n".join(parts)



def write_step_log(log_path: Path, command: list[str], cwd: Path, returncode: int, output: str) -> None:
    write_log(
        log_path,
        [
            f"cwd={posix_path(cwd)}",
            f"command={json.dumps(command, ensure_ascii=True)}",
            f"returncode={returncode}",
            "[output]",
            output if output else "(no output)",
        ],
    )



def register_paths(state: dict[str, Any], kind: str, paths: list[str]) -> None:
    state["artifacts"][kind] = []
    for path in paths:
        register_artifact(state, kind, path)



def transition_sequence(
    state: dict[str, Any],
    statuses: list[str],
    *,
    current_step: str,
    allow_retry: bool = False,
) -> None:
    retry = allow_retry
    for status in statuses:
        transition(state, status, current_step=current_step, allow_retry=retry)
        retry = False



def complete_from_existing_outputs(
    state: dict[str, Any],
    *,
    wrfinput_inventory: dict[str, Any],
    wrfout_files: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    state["execution"]["dry_run"] = dry_run
    clear_error(state)
    register_paths(state, "wrfinput_files", wrfinput_inventory["existing_files"])
    register_paths(state, "wrfout_files", wrfout_files)

    current_status = state["status"]
    if current_status == "wps_ready":
        transition_sequence(state, ["real_ready", "running", "completed"], current_step="wrf-run")
    elif current_status == "real_ready":
        transition_sequence(state, ["running", "completed"], current_step="wrf-run")
    elif current_status == "running":
        transition_sequence(state, ["completed"], current_step="wrf-run")
    elif current_status == "failed":
        transition_sequence(
            state,
            ["real_ready", "running", "completed"],
            current_step="wrf-run",
            allow_retry=True,
        )
    else:
        state["current_step"] = "wrf-run"

    return state



def real_ready_from_outputs(
    state: dict[str, Any],
    *,
    wrfinput_inventory: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    state["execution"]["dry_run"] = dry_run
    clear_error(state)
    register_paths(state, "wrfinput_files", wrfinput_inventory["existing_files"])

    if state["status"] == "wps_ready":
        transition_sequence(state, ["real_ready"], current_step="wrf-run")
    elif state["status"] == "failed":
        transition_sequence(state, ["real_ready"], current_step="wrf-run", allow_retry=True)
    else:
        state["current_step"] = "wrf-run"

    return state



def build_plan(
    project_root: Path,
    source_run_dir: Path,
    work_dir: Path,
    output_dir: Path,
    met_em_inventory: dict[str, Any],
    wrfinput_inventory: dict[str, Any],
    wrfout_files: list[Path],
    support_files: list[Path],
    commands: dict[str, list[str]],
    *,
    runtime_mode: str,
    np: int,
    boundary_path: Path,
) -> dict[str, Any]:
    return {
        "project_root": posix_path(project_root),
        "source_run_dir": posix_path(source_run_dir),
        "work_dir": posix_path(work_dir),
        "output_dir": posix_path(output_dir),
        "met_em_files": met_em_inventory["existing_files"],
        "missing_met_em_files": met_em_inventory["missing_files"],
        "expected_wrfinput_files": wrfinput_inventory["existing_files"] + wrfinput_inventory["missing_files"],
        "expected_wrfbdy_file": posix_path(boundary_path),
        "existing_wrfinput_files": wrfinput_inventory["existing_files"],
        "existing_wrfout_files": [posix_path(path) for path in wrfout_files],
        "support_files": [posix_path(path) for path in support_files],
        "runtime_mode": runtime_mode,
        "np": np,
        "commands": commands,
    }



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
    record_error(state, "wrf-run", code, message, posix_path(log_path))
    save_project(state, project_json_path)
    write_log(main_log_path, main_log_lines + [f"error_code={code}", f"error_message={message}"])
    raise RuntimeError(message)



def resolve_runtime_commands(
    config: dict[str, Any],
    *,
    project_root: Path,
    work_dir: Path,
    output_dir: Path,
    source_run_dir: Path,
    need_real: bool,
    require_internal_execs: bool,
) -> tuple[str, dict[str, list[str]], int, dict[str, str], list[str]]:
    validate_local_runtime_sections(config)
    runtime = local_runtime_config(config)
    if runtime["mode"] != SAFE_LOCAL_MODE:
        commands, np = build_commands(work_dir, config)
        return runtime["mode"], commands, np, {}, []

    if need_real and runtime.get("real_cmd") is None:
        raise LocalRuntimeConfigError("local.runtime.real_cmd is required when real.exe must run")

    np = max(1, int(config.get("local", {}).get("default_np") or 1))
    real_exe = work_dir.resolve() / "real.exe"
    wrf_exe = work_dir.resolve() / "wrf.exe"
    runtime_roots = trusted_exec_roots(config, runtime, project_root=project_root)
    context = {
        "project_name": project_root.name,
        "work_dir": posix_path(work_dir),
        "output_dir": posix_path(output_dir),
        "source_run_dir": posix_path(source_run_dir),
        "real_exe": posix_path(real_exe),
        "wrf_exe": posix_path(wrf_exe),
        "np": np,
    }
    allowed_placeholders = {
        "project_name",
        "work_dir",
        "output_dir",
        "source_run_dir",
        "real_exe",
        "wrf_exe",
        "np",
    }
    command_templates: dict[str, list[str]] = {"wrf": runtime["wrf_cmd"]}
    if need_real:
        command_templates = {"real": runtime["real_cmd"], "wrf": runtime["wrf_cmd"]}

    internal_execs = [real_exe, wrf_exe]
    commands: dict[str, list[str]] = {}
    for step_name, template in command_templates.items():
        command = render_command_template(
            template,
            context=context,
            allowed_placeholders=allowed_placeholders,
        )
        validate_rendered_command(
            command,
            cwd=work_dir,
            trusted_roots=runtime_roots,
            internal_execs=internal_execs,
            require_internal_execs=require_internal_execs,
            prepend_path=runtime.get("prepend_path") or [],
        )
        commands[step_name] = command

    return runtime["mode"], commands, np, runtime.get("env") or {}, runtime.get("prepend_path") or []



def run_project(
    project_name: str,
    *,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
    dry_run: bool = False,
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    project_root = runs_dir / project_name
    project_json_path = project_root / "project.json"

    if not project_json_path.exists():
        raise FileNotFoundError(f"Missing project.json: {project_json_path}")

    base_state = load_project(project_json_path)
    if str(base_state["execution"].get("mode") or "local").lower() != "local":
        raise NotImplementedError("wrf-run currently supports local mode only")

    config = load_json(config_path)
    runtime_mode_hint = local_runtime_config(config)["mode"]
    work_dir = Path(base_state["paths"]["wrf_dir"])
    output_dir = Path(base_state["paths"]["output_dir"])
    log_dir = Path(base_state["paths"]["log_dir"])
    main_log_path = log_dir / "wrf-run.log"
    real_log_path = log_dir / "wrf-run-real.log"
    wrf_log_path = log_dir / "wrf-run-wrf.log"
    namelist_path = Path(base_state["artifacts"].get("namelist_input") or (work_dir / "namelist.input"))
    if not namelist_path.exists():
        raise FileNotFoundError(f"Missing namelist.input: {namelist_path}")

    namelist = read_namelist(namelist_path)
    met_em_files = collect_met_em_files(base_state)
    namelist, namelist_sync = sync_namelist_with_met_em(namelist_path, namelist, met_em_files)
    domain_count = detect_domain_count(namelist)
    met_em_inventory = build_inventory(met_em_files)
    expected_inputs = expected_wrfinput_paths(work_dir, domain_count)
    boundary_path = work_dir / "wrfbdy_d01"
    wrfinput_inventory = build_inventory(expected_inputs)
    boundary_exists = boundary_path.exists() and boundary_path.is_file()
    wrfout_files = collect_wrfout_files(work_dir, output_dir)
    source_run_dir = discover_source_run_dir(config)
    support_files = collect_support_files(source_run_dir)
    need_real = not (wrfinput_inventory["complete"] and boundary_exists)

    main_log_lines = [
        f"wrf-run project={project_name}",
        f"work_dir={posix_path(work_dir)}",
        f"source_run_dir={posix_path(source_run_dir)}",
        f"namelist={posix_path(namelist_path)}",
        f"met_em_count={met_em_inventory['existing_count']}",
        f"existing_wrfinput_count={wrfinput_inventory['existing_count']}",
        f"boundary_exists={boundary_exists}",
        f"existing_wrfout_count={len(wrfout_files)}",
        f"runtime_mode={runtime_mode_hint}",
    ]
    for key, payload in namelist_sync.items():
        main_log_lines.append(f"sync_{key}={payload['old']}->{payload['new']} ({payload['source']})")

    commands: dict[str, list[str]] = {}
    np = max(1, int(config.get("local", {}).get("default_np") or 1))
    env_overrides: dict[str, str] = {}
    prepend_path: list[str] = []
    should_resolve_runtime = dry_run or not wrfout_files
    if should_resolve_runtime:
        try:
            runtime_mode, commands, np, env_overrides, prepend_path = resolve_runtime_commands(
                config,
                project_root=project_root,
                work_dir=work_dir,
                output_dir=output_dir,
                source_run_dir=source_run_dir,
                need_real=need_real,
                require_internal_execs=False,
            )
        except LocalRuntimeConfigError as exc:
            if dry_run:
                raise
            _failure(
                base_state,
                project_json_path,
                main_log_path,
                code="LOCAL_RUNTIME_INVALID",
                message=str(exc),
                log_path=main_log_path,
                main_log_lines=main_log_lines,
            )
    else:
        runtime_mode = runtime_mode_hint

    plan = build_plan(
        project_root,
        source_run_dir,
        work_dir,
        output_dir,
        met_em_inventory,
        wrfinput_inventory,
        wrfout_files,
        support_files,
        commands,
        runtime_mode=runtime_mode,
        np=np,
        boundary_path=boundary_path,
    )

    preview_state = deepcopy(base_state)
    preview_state["execution"]["dry_run"] = dry_run
    preview_state["current_step"] = "wrf-run"
    if wrfout_files:
        complete_from_existing_outputs(
            preview_state,
            wrfinput_inventory=wrfinput_inventory,
            wrfout_files=[posix_path(path) for path in wrfout_files],
            dry_run=dry_run,
        )
    elif wrfinput_inventory["complete"] and boundary_exists:
        real_ready_from_outputs(
            preview_state,
            wrfinput_inventory=wrfinput_inventory,
            dry_run=dry_run,
        )

    if dry_run:
        return {
            "dry_run": True,
            "project": preview_state,
            "plan": plan,
        }

    main_log_lines.append(f"np={np}")

    if wrfout_files:
        state = deepcopy(base_state)
        complete_from_existing_outputs(
            state,
            wrfinput_inventory=wrfinput_inventory,
            wrfout_files=[posix_path(path) for path in wrfout_files],
            dry_run=False,
        )
        save_project(state, project_json_path)
        write_log(main_log_path, main_log_lines + ["reused_existing_outputs=true"])
        return {
            "dry_run": False,
            "project": state,
            "log_path": posix_path(main_log_path),
            "plan": plan,
        }

    if not met_em_files:
        _failure(
            base_state,
            project_json_path,
            main_log_path,
            code="MET_EM_MISSING",
            message="No met_em files are available for wrf-run",
            log_path=main_log_path,
            main_log_lines=main_log_lines,
        )

    if not source_run_dir.exists():
        _failure(
            base_state,
            project_json_path,
            main_log_path,
            code="WRF_SOURCE_MISSING",
            message=f"Missing WRF run directory: {source_run_dir}",
            log_path=main_log_path,
            main_log_lines=main_log_lines,
        )

    support_inventory = build_inventory(support_files)
    main_log_lines.append(f"support_file_count={support_inventory['existing_count']}")

    clear_stale_met_em_files(work_dir, met_em_files)
    stage_files(met_em_files, work_dir)
    stage_files(support_files, work_dir)

    if runtime_mode == SAFE_LOCAL_MODE:
        try:
            _, commands, np, env_overrides, prepend_path = resolve_runtime_commands(
                config,
                project_root=project_root,
                work_dir=work_dir,
                output_dir=output_dir,
                source_run_dir=source_run_dir,
                need_real=need_real,
                require_internal_execs=True,
            )
        except LocalRuntimeConfigError as exc:
            _failure(
                base_state,
                project_json_path,
                main_log_path,
                code="LOCAL_RUNTIME_INVALID",
                message=str(exc),
                log_path=main_log_path,
                main_log_lines=main_log_lines,
            )
    else:
        missing_binaries = []
        if need_real and not (work_dir / "real.exe").exists():
            missing_binaries.append(posix_path(work_dir / "real.exe"))
        if not (work_dir / "wrf.exe").exists():
            missing_binaries.append(posix_path(work_dir / "wrf.exe"))
        if missing_binaries:
            _failure(
                base_state,
                project_json_path,
                main_log_path,
                code="WRF_BINARY_MISSING",
                message=f"Missing WRF executables: {', '.join(missing_binaries)}",
                log_path=main_log_path,
                main_log_lines=main_log_lines,
            )

    state = deepcopy(base_state)
    if need_real:
        completed = subprocess.run(
            commands["real"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
            env=build_runtime_env(
                commands["real"],
                env_overrides=env_overrides,
                prepend_path=prepend_path,
            ),
        )
        output = combine_output(completed)
        write_step_log(real_log_path, commands["real"], work_dir, completed.returncode, output)
        if completed.returncode != 0:
            _failure(
                base_state,
                project_json_path,
                main_log_path,
                code=STEP_CODE_MAP["real"],
                message=f"real.exe failed with exit code {completed.returncode}",
                log_path=real_log_path,
                main_log_lines=main_log_lines,
            )

    wrfinput_inventory = build_inventory(expected_inputs)
    boundary_exists = boundary_path.exists() and boundary_path.is_file()
    if not wrfinput_inventory["complete"] or not boundary_exists:
        _failure(
            base_state,
            project_json_path,
            main_log_path,
            code="REAL_INCOMPLETE",
            message="real.exe did not produce all required wrfinput/wrfbdy files",
            log_path=real_log_path,
            main_log_lines=main_log_lines,
        )

    real_ready_from_outputs(state, wrfinput_inventory=wrfinput_inventory, dry_run=False)
    save_project(state, project_json_path)

    transition_sequence(state, ["running"], current_step="wrf-run")
    save_project(state, project_json_path)

    completed = subprocess.run(
        commands["wrf"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
        env=build_runtime_env(
            commands["wrf"],
            env_overrides=env_overrides,
            prepend_path=prepend_path,
        ),
    )
    output = combine_output(completed)
    write_step_log(wrf_log_path, commands["wrf"], work_dir, completed.returncode, output)
    if completed.returncode != 0:
        _failure(
            state,
            project_json_path,
            main_log_path,
            code=STEP_CODE_MAP["wrf"],
            message=f"wrf.exe failed with exit code {completed.returncode}",
            log_path=wrf_log_path,
            main_log_lines=main_log_lines,
        )

    wrfout_files = collect_wrfout_files(work_dir, output_dir)
    if not wrfout_files:
        _failure(
            state,
            project_json_path,
            main_log_path,
            code="WRFOUT_MISSING",
            message="wrf.exe completed but no wrfout files were found",
            log_path=wrf_log_path,
            main_log_lines=main_log_lines,
        )

    complete_from_existing_outputs(
        state,
        wrfinput_inventory=wrfinput_inventory,
        wrfout_files=[posix_path(path) for path in wrfout_files],
        dry_run=False,
    )
    save_project(state, project_json_path)
    write_log(
        main_log_path,
        main_log_lines
        + [
            "reused_existing_outputs=false",
            f"final_wrfinput_count={wrfinput_inventory['existing_count']}",
            f"final_wrfout_count={len(wrfout_files)}",
        ],
    )

    return {
        "dry_run": False,
        "project": state,
        "log_path": posix_path(main_log_path),
        "step_logs": {
            "real": posix_path(real_log_path),
            "wrf": posix_path(wrf_log_path),
        },
        "plan": plan,
    }

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local WRF execution for a prepared project")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--config", default="config/wrf_env.json")
    parser.add_argument("--dry-run", action="store_true")
    return parser



def main() -> int:
    args = build_parser().parse_args()
    payload = run_project(
        args.project_name,
        runs_dir=args.runs_dir,
        config_path=args.config,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
