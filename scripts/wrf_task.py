from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from constants import (
        HPC_DEFAULT_POLL_INTERVAL_SECONDS,
        HPC_QUEUED_POLL_INTERVAL_SECONDS,
        HPC_RUNNING_POLL_INTERVAL_SECONDS,
        LOCAL_POLL_INTERVAL_SECONDS,
        TAIL_READ_BLOCK_SIZE,
        TASK_STEPS,
        TERMINAL_TASK_STATES,
    )
    from hpc import get_scheduler_adapter
    from hpc.admission import evaluate_admission
    from hpc.base import build_remote_command_argv, resolve_access_mode, resolve_transfer_host
    from namelist_parser import read_namelist
    from project_state import (
        assert_mutation_allowed,
        clear_error,
        finish_substep,
        load_project,
        mark_substeps_stale,
        record_admission,
        record_error,
        record_task_terminal,
        register_artifact,
        save_project,
        set_active_task,
        transition,
    )
    from spec_utils import normalize_spec, parse_time
    from utils import posix_path, utc_now
    from wrf_data import prepare_data
    from wrf_run import (
        build_inventory,
        collect_wrfout_files,
        collect_run_step_output_paths,
        complete_from_existing_outputs,
        detect_domain_count,
        expected_wrfinput_paths,
        invalidate_run_outputs,
        real_ready_from_outputs,
        resolve_selected_run_substeps,
        run_project,
        RUN_SUBSTEPS,
    )
    from wrf_wps import (
        build_output_inventory as build_wps_output_inventory,
        build_support_inventory,
        collect_step_output_paths,
        collect_forcing_files,
        determine_interval_hours,
        expected_met_em_paths,
        invalidate_wps_outputs,
        prepare_wps,
        resolve_selected_substeps,
        resolve_support_sources,
        stage_support_files,
        update_project_for_wps,
        validate_existing_wps_prerequisites,
        WPS_SUBSTEPS,
    )
except ImportError:  # pragma: no cover
    from .constants import (
        HPC_DEFAULT_POLL_INTERVAL_SECONDS,
        HPC_QUEUED_POLL_INTERVAL_SECONDS,
        HPC_RUNNING_POLL_INTERVAL_SECONDS,
        LOCAL_POLL_INTERVAL_SECONDS,
        TAIL_READ_BLOCK_SIZE,
        TASK_STEPS,
        TERMINAL_TASK_STATES,
    )
    from .hpc import get_scheduler_adapter
    from .hpc.admission import evaluate_admission
    from .hpc.base import build_remote_command_argv, resolve_access_mode, resolve_transfer_host
    from .namelist_parser import read_namelist
    from .project_state import (
        assert_mutation_allowed,
        clear_error,
        finish_substep,
        load_project,
        mark_substeps_stale,
        record_admission,
        record_error,
        record_task_terminal,
        register_artifact,
        save_project,
        set_active_task,
        transition,
    )
    from .spec_utils import normalize_spec, parse_time
    from .utils import posix_path, utc_now
    from .wrf_data import prepare_data
    from .wrf_run import (
        build_inventory,
        collect_wrfout_files,
        collect_run_step_output_paths,
        complete_from_existing_outputs,
        detect_domain_count,
        expected_wrfinput_paths,
        invalidate_run_outputs,
        real_ready_from_outputs,
        resolve_selected_run_substeps,
        run_project,
        RUN_SUBSTEPS,
    )
    from .wrf_wps import (
        build_output_inventory as build_wps_output_inventory,
        build_support_inventory,
        collect_step_output_paths,
        collect_forcing_files,
        determine_interval_hours,
        expected_met_em_paths,
        invalidate_wps_outputs,
        prepare_wps,
        resolve_selected_substeps,
        resolve_support_sources,
        stage_support_files,
        update_project_for_wps,
        validate_existing_wps_prerequisites,
        WPS_SUBSTEPS,
    )


class TaskPreflightError(RuntimeError):
    def __init__(self, code: str, message: str, log_path: Path) -> None:
        super().__init__(message)
        self.code = code
        self.log_path = log_path


LIGHTWEIGHT_OUTPUT_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".pdf",
    ".webp",
}
SUPPORTED_SUBSTEP_NAMES = {
    "geogrid",
    "link_grib",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
}
WORKFLOW_SUBSTEP_NAMES = {
    "wrf-wps": {"geogrid", "link_grib", "ungrib", "metgrid"},
    "wrf-run": {"real", "wrf"},
}
WPS_SUBSTEP_LOG_NAMES = {
    "geogrid": "wrf-wps-geogrid.log",
    "link_grib": "wrf-wps-link-grib.log",
    "ungrib": "wrf-wps-ungrib.log",
    "metgrid": "wrf-wps-metgrid.log",
}
RUN_SUBSTEP_LOG_NAMES = {
    "real": "wrf-run-real.log",
    "wrf": "wrf-run-wrf.log",
}

# ---------------------------------------------------------------------------
# monitor subcommand (streams live progress events for the Monitor tool)
# ---------------------------------------------------------------------------
MONITOR_LOG_APPEAR_TIMEOUT_SECONDS = 120.0
MONITOR_LOCAL_POLL_INTERVAL_SECONDS = 1.0
MONITOR_REFRESH_BACKUP_EVERY_N_LOCAL = 10
MONITOR_REFRESH_BACKUP_EVERY_N_HPC = 30
MONITOR_SSH_RECONNECT_TRIES = 3
MONITOR_SSH_RECONNECT_BACKOFF = (2.0, 4.0, 8.0)
MONITOR_REMOTE_FILE_PROBE_INTERVAL_SECONDS = 2.0

# rsl.out.0000 progress lines come in two forms; match both.
#   "Timing for main: time 2024-07-20_00:10:48 on domain   1: ..." (no d01 prefix)
#   "d01 2010-01-01_06:30:00 ..." (WRF domain print lines)
_RSL_TIMESTAMP_RE = re.compile(
    r"Timing for main:\s*time\s+(\d{4}-\d{2}-\d{2}_\d{2}:\d{2}:\d{2})"
    r"|(?:d\d\d\s+)(\d{4}-\d{2}-\d{2}_\d{2}:\d{2}:\d{2})"
)

_WRF_SUCCESS_SIGNATURES = (
    "SUCCESS COMPLETE WRF",
    "SUCCESS COMPLETE",
    "wrf: SUCCESS COMPLETE",
)
_WRF_FAILURE_SIGNATURES = (
    "Killed",
    "Segmentation",
    "forrtl: severe",
    "BAD RESPONSE",
    "netCDF Error",
    "Fatal",
    "FATAL",
    "ERROR: ",
    "Backtrace:",
    "application called MPI_Abort",
)

