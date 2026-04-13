from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from constants import (
        BLOCKING_TASK_STATES,
        TASK_STATES,
        TERMINAL_TASK_STATES,
        VALID_STATUSES,
    )
    from spec_utils import default_spec
    from utils import posix_path as posix, utc_now
except ImportError:  # pragma: no cover
    from .constants import (
        BLOCKING_TASK_STATES,
        TASK_STATES,
        TERMINAL_TASK_STATES,
        VALID_STATUSES,
    )
    from .spec_utils import default_spec
    from .utils import posix_path as posix, utc_now

ALLOWED_TRANSITIONS = {
    "created": {"env_checked", "configured", "failed"},
    "env_checked": {"configured", "failed"},
    "configured": {"data_ready", "failed"},
    "data_ready": {"wps_ready", "failed"},
    "wps_ready": {"real_ready", "failed"},
    "real_ready": {"running", "completed", "failed"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


def simulation_spec_template(project_name: str) -> dict[str, Any]:
    return default_spec(project_name)


def create_project_state(
    project_name: str,
    project_root: Path | str,
    *,
    platform: str = "wsl",
    execution_mode: str = "local",
    execution_access_mode: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(project_root)
    return {
        "project_name": project_name,
        "platform": platform,
        "status": "created",
        "current_step": "wrf-init",
        "paths": {
            "project_root": posix(root),
            "data_dir": posix(root / "data"),
            "wps_dir": posix(root / "wps"),
            "wrf_dir": posix(root / "wrf"),
            "output_dir": posix(root / "output"),
            "log_dir": posix(root / "logs"),
        },
        "artifacts": {
            "namelist_wps": None,
            "namelist_input": None,
            "data_manifest": None,
            "forcing_files": [],
            "met_em_files": [],
            "wrfinput_files": [],
            "wrfout_files": [],
            "plots": [],
        },
        "data_source": {
            "type": "gfs",
            "start_time": None,
            "end_time": None,
            "interval_hours": 3,
        },
        "execution": {
            "mode": execution_mode,
            "access_mode": execution_access_mode,
            "dry_run": dry_run,
            "job_id": None,
            "active_task": None,
            "last_task": None,
            "last_admission": None,
        },
        "last_error": None,
        "updated_at": utc_now(),
    }


def ensure_execution_fields(state: dict[str, Any]) -> dict[str, Any]:
    execution = state.setdefault("execution", {})
    execution.setdefault("mode", "local")
    execution.setdefault("access_mode", None)
    execution.setdefault("dry_run", False)
    execution.setdefault("job_id", None)
    execution.setdefault("active_task", None)
    execution.setdefault("last_task", None)
    execution.setdefault("last_admission", None)
    return execution


def load_project(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    ensure_execution_fields(payload)
    return payload


def save_project(state: dict[str, Any], path: Path | str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot = deepcopy(state)
    ensure_execution_fields(snapshot)
    snapshot["updated_at"] = utc_now()
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=False)
        handle.write("\n")


def transition(
    state: dict[str, Any],
    next_status: str,
    *,
    current_step: str | None = None,
    allow_retry: bool = False,
) -> dict[str, Any]:
    if next_status not in VALID_STATUSES:
        raise ValueError(f"Unsupported status: {next_status}")

    current_status = state.get("status")
    if current_status == next_status:
        if current_step is not None:
            state["current_step"] = current_step
        state["updated_at"] = utc_now()
        return state

    if current_status == "failed" and allow_retry:
        pass
    else:
        allowed = ALLOWED_TRANSITIONS.get(current_status, set())
        if next_status not in allowed:
            raise ValueError(f"Illegal transition: {current_status} -> {next_status}")

    state["status"] = next_status
    if current_step is not None:
        state["current_step"] = current_step
    state["updated_at"] = utc_now()
    return state


def register_artifact(state: dict[str, Any], kind: str, path: str) -> dict[str, Any]:
    artifacts = state.setdefault("artifacts", {})
    if kind not in artifacts:
        raise KeyError(f"Unknown artifact kind: {kind}")

    existing = artifacts[kind]
    if isinstance(existing, list):
        if path not in existing:
            existing.append(path)
    else:
        artifacts[kind] = path

    state["updated_at"] = utc_now()
    return state


def record_error(
    state: dict[str, Any],
    step: str,
    code: str,
    message: str,
    log_path: str,
) -> dict[str, Any]:
    state["last_error"] = {
        "step": step,
        "code": code,
        "message": message,
        "log_path": log_path,
        "time": utc_now(),
    }
    state["status"] = "failed"
    state["current_step"] = step
    state["updated_at"] = utc_now()
    return state


def clear_error(state: dict[str, Any]) -> dict[str, Any]:
    state["last_error"] = None
    state["updated_at"] = utc_now()
    return state


def has_blocking_active_task(state: dict[str, Any]) -> bool:
    execution = ensure_execution_fields(state)
    active_task = execution.get("active_task")
    if not isinstance(active_task, dict):
        return False
    return str(active_task.get("state") or "").lower() in BLOCKING_TASK_STATES


def assert_mutation_allowed(state: dict[str, Any], step: str) -> None:
    execution = ensure_execution_fields(state)
    active_task = execution.get("active_task")
    if not isinstance(active_task, dict):
        return
    active_state = str(active_task.get("state") or "").lower()
    if active_state not in BLOCKING_TASK_STATES:
        return
    active_step = active_task.get("step") or "unknown"
    active_id = active_task.get("id") or "unknown"
    raise RuntimeError(
        f"Cannot run {step} while task {active_id} ({active_step}) is {active_state}"
    )


def set_active_task(state: dict[str, Any], task: dict[str, Any] | None) -> dict[str, Any]:
    execution = ensure_execution_fields(state)
    execution["active_task"] = deepcopy(task) if task is not None else None
    execution["job_id"] = None if task is None else task.get("job_id")
    state["updated_at"] = utc_now()
    return state


def update_active_task(state: dict[str, Any], **changes: Any) -> dict[str, Any]:
    execution = ensure_execution_fields(state)
    active_task = execution.get("active_task")
    if not isinstance(active_task, dict):
        raise RuntimeError("No active task is registered in project state")
    active_task.update(changes)
    execution["job_id"] = active_task.get("job_id")
    state["updated_at"] = utc_now()
    return state


def record_task_terminal(
    state: dict[str, Any],
    task_summary: dict[str, Any],
    *,
    keep_as_active: bool = True,
) -> dict[str, Any]:
    execution = ensure_execution_fields(state)
    summary = deepcopy(task_summary)
    execution["last_task"] = summary
    execution["job_id"] = summary.get("job_id")
    if keep_as_active:
        execution["active_task"] = summary
    elif execution.get("active_task", {}).get("id") == summary.get("id"):
        execution["active_task"] = None
    state["updated_at"] = utc_now()
    return state


def record_admission(state: dict[str, Any], admission: dict[str, Any] | None) -> dict[str, Any]:
    execution = ensure_execution_fields(state)
    execution["last_admission"] = deepcopy(admission) if admission is not None else None
    state["updated_at"] = utc_now()
    return state


def clear_downstream_artifacts(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = state.setdefault("artifacts", {})
    artifacts["data_manifest"] = None
    for key in ("forcing_files", "met_em_files", "wrfinput_files", "wrfout_files", "plots"):
        artifacts[key] = []
    execution = ensure_execution_fields(state)
    execution["job_id"] = None
    state["updated_at"] = utc_now()
    return state


def reset_after_reconfigure(state: dict[str, Any]) -> dict[str, Any]:
    clear_error(state)
    clear_downstream_artifacts(state)
    state["status"] = "configured"
    state["current_step"] = "wrf-config"
    state["updated_at"] = utc_now()
    return state


def seed_project(
    project_name: str,
    runs_dir: Path | str,
    *,
    platform: str = "wsl",
    execution_mode: str = "local",
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    runs_root = Path(runs_dir)
    project_root = runs_root / project_name
    state = create_project_state(
        project_name,
        project_root,
        platform=platform,
        execution_mode=execution_mode,
        dry_run=dry_run,
    )
    spec = simulation_spec_template(project_name)

    if not dry_run:
        for key in ("data_dir", "wps_dir", "wrf_dir", "output_dir", "log_dir"):
            Path(state["paths"][key]).mkdir(parents=True, exist_ok=True)
        save_project(state, project_root / "project.json")
        with (project_root / "simulation_spec.json").open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(spec, handle, indent=2)
            handle.write("\n")

    return state, spec


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect or seed WRF project state")
    parser.add_argument("project_json", nargs="?")
    args = parser.parse_args()
    if args.project_json:
        print(json.dumps(load_project(args.project_json), indent=2))
