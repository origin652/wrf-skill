from __future__ import annotations

import argparse
import json
import os
import signal
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
    from hpc.base import resolve_access_mode, resolve_transfer_host
    from namelist_parser import read_namelist
    from project_state import (
        assert_mutation_allowed,
        load_project,
        record_admission,
        record_error,
        record_task_terminal,
        save_project,
        set_active_task,
    )
    from spec_utils import normalize_spec
    from utils import posix_path, utc_now
    from wrf_data import prepare_data
    from wrf_run import (
        build_inventory,
        collect_wrfout_files,
        complete_from_existing_outputs,
        detect_domain_count,
        expected_wrfinput_paths,
        real_ready_from_outputs,
        run_project,
    )
    from wrf_wps import (
        build_output_inventory as build_wps_output_inventory,
        build_support_inventory,
        collect_forcing_files,
        determine_interval_hours,
        expected_met_em_paths,
        prepare_wps,
        resolve_support_sources,
        stage_support_files,
        update_project_for_wps,
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
    from .hpc.base import resolve_access_mode, resolve_transfer_host
    from .namelist_parser import read_namelist
    from .project_state import (
        assert_mutation_allowed,
        load_project,
        record_admission,
        record_error,
        record_task_terminal,
        save_project,
        set_active_task,
    )
    from .spec_utils import normalize_spec
    from .utils import posix_path, utc_now
    from .wrf_data import prepare_data
    from .wrf_run import (
        build_inventory,
        collect_wrfout_files,
        complete_from_existing_outputs,
        detect_domain_count,
        expected_wrfinput_paths,
        real_ready_from_outputs,
        run_project,
    )
    from .wrf_wps import (
        build_output_inventory as build_wps_output_inventory,
        build_support_inventory,
        collect_forcing_files,
        determine_interval_hours,
        expected_met_em_paths,
        prepare_wps,
        resolve_support_sources,
        stage_support_files,
        update_project_for_wps,
    )


class TaskPreflightError(RuntimeError):
    def __init__(self, code: str, message: str, log_path: Path) -> None:
        super().__init__(message)
        self.code = code
        self.log_path = log_path


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
    if output_inventory["complete"]:
        return {
            "step": "wrf-wps",
            "reuse_existing_outputs": True,
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
    return {
        "step": "wrf-wps",
        "reuse_existing_outputs": False,
        "forcing_files": [posix_path(path) for path in forcing_files],
        "expected_met_em_files": [posix_path(path) for path in expected_outputs],
        "staged_support": staged_support,
        "output_inventory": output_inventory,
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
            wps_plan = build_wps_hpc_plan(project_state, project_dir, config)
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
            state = register_collected_outputs(project_json, step=step)
            return {
                "accepted": True,
                "project": state,
                "task": None,
                "reused_existing_outputs": True,
            }

        extra_plan.update(wps_plan)

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
    task = create_task_metadata(
        project_name,
        step,
        adapter.backend_name,
        project_dir,
        task_id,
        runs_dir=runs_dir,
        config_path=config_path,
        params={**params, "admission": admission},
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


def register_collected_wps_outputs(project_json: Path) -> dict[str, Any]:
    state = load_project(project_json)
    work_dir = Path(state["paths"]["wps_dir"])
    spec_path = Path(state["paths"]["project_root"]) / "simulation_spec.json"
    namelist_path = Path(state["artifacts"].get("namelist_wps") or (work_dir / "namelist.wps"))
    spec = normalize_spec(load_json(spec_path))
    namelist = read_namelist(namelist_path)
    interval_hours = determine_interval_hours(state, namelist)
    expected_outputs = expected_met_em_paths(spec, namelist, work_dir, interval_hours=interval_hours)
    output_inventory = build_wps_output_inventory(expected_outputs)
    update_project_for_wps(state, output_inventory, dry_run=False)
    save_project(state, project_json)
    return state


def register_collected_outputs(project_json: Path, *, step: str = "wrf-run") -> dict[str, Any]:
    if step == "wrf-wps":
        return register_collected_wps_outputs(project_json)

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
    if wrfout_files:
        complete_from_existing_outputs(
            state,
            wrfinput_inventory=wrfinput_inventory,
            wrfout_files=[posix_path(path) for path in wrfout_files],
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

    if step in {"wrf-wps", "wrf-run"} and str(state["execution"].get("mode") or "local").lower() == "hpc":
        payload = start_hpc_task(project_name, step, runs_dir, config_path, task_kwargs)
    else:
        payload = start_local_task(project_name, step, runs_dir, config_path, task_kwargs)

    if wait and payload.get("task"):
        return wait_for_task(project_name, task_id=payload["task"]["id"], runs_dir=runs_dir, config_path=config_path)
    return payload


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
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    project_dir = project_root(runs_dir, project_name)
    state = load_project(project_json_path(runs_dir, project_name))
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
    command = [
        "bash",
        str(Path(__file__).with_name("collect_hpc.sh")),
        resolve_access_mode(config),
        resolve_transfer_host(config) or "-",
        rendered_job["remote_project_dir"],
        str(project_dir),
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

    state = register_collected_outputs(project_json, step=task["step"])
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

    worker = subparsers.add_parser("_worker")
    worker.add_argument("--project-name", required=True)
    worker.add_argument("--runs-dir", required=True)
    worker.add_argument("--task-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "start":
        payload = start_task(
            args.project_name,
            args.step,
            runs_dir=args.runs_dir,
            config_path=args.config,
            wait=args.wait,
            task_kwargs=parse_task_kwargs(args.task_kwargs_json),
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(status_task(args.project_name, task_id=args.task_id, runs_dir=args.runs_dir, config_path=args.config), indent=2))
        return 0
    if args.command == "logs":
        print(json.dumps(logs_task(args.project_name, task_id=args.task_id, runs_dir=args.runs_dir, lines=args.lines), indent=2))
        return 0
    if args.command == "cancel":
        print(json.dumps(cancel_task(args.project_name, task_id=args.task_id, runs_dir=args.runs_dir, config_path=args.config), indent=2))
        return 0
    if args.command == "collect":
        print(json.dumps(collect_task(args.project_name, task_id=args.task_id, runs_dir=args.runs_dir, config_path=args.config), indent=2))
        return 0
    if args.command == "_worker":
        return worker_main(args.project_name, Path(args.runs_dir), args.task_id)
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