# Map a non-substep task step to its main log filename inside remote_log_dir.
_STEP_MAIN_LOG_NAMES = {
    "wrf-wps": "wrf-wps.log",
    "wrf-run": "wrf-run.log",
    "wrf-data": "wrf-data.log",
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_root(runs_dir: Path | str, project_name: str) -> Path:
    return Path(runs_dir) / project_name


def project_json_path(runs_dir: Path | str, project_name: str) -> Path:
    return project_root(runs_dir, project_name) / "project.json"


def task_root(project_dir: Path) -> Path:
    return project_dir / "tasks"


def task_dir(project_dir: Path, task_id: str) -> Path:
    return task_root(project_dir) / task_id


def task_json_path(project_dir: Path, task_id: str) -> Path:
    return task_dir(project_dir, task_id) / "task.json"


def task_stdout_path(project_dir: Path, task_id: str) -> Path:
    return task_dir(project_dir, task_id) / "stdout.log"


def task_stderr_path(project_dir: Path, task_id: str) -> Path:
    return task_dir(project_dir, task_id) / "stderr.log"


def task_exit_code_path(project_dir: Path, task_id: str) -> Path:
    return task_dir(project_dir, task_id) / "exit_code"


def task_result_path(project_dir: Path, task_id: str) -> Path:
    return task_dir(project_dir, task_id) / "result.json"


def build_task_id(step: str) -> str:
    return f"{step}-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "step": task.get("step"),
        "backend": task.get("backend"),
        "state": task.get("state"),
        "pid": task.get("pid"),
        "job_id": task.get("job_id"),
        "log_path": task.get("log_path"),
        "submitted_at": task.get("submitted_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "last_progress": task.get("last_progress"),
        "task_dir": task.get("task_dir"),
    }


def save_task(task: dict[str, Any]) -> None:
    dump_json(task["task_json"], task)


def load_task(project_dir: Path, task_id: str) -> dict[str, Any]:
    return load_json(task_json_path(project_dir, task_id))


def process_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _read_tail_blocks(path: Path, *, min_newlines: int) -> bytes:
    if not path.exists() or not path.is_file():
        return b""

    blocks: list[bytes] = []
    newline_count = 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        offset = handle.tell()
        while offset > 0 and newline_count <= min_newlines:
            read_size = min(TAIL_READ_BLOCK_SIZE, offset)
            offset -= read_size
            handle.seek(offset)
            block = handle.read(read_size)
            blocks.append(block)
            newline_count += block.count(b"\n")
    return b"".join(reversed(blocks))


def read_last_nonempty_line(path: Path) -> str | None:
    data = _read_tail_blocks(path, min_newlines=1)
    if not data:
        return None
    lines = data.decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def read_last_lines_text(path: Path, *, lines: int) -> str:
    if lines <= 0:
        return ""
    data = _read_tail_blocks(path, min_newlines=lines)
    if not data:
        return ""
    lines_list = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join(lines_list[-lines:])


def resolve_substep_log_path(state: dict[str, Any], substep: str) -> Path:
    substeps = state.get("substeps", {})
    for workflow_steps in substeps.values():
        if isinstance(workflow_steps, dict) and substep in workflow_steps:
            raw_path = workflow_steps[substep].get("log_path")
            if raw_path:
                return Path(str(raw_path))

    log_dir = Path(state["paths"]["log_dir"])
    fallback = {
        "geogrid": log_dir / "wrf-wps-geogrid.log",
        "link_grib": log_dir / "wrf-wps-link-grib.log",
        "ungrib": log_dir / "wrf-wps-ungrib.log",
        "metgrid": log_dir / "wrf-wps-metgrid.log",
        "real": log_dir / "wrf-run-real.log",
        "wrf": log_dir / "wrf-run-wrf.log",
    }.get(substep)
    if fallback is None:
        raise ValueError(f"Unsupported substep: {substep}")
    return fallback


def substep_state_path(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".state")


def read_substep_state(log_path: Path) -> str | None:
    state_path = substep_state_path(log_path)
    if not state_path.exists() or not state_path.is_file():
        return None
    value = state_path.read_text(encoding="utf-8").strip().lower()
    return value or None


def workflow_substep_log_paths(state: dict[str, Any], workflow: str) -> dict[str, Path]:
    log_dir = Path(state["paths"]["log_dir"])
    if workflow == "wrf-wps":
        names = WPS_SUBSTEP_LOG_NAMES
    elif workflow == "wrf-run":
        names = RUN_SUBSTEP_LOG_NAMES
    else:
        raise ValueError(f"Unsupported workflow: {workflow}")
    return {name: log_dir / filename for name, filename in names.items()}


def remove_files(paths: list[Path]) -> list[str]:
    removed: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = posix_path(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            path.unlink()
            removed.append(key)
    return removed


def clear_output_tree(output_dir: Path) -> list[str]:
    removed: list[str] = []
    if not output_dir.exists() or not output_dir.is_dir():
        return removed
    for path in sorted(output_dir.iterdir(), key=lambda item: item.as_posix(), reverse=True):
        key = posix_path(path)
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(key)
        elif path.is_file():
            path.unlink()
            removed.append(key)
    return removed


def resolve_hpc_selected_substeps(
    step: str,
    task: dict[str, Any] | None,
    rendered_job: dict[str, Any] | None,
) -> list[str]:
    task_params = task.get("params", {}) if isinstance(task, dict) else {}
    if isinstance(task_params.get("selected_substeps"), list):
        return [str(name) for name in task_params["selected_substeps"]]
    rendered_plan = rendered_job.get("plan", {}) if isinstance(rendered_job, dict) else {}
    if isinstance(rendered_plan.get("selected_substeps"), list):
        return [str(name) for name in rendered_plan["selected_substeps"]]
    if step == "wrf-wps":
        return list(WPS_SUBSTEPS)
    if step == "wrf-run":
        return list(RUN_SUBSTEPS)
    return []


def prepare_hpc_wps_submission(
    project_json: Path,
    *,
    selected_substeps: list[str],
    expected_met_em_files: list[Path],
) -> dict[str, Any]:
    state = load_project(project_json)
    work_dir = Path(state["paths"]["wps_dir"])
    log_paths = workflow_substep_log_paths(state, "wrf-wps")
    main_log_path = Path(state["paths"]["log_dir"]) / "wrf-wps.log"
    clear_error(state)
    mark_substeps_stale(state, "wrf-wps", from_substep=selected_substeps[0])
    invalidate_wps_outputs(
        work_dir,
        selected_substeps[0],
        expected_met_em_files=expected_met_em_files,
    )
    remove_files(
        [main_log_path, *[log_paths[name] for name in selected_substeps], *[substep_state_path(log_paths[name]) for name in selected_substeps]]
    )
    output_inventory = build_wps_output_inventory(expected_met_em_files)
    update_project_for_wps(state, output_inventory, dry_run=False)
    save_project(state, project_json)
    return state


def prepare_hpc_run_submission(
    project_json: Path,
    *,
    selected_substeps: list[str],
    expected_inputs: list[Path],
    boundary_path: Path,
) -> dict[str, Any]:
    state = load_project(project_json)
    work_dir = Path(state["paths"]["wrf_dir"])
    output_dir = Path(state["paths"]["output_dir"])
    log_paths = workflow_substep_log_paths(state, "wrf-run")
    main_log_path = Path(state["paths"]["log_dir"]) / "wrf-run.log"
    clear_error(state)
    mark_substeps_stale(state, "wrf-run", from_substep=selected_substeps[0])
    invalidate_run_outputs(
        work_dir,
        output_dir,
        selected_substeps[0],
        expected_inputs=expected_inputs,
        boundary_path=boundary_path,
    )
    clear_output_tree(output_dir)
    remove_files(
        [main_log_path, *[log_paths[name] for name in selected_substeps], *[substep_state_path(log_paths[name]) for name in selected_substeps]]
    )
    if selected_substeps[0] == "real":
        state["artifacts"]["wrfinput_files"] = []
        state["artifacts"]["wrfout_files"] = []
        state["artifacts"]["plots"] = []
        if state["status"] in {"failed", "real_ready", "running", "completed"}:
            state["status"] = "wps_ready"
            state["current_step"] = "wrf-run"
    else:
        state["artifacts"]["wrfout_files"] = []
        state["artifacts"]["plots"] = []
        if state["status"] in {"failed", "completed"}:
            state["status"] = "real_ready"
            state["current_step"] = "wrf-run"
    save_project(state, project_json)
    return state


def collect_lightweight_output_artifacts(output_dir: Path) -> list[Path]:
    if not output_dir.exists() or not output_dir.is_dir():
        return []

    artifacts: list[Path] = []
    seen: set[str] = set()
    for path in sorted(output_dir.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        if path.name.startswith("wrfout_d"):
            continue
        if path.suffix.lower() not in LIGHTWEIGHT_OUTPUT_EXTENSIONS:
            continue
        key = posix_path(path)
        if key in seen:
            continue
        seen.add(key)
        artifacts.append(path)
    return artifacts


def register_lightweight_output_artifacts(state: dict[str, Any], output_dir: Path) -> list[Path]:
    artifacts = collect_lightweight_output_artifacts(output_dir)
    for path in artifacts:
        register_artifact(state, "plots", posix_path(path))
    return artifacts


def complete_from_lightweight_outputs(
    state: dict[str, Any],
    *,
    wrfinput_inventory: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    real_ready_from_outputs(state, wrfinput_inventory=wrfinput_inventory, dry_run=dry_run)
    if state["status"] == "real_ready":
        transition(state, "running", current_step="wrf-run")
        transition(state, "completed", current_step="wrf-run")
    elif state["status"] == "running":
        transition(state, "completed", current_step="wrf-run")
    elif state["status"] == "completed":
        clear_error(state)
        state["current_step"] = "wrf-run"
    else:
        clear_error(state)
        state["status"] = "completed"
        state["current_step"] = "wrf-run"
    return state


def _local_task_log_candidates(state: dict[str, Any], task: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = posix_path(path)
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    step = str(task.get("step") or "")
    task_log_path = Path(task["log_path"])
    if step == "wrf-run" and str(task.get("state") or "").lower() == "running":
        wrf_dir = Path(state["paths"]["wrf_dir"])
        log_dir = Path(state["paths"]["log_dir"])
        add(wrf_dir / "rsl.out.0000")
        add(wrf_dir / "rsl.error.0000")
        add(task_log_path)
        add(log_dir / "wrf-run-real.log")
        add(log_dir / "wrf-run-wrf.log")
        add(log_dir / "wrf-run.log")
        return candidates

    add(task_log_path)
    return candidates


def resolve_local_task_log_path(
    state: dict[str, Any],
    task: dict[str, Any],
    *,
    require_content: bool = False,
) -> Path:
    candidates = _local_task_log_candidates(state, task)
    if require_content:
        for path in candidates:
            if read_last_nonempty_line(path):
                return path
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return candidates[0]


def poll_interval_seconds(task: dict[str, Any] | None) -> float:
    if not isinstance(task, dict):
        return LOCAL_POLL_INTERVAL_SECONDS

    backend = str(task.get("backend") or "local").lower()
    if backend == "local":
        return LOCAL_POLL_INTERVAL_SECONDS

    state = str(task.get("state") or "").lower()
    if state == "queued":
        return HPC_QUEUED_POLL_INTERVAL_SECONDS
    if state == "running":
        return HPC_RUNNING_POLL_INTERVAL_SECONDS
    return HPC_DEFAULT_POLL_INTERVAL_SECONDS


def render_notification_command(command: list[str] | str, context: dict[str, Any]) -> list[str]:
    if isinstance(command, list):
        return [str(part).format(**context) for part in command]
    return [part.format(**context) for part in str(command).split()]


def run_notification(config: dict[str, Any], project_name: str, task: dict[str, Any]) -> None:
    command = config.get("notifications", {}).get("command")
    if not command:
        return
    context = {
        "project_name": project_name,
        "task_id": task.get("id"),
        "task_step": task.get("step"),
        "task_state": task.get("state"),
        "task_backend": task.get("backend"),
        "task_job_id": task.get("job_id") or "",
        "task_log_path": task.get("log_path") or "",
    }
    rendered = render_notification_command(command, context)
    subprocess.run(rendered, cwd=repo_root(), check=False)


def dispatch_step(step: str, project_name: str, runs_dir: Path, config_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    if step == "wrf-data":
        kwargs = {
            "runs_dir": runs_dir,
            "dry_run": False,
            **params,
        }
        return prepare_data(project_name, **kwargs)
    if step == "wrf-wps":
        kwargs = {
            "runs_dir": runs_dir,
            "config_path": config_path,
            "dry_run": False,
            **params,
        }
        return prepare_wps(project_name, **kwargs)
    if step == "wrf-run":
        kwargs = {
            "runs_dir": runs_dir,
            "config_path": config_path,
            "dry_run": False,
            **params,
        }
        return run_project(project_name, **kwargs)
    raise ValueError(f"Unsupported task step: {step}")


def create_task_metadata(
    project_name: str,
    step: str,
    backend: str,
    project_dir: Path,
    task_id: str,
    *,
    runs_dir: Path,
    config_path: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    task_path = task_dir(project_dir, task_id)
    task_path.mkdir(parents=True, exist_ok=True)
    return {
        "id": task_id,
        "project_name": project_name,
        "step": step,
        "backend": backend,
        "state": "queued",
        "pid": None,
        "job_id": None,
        "log_path": posix_path(task_stdout_path(project_dir, task_id)),
        "submitted_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "last_progress": "queued",
        "params": params,
        "runs_dir": posix_path(runs_dir),
        "config_path": posix_path(config_path),
        "task_dir": posix_path(task_path),
        "task_json": posix_path(task_json_path(project_dir, task_id)),
        "result_path": posix_path(task_result_path(project_dir, task_id)),
        "stdout_path": posix_path(task_stdout_path(project_dir, task_id)),
        "stderr_path": posix_path(task_stderr_path(project_dir, task_id)),
        "exit_code_path": posix_path(task_exit_code_path(project_dir, task_id)),
    }


def store_active_task(project_json: Path, step: str, task: dict[str, Any]) -> dict[str, Any]:
    state = load_project(project_json)
    set_active_task(state, task_summary(task))
    state["current_step"] = step
    save_project(state, project_json)
    return state


def finalize_task(project_json: Path, config: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    state = load_project(project_json)
    record_task_terminal(state, task_summary(task))
    state["current_step"] = task["step"]
    save_project(state, project_json)
    run_notification(config, state["project_name"], task)
    return state


def mark_task_failed(
    project_json: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    task["state"] = "failed"
    task["finished_at"] = utc_now()
    task["last_progress"] = message
    save_task(task)
    state = load_project(project_json)
    if state.get("status") != "failed":
        record_error(state, task["step"], code, message, task["log_path"])
    record_task_terminal(state, task_summary(task))
    save_project(state, project_json)
    run_notification(config, state["project_name"], task)
    return state


def mark_step_error(
    project_json: Path,
    step: str,
    *,
    code: str,
    message: str,
    log_path: Path,
) -> dict[str, Any]:
    state = load_project(project_json)
    record_error(state, step, code, message, posix_path(log_path))
    save_project(state, project_json)
    return state


def build_wps_hpc_plan(
    project_state: dict[str, Any],
    project_dir: Path,
    config: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    work_dir = Path(project_state["paths"]["wps_dir"])
    log_path = Path(project_state["paths"]["log_dir"]) / "wrf-wps.log"
    namelist_path = Path(project_state["artifacts"].get("namelist_wps") or (work_dir / "namelist.wps"))
    if not namelist_path.exists():
        raise TaskPreflightError("WPS_NAMELIST_MISSING", f"Missing namelist.wps: {namelist_path}", log_path)

    spec_path = project_dir / "simulation_spec.json"
    if not spec_path.exists():
        raise TaskPreflightError("WPS_SPEC_MISSING", f"Missing simulation spec: {spec_path}", log_path)

    spec = normalize_spec(load_json(spec_path))
    namelist = read_namelist(namelist_path)
    interval_hours = determine_interval_hours(project_state, namelist)
    expected_outputs = expected_met_em_paths(
        spec,
        namelist,
        work_dir,
        interval_hours=interval_hours,
    )
    output_inventory = build_wps_output_inventory(expected_outputs)
    selected_substeps = resolve_selected_substeps(
        only_step=params.get("only_step"),
        from_step=params.get("from_step"),
    )
    force_execution = params.get("only_step") is not None or params.get("from_step") is not None
    if output_inventory["complete"] and not force_execution:
        return {
            "step": "wrf-wps",
            "reuse_existing_outputs": True,
            "selected_substeps": selected_substeps,
            "output_inventory": output_inventory,
        }

    forcing_files = collect_forcing_files(project_state)
    if not forcing_files:
        raise TaskPreflightError("FORCING_MISSING", "No forcing files are available for wrf-wps", log_path)

    support_sources = resolve_support_sources(config, Path(config["wps_dir"]), spec["data_source"])
    support_inventory = build_support_inventory(support_sources)
    if not support_inventory["complete"]:
        raise TaskPreflightError(
            "WPS_SUPPORT_MISSING",
            f"Missing WPS support files: {', '.join(support_inventory['missing_files'])}",
            log_path,
        )

    staged_support = stage_support_files(support_sources, work_dir)
    prereqs_ok, prereq_message = validate_existing_wps_prerequisites(
        selected_substeps,
        work_dir=work_dir,
        expected_met_em_files=expected_outputs,
    )
    if not prereqs_ok:
        raise TaskPreflightError("WPS_PREREQUISITE_MISSING", str(prereq_message), log_path)
    return {
        "step": "wrf-wps",
        "reuse_existing_outputs": False,
        "selected_substeps": selected_substeps,
        "forcing_files": [posix_path(path) for path in forcing_files],
        "expected_met_em_files": [posix_path(path) for path in expected_outputs],
        "staged_support": staged_support,
        "output_inventory": output_inventory,
    }


def build_run_hpc_plan(
    project_state: dict[str, Any],
    project_dir: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    work_dir = Path(project_state["paths"]["wrf_dir"])
    output_dir = Path(project_state["paths"]["output_dir"])
    log_path = Path(project_state["paths"]["log_dir"]) / "wrf-run.log"
    namelist_path = Path(project_state["artifacts"].get("namelist_input") or (work_dir / "namelist.input"))
    if not namelist_path.exists():
        raise TaskPreflightError("WRF_NAMELIST_MISSING", f"Missing namelist.input: {namelist_path}", log_path)

    namelist = read_namelist(namelist_path)
    domain_count = detect_domain_count(namelist)
    met_em_files = [
        Path(raw_path)
        for raw_path in project_state["artifacts"].get("met_em_files", [])
        if Path(raw_path).exists()
    ]
    if not met_em_files:
        raise TaskPreflightError("MET_EM_MISSING", "No met_em files are available for wrf-run", log_path)

    expected_inputs = expected_wrfinput_paths(work_dir, domain_count)
    boundary_path = work_dir / "wrfbdy_d01"
    wrfinput_inventory = build_inventory(expected_inputs)
    boundary_exists = boundary_path.exists() and boundary_path.is_file()
    wrfout_files = collect_wrfout_files(work_dir, output_dir)
    lightweight_outputs = collect_lightweight_output_artifacts(output_dir)
    default_need_real = not (wrfinput_inventory["complete"] and boundary_exists)
    selected_substeps = resolve_selected_run_substeps(
        only_step=params.get("only_step"),
        from_step=params.get("from_step"),
        need_real=default_need_real,
    )
    force_execution = params.get("only_step") is not None or params.get("from_step") is not None
    if wrfout_files and not force_execution:
        return {
            "step": "wrf-run",
            "reuse_existing_outputs": True,
            "selected_substeps": selected_substeps,
            "expected_wrfinput_files": [posix_path(path) for path in expected_inputs],
            "expected_wrfbdy_file": posix_path(boundary_path),
        }
    if lightweight_outputs and wrfinput_inventory["complete"] and boundary_exists and not force_execution:
        return {
            "step": "wrf-run",
            "reuse_existing_outputs": True,
            "selected_substeps": selected_substeps,
            "expected_wrfinput_files": [posix_path(path) for path in expected_inputs],
            "expected_wrfbdy_file": posix_path(boundary_path),
        }
    if selected_substeps[0] == "wrf" and (not wrfinput_inventory["complete"] or not boundary_exists):
        raise TaskPreflightError(
            "WRF_PREREQUISITE_MISSING",
            "wrf.exe requires existing wrfinput/wrfbdy files when real.exe is not selected",
            log_path,
        )
    return {
        "step": "wrf-run",
        "reuse_existing_outputs": False,
        "selected_substeps": selected_substeps,
        "expected_wrfinput_files": [posix_path(path) for path in expected_inputs],
        "expected_wrfbdy_file": posix_path(boundary_path),
    }


def run_sync_hpc(
    project_dir: Path,
    access_mode: str,
    remote_host: str,
    remote_project_dir: str,
    log_path: Path,
    step: str,
) -> subprocess.CompletedProcess[str]:
    command = [
        "bash",
        str(Path(__file__).with_name("sync_hpc.sh")),
        access_mode,
        str(project_dir),
        remote_host or "-",
        remote_project_dir,
        step,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    write_text(
        log_path,
        "\n".join(
            [
                f"command={json.dumps(command)}",
                f"returncode={completed.returncode}",
                "[stdout]",
                completed.stdout.strip() or "(empty)",
                "[stderr]",
                completed.stderr.strip() or "(empty)",
            ]
        )
        + "\n",
    )
    return completed


def start_local_task(project_name: str, step: str, runs_dir: Path, config_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    project_dir = project_root(runs_dir, project_name)
    project_json = project_json_path(runs_dir, project_name)
    task_id = build_task_id(step)
    task = create_task_metadata(
        project_name,
        step,
        "local",
        project_dir,
        task_id,
        runs_dir=runs_dir,
        config_path=config_path,
        params=params,
    )
    if step == "wrf-data":
        task["log_path"] = posix_path(project_dir / "logs" / "wrf-data.log")
    save_task(task)

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_worker",
        "--project-name",
        project_name,
        "--runs-dir",
        posix_path(runs_dir),
        "--task-id",
        task_id,
    ]
    write_text(task_dir(project_dir, task_id) / "command.sh", " ".join(command) + "\n")
    stdout_handle = task_stdout_path(project_dir, task_id).open("a", encoding="utf-8")
    stderr_handle = task_stderr_path(project_dir, task_id).open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=repo_root(),
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
            text=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    task["pid"] = process.pid
    if hasattr(process, "_child_created"):
        process._child_created = False
    task["state"] = "running"
    task["started_at"] = utc_now()
    task["last_progress"] = "worker_started"
    save_task(task)
    state = store_active_task(project_json, step, task)
    return {
        "accepted": True,
        "project": state,
        "task": task_summary(task),
        "task_path": posix_path(task_dir(project_dir, task_id)),
    }


def start_hpc_task(project_name: str, step: str, runs_dir: Path, config_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    project_dir = project_root(runs_dir, project_name)
    project_json = project_json_path(runs_dir, project_name)
    config = load_json(config_path)
    project_state = load_project(project_json)
    spec = normalize_spec(load_json(project_dir / "simulation_spec.json"))
    extra_plan: dict[str, Any] = {"step": step}

    if step == "wrf-wps":
        try:
            wps_plan = build_wps_hpc_plan(project_state, project_dir, config, params)
        except TaskPreflightError as exc:
            failed_state = mark_step_error(
                project_json,
                step,
                code=exc.code,
                message=str(exc),
                log_path=exc.log_path,
            )
            return {
                "accepted": False,
                "project": failed_state,
                "task": None,
            }

        if wps_plan.get("reuse_existing_outputs"):
            state = register_collected_outputs(
                project_json,
                step=step,
                selected_substeps=wps_plan.get("selected_substeps"),
                task_state="completed",
            )
            return {
                "accepted": True,
                "project": state,
                "task": None,
                "reused_existing_outputs": True,
            }

        extra_plan.update(wps_plan)
    elif step == "wrf-run":
        try:
            run_plan = build_run_hpc_plan(project_state, project_dir, params)
        except TaskPreflightError as exc:
            failed_state = mark_step_error(
                project_json,
                step,
                code=exc.code,
                message=str(exc),
                log_path=exc.log_path,
            )
            return {
                "accepted": False,
                "project": failed_state,
                "task": None,
            }

        if run_plan.get("reuse_existing_outputs"):
            state = register_collected_outputs(
                project_json,
                step=step,
                selected_substeps=run_plan.get("selected_substeps"),
                task_state="completed",
            )
            return {
                "accepted": True,
                "project": state,
                "task": None,
                "reused_existing_outputs": True,
            }

        extra_plan.update(run_plan)

    admission = evaluate_admission(spec, config)
    record_admission(project_state, admission)
    save_project(project_state, project_json)

    if admission["decision"] not in {"admissible_now", "admissible_with_queue"}:
        return {
            "accepted": False,
            "project": project_state,
            "task": None,
            "admission": admission,
        }

    adapter = get_scheduler_adapter(config)
    task_id = build_task_id(step)
    task_params = {**params, "admission": admission}
    if isinstance(extra_plan.get("selected_substeps"), list):
        task_params["selected_substeps"] = list(extra_plan["selected_substeps"])
    task = create_task_metadata(
        project_name,
        step,
        adapter.backend_name,
        project_dir,
        task_id,
        runs_dir=runs_dir,
        config_path=config_path,
        params=task_params,
    )
    submit_log = task_dir(project_dir, task_id) / "hpc-submit.log"
    task["log_path"] = posix_path(submit_log)
    save_task(task)

    rendered_plan = {**admission["recommended_layout"], **extra_plan}
    try:
        rendered_job = adapter.render_job(project_state, rendered_plan, config)
    except Exception as exc:
        failed_state = mark_task_failed(
            project_json,
            config,
            task,
            code="HPC_RENDER_FAILED",
            message=str(exc),
        )
        return {
            "accepted": False,
            "project": failed_state,
            "task": task_summary(task),
            "admission": admission,
        }

    if step == "wrf-wps":
        prepare_hpc_wps_submission(
            project_json,
            selected_substeps=list(extra_plan["selected_substeps"]),
            expected_met_em_files=[Path(path) for path in extra_plan.get("expected_met_em_files", [])],
        )
    elif step == "wrf-run":
        prepare_hpc_run_submission(
            project_json,
            selected_substeps=list(extra_plan["selected_substeps"]),
            expected_inputs=[Path(path) for path in extra_plan.get("expected_wrfinput_files", [])],
            boundary_path=Path(str(extra_plan["expected_wrfbdy_file"])),
        )

    dump_json(task_dir(project_dir, task_id) / "rendered_job.json", rendered_job)
    sync_result = run_sync_hpc(
        project_dir,
        resolve_access_mode(config),
        resolve_transfer_host(config),
        rendered_job["remote_project_dir"],
        submit_log,
        step,
    )
    if sync_result.returncode != 0:
        failed_state = mark_task_failed(
            project_json,
            config,
            task,
            code="HPC_SYNC_FAILED",
            message="Failed to sync project to HPC before submit",
        )
        return {
            "accepted": False,
            "project": failed_state,
            "task": task_summary(task),
            "admission": admission,
        }

    try:
        submit_result = adapter.submit(rendered_job, config)
    except Exception as exc:
        with submit_log.open("a", encoding="utf-8") as handle:
            handle.write(f"submit_error={exc}\n")
        failed_state = mark_task_failed(
            project_json,
            config,
            task,
            code="HPC_SUBMIT_FAILED",
            message=str(exc),
        )
        return {
            "accepted": False,
            "project": failed_state,
            "task": task_summary(task),
            "admission": admission,
        }

    task["job_id"] = submit_result["job_id"]
    task["state"] = "queued"
    task["last_progress"] = admission["decision"]
    save_task(task)
    with submit_log.open("a", encoding="utf-8") as handle:
        handle.write(f"submit_output={submit_result['submit_output']}\n")
    state = store_active_task(project_json, step, task)
    return {
        "accepted": True,
        "project": state,
        "task": task_summary(task),
        "task_path": posix_path(task_dir(project_dir, task_id)),
        "admission": admission,
    }


def find_task(project_dir: Path, state: dict[str, Any], task_id: str | None) -> dict[str, Any] | None:
    resolved_id = task_id
    if resolved_id is None:
        active_task = state.get("execution", {}).get("active_task")
        if isinstance(active_task, dict) and active_task.get("id"):
            resolved_id = str(active_task["id"])
    if resolved_id is None:
        last_task = state.get("execution", {}).get("last_task")
        if isinstance(last_task, dict) and last_task.get("id"):
            resolved_id = str(last_task["id"])
    if resolved_id is None:
        return None
    path = task_json_path(project_dir, resolved_id)
    if not path.exists():
        return None
    return load_json(path)


def refresh_local_task(project_json: Path, task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    state = load_project(project_json)
    project_dir = Path(state["paths"]["project_root"])
    exit_code_file = Path(task["exit_code_path"])
    task_changed = False
    if task["state"] not in TERMINAL_TASK_STATES:
        if exit_code_file.exists():
            exit_code = int(exit_code_file.read_text(encoding="utf-8").strip())
            task["state"] = "completed" if exit_code == 0 else "failed"
            task["finished_at"] = task.get("finished_at") or utc_now()
            task["last_progress"] = f"exit_code={exit_code}"
            task_changed = True
        elif process_alive(task.get("pid")):
            progress_log_path = resolve_local_task_log_path(state, task, require_content=True)
            progress_line = read_last_nonempty_line(progress_log_path)
            desired_progress = progress_line or "running"
            desired_log_path = posix_path(progress_log_path)
            if (
                task["state"] != "running"
                or task.get("last_progress") != desired_progress
                or task.get("log_path") != desired_log_path
            ):
                task["state"] = "running"
                task["started_at"] = task.get("started_at") or utc_now()
                task["last_progress"] = desired_progress
                task["log_path"] = desired_log_path
                task_changed = True
        else:
            task["state"] = "failed"
            task["finished_at"] = task.get("finished_at") or utc_now()
            task["last_progress"] = "worker_exited_without_status"
            task_changed = True

    if task_changed:
        save_task(task)
        config = load_json(task["config_path"])
        state = finalize_task(project_json, config, task) if task["state"] in TERMINAL_TASK_STATES else store_active_task(project_json, task["step"], task)
    return state, task


def refresh_hpc_task(project_json: Path, task: dict[str, Any], config_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if task["state"] in TERMINAL_TASK_STATES:
        return load_project(project_json), task
    config = load_json(config_path)
    adapter = get_scheduler_adapter(config)
    status = adapter.query({"job_id": task["job_id"]}, config)
    task["state"] = status["state"]
    if task["state"] == "running" and not task.get("started_at"):
        task["started_at"] = utc_now()
    if task["state"] in TERMINAL_TASK_STATES and not task.get("finished_at"):
        task["finished_at"] = utc_now()
    task["last_progress"] = status.get("raw_state") or status["state"]
    save_task(task)
    state = finalize_task(project_json, config, task) if task["state"] in TERMINAL_TASK_STATES else store_active_task(project_json, task["step"], task)
    return state, task


def sync_collected_wps_substeps(
    state: dict[str, Any],
    *,
    selected_substeps: list[str],
    expected_outputs: list[Path],
) -> None:
    work_dir = Path(state["paths"]["wps_dir"])
    log_paths = workflow_substep_log_paths(state, "wrf-wps")
    for step_name in selected_substeps:
        log_path = log_paths[step_name]
        marker_state = read_substep_state(log_path)
        outputs = [
            posix_path(path)
            for path in collect_step_output_paths(
                work_dir,
                step_name,
                expected_met_em_files=expected_outputs,
            )
        ]
        if marker_state == "failed":
            finish_substep(
                state,
                "wrf-wps",
                step_name,
                substep_state="failed",
                log_path=posix_path(log_path),
                outputs=outputs,
                error={
                    "code": f"{step_name.upper()}_FAILED",
                    "message": f"HPC {step_name} failed; see {posix_path(log_path)}",
                },
            )
            continue
        if marker_state == "completed" or outputs:
            finish_substep(
                state,
                "wrf-wps",
                step_name,
                substep_state="completed",
                log_path=posix_path(log_path),
                outputs=outputs,
            )


def register_collected_wps_outputs(
    project_json: Path,
    *,
    selected_substeps: list[str] | None = None,
    task_state: str | None = None,
) -> dict[str, Any]:
    state = load_project(project_json)
    work_dir = Path(state["paths"]["wps_dir"])
    spec_path = Path(state["paths"]["project_root"]) / "simulation_spec.json"
    namelist_path = Path(state["artifacts"].get("namelist_wps") or (work_dir / "namelist.wps"))
    spec = normalize_spec(load_json(spec_path))
    namelist = read_namelist(namelist_path)
    interval_hours = determine_interval_hours(state, namelist)
    expected_outputs = expected_met_em_paths(spec, namelist, work_dir, interval_hours=interval_hours)
    output_inventory = build_wps_output_inventory(expected_outputs)
    sync_collected_wps_substeps(
        state,
        selected_substeps=selected_substeps or list(WPS_SUBSTEPS),
        expected_outputs=expected_outputs,
    )
    if str(task_state or "completed").lower() == "completed":
        update_project_for_wps(state, output_inventory, dry_run=False)
    else:
        state["artifacts"]["met_em_files"] = []
        for path in output_inventory["existing_files"]:
            register_artifact(state, "met_em_files", path)
    save_project(state, project_json)
    return state


def sync_collected_run_substeps(
    state: dict[str, Any],
    *,
    selected_substeps: list[str],
    expected_inputs: list[Path],
    boundary_path: Path,
    lightweight_outputs: list[Path],
    task_state: str | None,
) -> None:
    work_dir = Path(state["paths"]["wrf_dir"])
    output_dir = Path(state["paths"]["output_dir"])
    log_paths = workflow_substep_log_paths(state, "wrf-run")
    boundary_exists = boundary_path.exists() and boundary_path.is_file()
    wrfinput_inventory = build_inventory(expected_inputs)
    wrf_outputs = collect_wrfout_files(work_dir, output_dir)
    lightweight_output_paths = [posix_path(path) for path in lightweight_outputs]

    if "real" in selected_substeps:
        real_log_path = log_paths["real"]
        real_marker = read_substep_state(real_log_path)
        real_outputs = [
            posix_path(path)
            for path in collect_run_step_output_paths(
                work_dir,
                output_dir,
                "real",
                expected_inputs=expected_inputs,
                boundary_path=boundary_path,
            )
        ]
        if real_marker == "failed":
            finish_substep(
                state,
                "wrf-run",
                "real",
                substep_state="failed",
                log_path=posix_path(real_log_path),
                outputs=real_outputs,
                error={
                    "code": "REAL_FAILED",
                    "message": f"HPC real failed; see {posix_path(real_log_path)}",
                },
            )
        elif real_marker == "completed" or (wrfinput_inventory["complete"] and boundary_exists):
            finish_substep(
                state,
                "wrf-run",
                "real",
                substep_state="completed",
                log_path=posix_path(real_log_path),
                outputs=real_outputs,
            )

    if "wrf" in selected_substeps:
        wrf_log_path = log_paths["wrf"]
        wrf_marker = read_substep_state(wrf_log_path)
        wrf_outputs_list = [posix_path(path) for path in wrf_outputs] or lightweight_output_paths
        if wrf_marker == "failed":
            finish_substep(
                state,
                "wrf-run",
                "wrf",
                substep_state="failed",
                log_path=posix_path(wrf_log_path),
                outputs=wrf_outputs_list,
                error={
                    "code": "WRF_FAILED",
                    "message": f"HPC wrf failed; see {posix_path(wrf_log_path)}",
                },
            )
        elif (
            wrf_marker == "completed"
            or wrf_outputs_list
            or (str(task_state or "").lower() == "completed" and "wrf" in selected_substeps)
        ):
            finish_substep(
                state,
                "wrf-run",
                "wrf",
                substep_state="completed",
                log_path=posix_path(wrf_log_path),
                outputs=wrf_outputs_list,
            )


def register_collected_outputs(
    project_json: Path,
    *,
    step: str = "wrf-run",
    selected_substeps: list[str] | None = None,
    task_state: str | None = None,
) -> dict[str, Any]:
    if step == "wrf-wps":
        return register_collected_wps_outputs(
            project_json,
            selected_substeps=selected_substeps,
            task_state=task_state,
        )

    state = load_project(project_json)
    work_dir = Path(state["paths"]["wrf_dir"])
    output_dir = Path(state["paths"]["output_dir"])
    namelist_path = Path(state["artifacts"].get("namelist_input") or (work_dir / "namelist.input"))
    namelist = read_namelist(namelist_path)
    domain_count = detect_domain_count(namelist)
    expected_inputs = expected_wrfinput_paths(work_dir, domain_count)
    boundary_path = work_dir / "wrfbdy_d01"
    wrfinput_inventory = build_inventory(expected_inputs)
    wrfout_files = collect_wrfout_files(work_dir, output_dir)
    lightweight_outputs = register_lightweight_output_artifacts(state, output_dir)
    selected = selected_substeps or list(RUN_SUBSTEPS)
    sync_collected_run_substeps(
        state,
        selected_substeps=selected,
        expected_inputs=expected_inputs,
        boundary_path=boundary_path,
        lightweight_outputs=lightweight_outputs,
        task_state=task_state,
    )
    if str(task_state or "completed").lower() != "completed":
        state["artifacts"]["wrfinput_files"] = []
        for path in wrfinput_inventory["existing_files"]:
            register_artifact(state, "wrfinput_files", path)
        state["artifacts"]["wrfout_files"] = [posix_path(path) for path in wrfout_files]
    elif wrfout_files:
        complete_from_existing_outputs(
            state,
            wrfinput_inventory=wrfinput_inventory,
            wrfout_files=[posix_path(path) for path in wrfout_files],
            dry_run=False,
        )
    elif "wrf" in selected and wrfinput_inventory["complete"] and boundary_path.exists() and boundary_path.is_file():
        complete_from_lightweight_outputs(
            state,
            wrfinput_inventory=wrfinput_inventory,
            dry_run=False,
        )
    elif wrfinput_inventory["complete"] and boundary_path.exists() and boundary_path.is_file():
        real_ready_from_outputs(
            state,
            wrfinput_inventory=wrfinput_inventory,
            dry_run=False,
        )
    save_project(state, project_json)
    return state


def start_task(
    project_name: str,
    step: str,
    *,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
    wait: bool = False,
    task_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if step not in TASK_STEPS:
        raise ValueError(f"Unsupported task step: {step}")
    runs_dir = Path(runs_dir)
    config_path = Path(config_path)
    project_json = project_json_path(runs_dir, project_name)
    if not project_json.exists():
        raise FileNotFoundError(f"Missing project.json: {project_json}")
    state = load_project(project_json)
    assert_mutation_allowed(state, step)
    task_kwargs = task_kwargs or {}
    if step not in {"wrf-wps", "wrf-run"} and any(task_kwargs.get(key) for key in ("only_step", "from_step")):
        raise ValueError(f"{step} does not support substep selection")
    if step in WORKFLOW_SUBSTEP_NAMES:
        for key in ("only_step", "from_step"):
            value = task_kwargs.get(key)
            if value and value not in WORKFLOW_SUBSTEP_NAMES[step]:
                raise ValueError(f"{step} does not support substep {value!r}")

    if step in {"wrf-wps", "wrf-run"} and str(state["execution"].get("mode") or "local").lower() == "hpc":
        payload = start_hpc_task(project_name, step, runs_dir, config_path, task_kwargs)
    else:
        payload = start_local_task(project_name, step, runs_dir, config_path, task_kwargs)

    if wait and payload.get("task"):
        return wait_for_task(project_name, task_id=payload["task"]["id"], runs_dir=runs_dir, config_path=config_path)
    return payload


# ---------------------------------------------------------------------------
# monitor implementation
# ---------------------------------------------------------------------------


def _emit_event(text: str) -> None:
    print(text, flush=True)


def _scan_signature(line: str) -> tuple[str | None, str | None]:
    """Return (kind, detail) where kind in {'success', 'failure', None}."""
    upper = line.upper()
    for sig in _WRF_SUCCESS_SIGNATURES:
        if sig.upper() in upper:
            return "success", sig
    for sig in _WRF_FAILURE_SIGNATURES:
        if sig in line or sig.upper() in upper:
            return "failure", sig
    return None, None


def _extract_sim_time(line: str) -> _dt.datetime | None:
    match = _RSL_TIMESTAMP_RE.search(line)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    try:
        return parse_time(raw)
    except ValueError:
        return None


def _percent_for(sim_time: _dt.datetime, start: _dt.datetime, end: _dt.datetime) -> int:
    total = (end - start).total_seconds()
    if total <= 0:
        return 0
    done = (sim_time - start).total_seconds()
    return int(max(0, min(100, done / total * 100)))


def _resolve_simulation_span(project_dir: Path) -> tuple[_dt.datetime, _dt.datetime] | None:
    """Resolve (start, end) from simulation_spec.json, falling back to namelist.input."""
    spec_path = project_dir / "simulation_spec.json"
    if spec_path.exists():
        try:
            spec = load_json(spec_path)
            timing = spec.get("timing", {}) or {}
            start = parse_time(str(timing["start_time"]))
            end = parse_time(str(timing["end_time"]))
            if end > start:
                return start, end
        except Exception:
            pass

    namelist_path = project_dir / "wrf" / "namelist.input"
    if not namelist_path.exists():
        namelist_path = project_dir / "namelist.input"
    if namelist_path.exists():
        try:
            nl = read_namelist(namelist_path)
            tc = nl.get("time_control", {}) or {}

            def _first(value: Any) -> Any:
                return value[0] if isinstance(value, list) else value

            if all(k in tc for k in ("start_year", "start_month", "start_day", "start_hour")) and all(
                k in tc for k in ("end_year", "end_month", "end_day", "end_hour")
            ):
                start = _dt.datetime(
                    int(_first(tc["start_year"])),
                    int(_first(tc["start_month"])),
                    int(_first(tc["start_day"])),
                    int(_first(tc["start_hour"])),
                )
                end = _dt.datetime(
                    int(_first(tc["end_year"])),
                    int(_first(tc["end_month"])),
                    int(_first(tc["end_day"])),
                    int(_first(tc["end_hour"])),
                )
                if end > start:
                    return start, end
                run_days = int(_first(tc.get("run_days", 0)) or 0)
                run_hours = int(_first(tc.get("run_hours", 0)) or 0)
                if run_days or run_hours:
                    return start, start + _dt.timedelta(hours=run_days * 24 + run_hours)
        except Exception:
            pass
    return None


def _infer_substep_from_log_name(name: str) -> str | None:
    for mapping in (WPS_SUBSTEP_LOG_NAMES, RUN_SUBSTEP_LOG_NAMES):
        for substep, filename in mapping.items():
            if filename == name:
                return substep
    return None


def _load_rendered_job(project_dir: Path, task: dict[str, Any]) -> dict[str, Any] | None:
    rendered_path = task_dir(project_dir, str(task.get("id") or "")) / "rendered_job.json"
    if not rendered_path.exists():
        return None
    try:
        return load_json(rendered_path)
    except Exception:
        return None


def _resolve_monitor_log_and_mode(
    state: dict[str, Any],
    task: dict[str, Any],
    substep: str | None,
    *,
    backend: str,
) -> tuple[Path | str, str]:
    """Return (log_path, mode) where mode in {'percentage', 'raw'}.

    For local backend log_path is a Path; for hpc backend it's a remote POSIX string.
    """
    step = str(task.get("step") or "")
    is_local = backend == "local"

    if is_local:
        if substep is not None:
            log_path = resolve_substep_log_path(state, substep)
            effective_substep = substep
        else:
            log_path = resolve_local_task_log_path(state, task, require_content=False)
            effective_substep = _infer_substep_from_log_name(log_path.name)
            if effective_substep is None and step == "wrf-run":
                effective_substep = "wrf"
        # For the wrf substep, the real per-timestep log is rsl.out.0000, not the wrapper log.
        if effective_substep == "wrf" and log_path.name != "rsl.out.0000":
            wrf_dir = Path(state["paths"]["wrf_dir"])
            rsl = wrf_dir / "rsl.out.0000"
            if rsl.exists() or not log_path.exists():
                log_path = rsl
        mode = "percentage" if effective_substep == "wrf" else "raw"
        return log_path, mode

    # hpc backend (any non-local backend): derive remote path from rendered_job.json.
    project_dir = Path(state["paths"]["project_root"])
    rendered = _load_rendered_job(project_dir, task)
    if rendered is None:
        raise RuntimeError("no_rendered_job")
    remote_project_dir = str(rendered.get("remote_project_dir") or "").rstrip("/")
    remote_log_dir = str(rendered.get("remote_log_dir") or "").rstrip("/")
    if not remote_project_dir:
        raise RuntimeError("no_rendered_job")

    if substep == "wrf" or (substep is None and step == "wrf-run"):
        remote_path = f"{remote_project_dir}/wrf/rsl.out.0000"
        return remote_path, "percentage"
    if substep is not None:
        filename = WPS_SUBSTEP_LOG_NAMES.get(substep) or RUN_SUBSTEP_LOG_NAMES.get(substep)
        if filename is None:
            raise RuntimeError(f"unsupported_substep:{substep}")
        if not remote_log_dir:
            raise RuntimeError("no_rendered_job")
        return f"{remote_log_dir}/{filename}", "raw"
    # no substep, non-wrf-run HPC task
    if not remote_log_dir:
        raise RuntimeError("no_rendered_job")
    main_log = _STEP_MAIN_LOG_NAMES.get(step)
    if main_log is None:
        raise RuntimeError(f"unsupported_step:{step}")
    return f"{remote_log_dir}/{main_log}", "raw"


def _compile_raw_filter(filter_spec: str) -> tuple[re.Pattern | None, bool]:
    """Compile a RAW-mode filter spec. Returns (regex_or_None, emit_all_lines)."""
    if filter_spec == "raw":
        return None, True
    if filter_spec == "preset" or not filter_spec:
        return None, False  # preset = only signatures, handled in _emit_line
    try:
        return re.compile(filter_spec), False
    except re.error as exc:
        raise ValueError(f"bad_filter_regex: {exc}") from exc


def _emit_line(
    line: str,
    *,
    mode: str,
    start: _dt.datetime | None,
    end: _dt.datetime | None,
    last_pct_ref: list[int],
    raw_filter_regex: re.Pattern | None,
    raw_emit_all: bool,
) -> int | None:
    """Process one log line. Return exit code on terminal event, None to continue."""
    kind, detail = _scan_signature(line)
    if kind == "success":
        if mode == "percentage" and start is not None and end is not None and last_pct_ref[0] < 100:
            last_pct_ref[0] = 100
            _emit_event(f"100%  {end.strftime('%Y-%m-%d_%H:%M:%S')}")
        _emit_event("SUCCESS")
        return 0
    if kind == "failure":
        _emit_event(f"FAILED {detail}")
        return 1

    if mode == "percentage":
        if start is None or end is None:
            return None
        sim_time = _extract_sim_time(line)
        if sim_time is None:
            return None
        pct = _percent_for(sim_time, start, end)
        if pct > last_pct_ref[0]:
            last_pct_ref[0] = pct
            _emit_event(f"{pct}%  {sim_time.strftime('%Y-%m-%d_%H:%M:%S')}")
        return None

    # RAW mode
    if raw_emit_all:
        _emit_event(line)
    elif raw_filter_regex is not None:
        if raw_filter_regex.search(line):
            _emit_event(line)
    # preset (default): signatures only, already handled above.
    return None


def _wait_for_local_log(log_path: Path, timeout: float) -> bool:
    deadline = time.time() + timeout
    while not (log_path.exists() and log_path.is_file()):
        if time.time() >= deadline:
            return False
        time.sleep(0.5)
    return True


def _stream_local_log(
    log_path: Path,
    *,
    mode: str,
    start: _dt.datetime | None,
    end: _dt.datetime | None,
    project_json: Path,
    task: dict[str, Any],
    refresh_backup_every_n: int,
    log_appear_timeout: float,
    raw_filter_regex: re.Pattern | None,
    raw_emit_all: bool,
) -> int:
    if not _wait_for_local_log(log_path, log_appear_timeout):
        _emit_event(f"FAILED log_not_found {posix_path(log_path)}")
        return 1

    last_pct_ref = [-1]
    offset = 0
    buf = ""
    poll_count = 0
    while True:
        try:
            with log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                if size > offset:
                    handle.seek(offset)
                    data = handle.read(size - offset)
                    offset = size
                    buf += data.decode("utf-8", errors="replace")
        except OSError:
            pass

        if "\n" in buf:
            parts = buf.split("\n")
            buf = parts[-1]
            for line in parts[:-1]:
                rc = _emit_line(
                    line.rstrip("\r"),
                    mode=mode,
                    start=start,
                    end=end,
                    last_pct_ref=last_pct_ref,
                    raw_filter_regex=raw_filter_regex,
                    raw_emit_all=raw_emit_all,
                )
                if rc is not None:
                    return rc

        poll_count += 1
        if poll_count % refresh_backup_every_n == 0:
            try:
                _, refreshed = refresh_local_task(project_json, dict(task))
                task.update(refreshed)
                if task.get("state") in TERMINAL_TASK_STATES:
                    _emit_event(f"ENDED {task['state']}")
                    return 0 if task["state"] == "completed" else 1
            except Exception:
                pass
        time.sleep(MONITOR_LOCAL_POLL_INTERVAL_SECONDS)


def _wait_for_remote_file(remote_path: str, *, config: dict[str, Any], timeout: float) -> bool:
    deadline = time.time() + timeout
    probe = f"test -f {shlex.quote(remote_path)} && echo ok"
    while True:
        try:
            completed = subprocess.run(
                build_remote_command_argv(probe, config=config),
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.stdout.strip() == "ok":
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(MONITOR_REMOTE_FILE_PROBE_INTERVAL_SECONDS)


def _start_remote_tail(remote_path: str, *, config: dict[str, Any], from_line: int = 1) -> subprocess.Popen:
    cmd = f"tail -n +{from_line} -F {shlex.quote(remote_path)}"
    argv = build_remote_command_argv(cmd, config=config)
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)


def _remote_line_count(remote_path: str, *, config: dict[str, Any]) -> int:
    """Best-effort current line count of the remote file (for reconnect offset)."""
    try:
        completed = subprocess.run(
            build_remote_command_argv(f"wc -l < {shlex.quote(remote_path)}", config=config),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return int(completed.stdout.strip() or 0)
    except Exception:
        pass
    return 0


def _mark_task_needs_intervention(project_dir: Path, task: dict[str, Any], reason: str) -> None:
    try:
        task["monitor_flag"] = "needs_intervention"
        task["monitor_reason"] = reason
        save_task(task)
    except Exception:
        pass


def _stream_hpc_log(
    remote_path: str,
    *,
    config: dict[str, Any],
    mode: str,
    start: _dt.datetime | None,
    end: _dt.datetime | None,
    project_json: Path,
    task: dict[str, Any],
    config_path: Path,
    refresh_backup_every_n: int,
    log_appear_timeout: float,
    raw_filter_regex: re.Pattern | None,
    raw_emit_all: bool,
) -> int:
    if not _wait_for_remote_file(remote_path, config=config, timeout=log_appear_timeout):
        _emit_event(f"FAILED remote_log_not_found {remote_path}")
        return 1

    project_dir = Path(load_project(project_json)["paths"]["project_root"])
    last_pct_ref = [-1]
    lines_read = 0
    poll_count = 0
    from_line = 1
    proc = _start_remote_tail(remote_path, config=config, from_line=from_line)
    reconnect_attempts = 0

    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                rc = proc.poll()
                if rc is not None:
                    # ssh/tail died; attempt reconnect up to MONITOR_SSH_RECONNECT_TRIES.
                    if reconnect_attempts < MONITOR_SSH_RECONNECT_TRIES:
                        backoff = MONITOR_SSH_RECONNECT_BACKOFF[reconnect_attempts]
                        reconnect_attempts += 1
                        time.sleep(backoff)
                        # Resume from the line after the last one we read.
                        from_line = lines_read + 1
                        try:
                            proc = _start_remote_tail(remote_path, config=config, from_line=from_line)
                        except Exception:
                            pass
                        continue
                    # Reconnect exhausted: pause task for human intervention.
                    _mark_task_needs_intervention(project_dir, task, "ssh_disconnected")
                    _emit_event("INTERVENTION_NEEDED ssh_disconnected")
                    return 1
                continue

            lines_read += 1
            rc = _emit_line(
                line.rstrip("\r\n"),
                mode=mode,
                start=start,
                end=end,
                last_pct_ref=last_pct_ref,
                raw_filter_regex=raw_filter_regex,
                raw_emit_all=raw_emit_all,
            )
            if rc is not None:
                return rc

            poll_count += 1
            if poll_count % refresh_backup_every_n == 0:
                try:
                    _, refreshed = refresh_hpc_task(project_json, dict(task), Path(config_path))
                    task.update(refreshed)
                    if task.get("state") in TERMINAL_TASK_STATES:
                        _emit_event(f"ENDED {task['state']}")
                        return 0 if task["state"] == "completed" else 1
                except Exception:
                    pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


def monitor_task(
    project_name: str,
    *,
    task_id: str | None = None,
    substep: str | None = None,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
    filter: str = "preset",
    log_appear_timeout: float = MONITOR_LOG_APPEAR_TIMEOUT_SECONDS,
) -> int:
    runs_dir = Path(runs_dir)
    config_path = Path(config_path)
    project_dir = project_root(runs_dir, project_name)
    project_json = project_json_path(runs_dir, project_name)
    try:
        state = load_project(project_json)
    except FileNotFoundError:
        _emit_event("FAILED no_project")
        return 2
    task = find_task(project_dir, state, task_id)
    if task is None:
        _emit_event("FAILED no_task")
        return 2

    backend = str(task.get("backend") or "local").lower()
    try:
        log_path, mode = _resolve_monitor_log_and_mode(state, task, substep, backend=backend)
    except RuntimeError as exc:
        _emit_event(f"FAILED {exc}")
        return 1

    start: _dt.datetime | None = None
    end: _dt.datetime | None = None
    if mode == "percentage":
        span = _resolve_simulation_span(project_dir)
        if span is None:
            _emit_event("FAILED no_span")
            return 1
        start, end = span

    raw_filter_regex: re.Pattern | None = None
    raw_emit_all = False
    if mode == "raw":
        try:
            raw_filter_regex, raw_emit_all = _compile_raw_filter(filter)
        except ValueError as exc:
            _emit_event(f"FAILED {exc}")
            return 1

    if backend != "local":
        config = load_json(config_path)
        return _stream_hpc_log(
            str(log_path),
            config=config,
            mode=mode,
            start=start,
            end=end,
            project_json=project_json,
            task=task,
            config_path=config_path,
            refresh_backup_every_n=MONITOR_REFRESH_BACKUP_EVERY_N_HPC,
            log_appear_timeout=log_appear_timeout,
            raw_filter_regex=raw_filter_regex,
            raw_emit_all=raw_emit_all,
        )

    return _stream_local_log(
        Path(log_path),
        mode=mode,
        start=start,
        end=end,
        project_json=project_json,
        task=task,
        refresh_backup_every_n=MONITOR_REFRESH_BACKUP_EVERY_N_LOCAL,
        log_appear_timeout=log_appear_timeout,
        raw_filter_regex=raw_filter_regex,
        raw_emit_all=raw_emit_all,
    )


def status_task(
    project_name: str,
    *,
    task_id: str | None = None,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    project_dir = project_root(runs_dir, project_name)
    project_json = project_json_path(runs_dir, project_name)
    state = load_project(project_json)
    task = find_task(project_dir, state, task_id)
    if task is None:
        return {"project": state, "task": None}
    if task["backend"] == "local":
        state, task = refresh_local_task(project_json, task)
    else:
        state, task = refresh_hpc_task(project_json, task, Path(config_path))
    return {"project": state, "task": task_summary(task)}


def wait_for_task(
    project_name: str,
    *,
    task_id: str | None = None,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while True:
        payload = status_task(project_name, task_id=task_id, runs_dir=runs_dir, config_path=config_path)
        task = payload.get("task")
        if task is None or task["state"] in TERMINAL_TASK_STATES:
            return payload
        if time.time() >= deadline:
            raise TimeoutError(f"Task {task['id']} did not finish within {timeout_seconds} seconds")
        time.sleep(poll_interval_seconds(task))


def logs_task(
    project_name: str,
    *,
    task_id: str | None = None,
    runs_dir: Path | str = "runs",
    lines: int = 200,
    substep: str | None = None,
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    project_dir = project_root(runs_dir, project_name)
    state = load_project(project_json_path(runs_dir, project_name))
    if substep is not None:
        log_path = resolve_substep_log_path(state, substep)
        if not log_path.exists():
            return {"project": state, "task": None, "log_path": posix_path(log_path), "text": ""}
        tail = read_last_lines_text(log_path, lines=lines)
        return {
            "project": state,
            "task": None,
            "log_path": posix_path(log_path),
            "text": tail,
        }
    task = find_task(project_dir, state, task_id)
    if task is None:
        return {"project": state, "task": None, "log_path": None, "text": ""}
    log_path = (
        resolve_local_task_log_path(state, task, require_content=True)
        if task["backend"] == "local"
        else Path(task["log_path"])
    )
    if not log_path.exists():
        return {"project": state, "task": task_summary(task), "log_path": posix_path(log_path), "text": ""}
    tail = read_last_lines_text(log_path, lines=lines)
    return {
        "project": state,
        "task": task_summary(task),
        "log_path": posix_path(log_path),
        "text": tail,
    }


def cancel_task(
    project_name: str,
    *,
    task_id: str | None = None,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    project_dir = project_root(runs_dir, project_name)
    project_json = project_json_path(runs_dir, project_name)
    state = load_project(project_json)
    task = find_task(project_dir, state, task_id)
    if task is None:
        return {"project": state, "task": None}
    if task["state"] in TERMINAL_TASK_STATES:
        return {"project": state, "task": task_summary(task)}

    config = load_json(config_path)
    if task["backend"] == "local":
        if task.get("pid") and process_alive(task["pid"]):
            os.killpg(task["pid"], signal.SIGTERM)
    else:
        adapter = get_scheduler_adapter(config)
        adapter.cancel({"job_id": task["job_id"]}, config)
    task["state"] = "canceled"
    task["finished_at"] = utc_now()
    task["last_progress"] = "canceled"
    save_task(task)
    state = finalize_task(project_json, config, task)
    return {"project": state, "task": task_summary(task)}


def collect_task(
    project_name: str,
    *,
    task_id: str | None = None,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    project_dir = project_root(runs_dir, project_name)
    project_json = project_json_path(runs_dir, project_name)
    state = load_project(project_json)
    task = find_task(project_dir, state, task_id)
    if task is None:
        return {"project": state, "task": None}
    if task["backend"] == "local":
        return status_task(project_name, task_id=task.get("id"), runs_dir=runs_dir, config_path=config_path)

    if task["state"] not in TERMINAL_TASK_STATES:
        raise RuntimeError(f"HPC task {task['id']} is not terminal yet")

    config = load_json(config_path)
    rendered_job_path = Path(task["task_dir"]) / "rendered_job.json"
    rendered_job = load_json(rendered_job_path)
    selected_substeps = resolve_hpc_selected_substeps(task["step"], task, rendered_job)
    command = [
        "bash",
        str(Path(__file__).with_name("collect_hpc.sh")),
        resolve_access_mode(config),
        resolve_transfer_host(config) or "-",
        rendered_job["remote_project_dir"],
        str(project_dir),
        task["step"],
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    with Path(task["log_path"]).open("a", encoding="utf-8") as handle:
        handle.write(f"collect_command={json.dumps(command)}\n")
        handle.write(f"collect_returncode={completed.returncode}\n")
        handle.write(f"collect_stdout={completed.stdout.strip() or '(empty)'}\n")
        handle.write(f"collect_stderr={completed.stderr.strip() or '(empty)'}\n")
    if completed.returncode != 0:
        failed_state = mark_task_failed(
            project_json,
            config,
            task,
            code="HPC_COLLECT_FAILED",
            message="Failed to collect HPC outputs",
        )
        return {"project": failed_state, "task": task_summary(task)}

    state = register_collected_outputs(
        project_json,
        step=task["step"],
        selected_substeps=selected_substeps,
        task_state=task["state"],
    )
    task["last_progress"] = "collected"
    save_task(task)
    record_task_terminal(state, task_summary(task))
    save_project(state, project_json)
    return {"project": state, "task": task_summary(task)}


def worker_main(project_name: str, runs_dir: Path, task_id: str) -> int:
    project_dir = project_root(runs_dir, project_name)
    project_json = project_json_path(runs_dir, project_name)
    task = load_task(project_dir, task_id)
    config = load_json(task["config_path"])
    task["state"] = "running"
    task["started_at"] = task.get("started_at") or utc_now()
    task["last_progress"] = "running"
    save_task(task)
    store_active_task(project_json, task["step"], task)
    exit_code = 1
    try:
        payload = dispatch_step(
            task["step"],
            project_name,
            Path(task["runs_dir"]),
            Path(task["config_path"]),
            task.get("params", {}),
        )
        dump_json(task["result_path"], payload)
        if isinstance(payload, dict) and payload.get("log_path"):
            task["log_path"] = str(payload["log_path"])
        exit_code = 0
        task["state"] = "completed"
        task["finished_at"] = utc_now()
        task["last_progress"] = "completed"
        save_task(task)
        finalize_task(project_json, config, task)
    except Exception as exc:
        task["state"] = "failed"
        task["finished_at"] = utc_now()
        task["last_progress"] = str(exc)
        save_task(task)
        state = load_project(project_json)
        if state.get("status") != "failed":
            record_error(state, task["step"], "TASK_FAILED", str(exc), task["log_path"])
        record_task_terminal(state, task_summary(task))
        save_project(state, project_json)
        run_notification(config, project_name, task)
        traceback.print_exc()
    finally:
        write_text(Path(task["exit_code_path"]), f"{exit_code}\n")
    return exit_code


def parse_task_kwargs(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("task kwargs JSON must be an object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified async task controller for WRF steps")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--project-name", required=True)
    start.add_argument("--step", required=True, choices=sorted(TASK_STEPS))
    start.add_argument("--runs-dir", default="runs")
    start.add_argument("--config", default="config/wrf_env.json")
    start.add_argument("--wait", action="store_true")
    start.add_argument("--only", choices=sorted(SUPPORTED_SUBSTEP_NAMES))
    start.add_argument("--from-step", "--from", dest="from_step", choices=sorted(SUPPORTED_SUBSTEP_NAMES))
    start.add_argument("--task-kwargs-json")

    status = subparsers.add_parser("status")
    status.add_argument("--project-name", required=True)
    status.add_argument("--task-id")
    status.add_argument("--runs-dir", default="runs")
    status.add_argument("--config", default="config/wrf_env.json")

    logs = subparsers.add_parser("logs")
    logs.add_argument("--project-name", required=True)
    logs.add_argument("--task-id")
    logs.add_argument("--runs-dir", default="runs")
    logs.add_argument("--lines", type=int, default=200)
    logs.add_argument("--substep", choices=sorted(SUPPORTED_SUBSTEP_NAMES))

    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("--project-name", required=True)
    cancel.add_argument("--task-id")
    cancel.add_argument("--runs-dir", default="runs")
    cancel.add_argument("--config", default="config/wrf_env.json")

    collect = subparsers.add_parser("collect")
    collect.add_argument("--project-name", required=True)
    collect.add_argument("--task-id")
    collect.add_argument("--runs-dir", default="runs")
    collect.add_argument("--config", default="config/wrf_env.json")

    monitor = subparsers.add_parser("monitor")
    monitor.add_argument("--project-name", required=True)
    monitor.add_argument("--task-id")
    monitor.add_argument("--runs-dir", default="runs")
    monitor.add_argument("--config", default="config/wrf_env.json")
    monitor.add_argument("--substep", choices=sorted(SUPPORTED_SUBSTEP_NAMES))
    monitor.add_argument(
        "--filter",
        default="preset",
        help="RAW-mode line filter: preset|raw|<regex> (ignored in percentage mode)",
    )
    monitor.add_argument(
        "--log-appear-timeout",
        type=float,
        default=MONITOR_LOG_APPEAR_TIMEOUT_SECONDS,
        help="Seconds to wait for the log file to appear before failing",
    )

    worker = subparsers.add_parser("_worker")
    worker.add_argument("--project-name", required=True)
    worker.add_argument("--runs-dir", required=True)
    worker.add_argument("--task-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "start":
        task_kwargs = parse_task_kwargs(args.task_kwargs_json)
        if args.only:
            task_kwargs["only_step"] = args.only
        if args.from_step:
            task_kwargs["from_step"] = args.from_step
        payload = start_task(
            args.project_name,
            args.step,
            runs_dir=args.runs_dir,
            config_path=args.config,
            wait=args.wait,
            task_kwargs=task_kwargs,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(status_task(args.project_name, task_id=args.task_id, runs_dir=args.runs_dir, config_path=args.config), indent=2))
        return 0
    if args.command == "logs":
        print(
            json.dumps(
                logs_task(
                    args.project_name,
                    task_id=args.task_id,
                    runs_dir=args.runs_dir,
                    lines=args.lines,
                    substep=args.substep,
                ),
                indent=2,
            )
        )
        return 0
    if args.command == "cancel":
        print(json.dumps(cancel_task(args.project_name, task_id=args.task_id, runs_dir=args.runs_dir, config_path=args.config), indent=2))
        return 0
    if args.command == "collect":
        print(json.dumps(collect_task(args.project_name, task_id=args.task_id, runs_dir=args.runs_dir, config_path=args.config), indent=2))
        return 0
    if args.command == "monitor":
        return monitor_task(
            args.project_name,
            task_id=args.task_id,
            substep=args.substep,
            runs_dir=args.runs_dir,
            config_path=args.config,
            filter=args.filter,
            log_appear_timeout=args.log_appear_timeout,
        )
    if args.command == "_worker":
        return worker_main(args.project_name, Path(args.runs_dir), args.task_id)
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
