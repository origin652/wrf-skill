from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from hpc.base import (
        activate_block,
        hpc_runtime_config,
        module_load_block,
        render_shell_command,
        resolve_backend_name,
    )
except ImportError:  # pragma: no cover
    from .hpc.base import (
        activate_block,
        hpc_runtime_config,
        module_load_block,
        render_shell_command,
        resolve_backend_name,
    )


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def choose_template_path(config: dict[str, Any], templates_dir: Path | str) -> Path:
    backend = resolve_backend_name(config)
    file_name = "slurm_wrf.sh.template" if backend == "slurm" else "pbs_wrf.sh.template"
    return Path(templates_dir) / file_name


def recommend_layout(project_state: dict[str, Any], config: dict[str, Any]) -> dict[str, int]:
    del project_state
    hpc = config["hpc"]
    max_cores = int(hpc.get("cores_per_node") or 1)
    default_tasks = min(max_cores, int(config.get("local", {}).get("default_np") or 1))
    nodes = 1
    tasks_per_node = max(1, default_tasks)
    return {
        "nodes": nodes,
        "tasks_per_node": tasks_per_node,
        "total_tasks": nodes * tasks_per_node,
        "walltime_hours": max(1, int(hpc.get("default_walltime_hours") or 1)),
    }


def render_job_script(
    project_state: dict[str, Any],
    config: dict[str, Any],
    templates_dir: Path | str,
    output_path: Path | str,
) -> Path:
    template_path = choose_template_path(config, templates_dir)
    template = template_path.read_text(encoding="utf-8")
    layout = recommend_layout(project_state, config)
    hpc = config["hpc"]
    runtime = hpc_runtime_config(config)
    project_name = project_state["project_name"]
    remote_project_dir = PurePosixPath(hpc["remote_base_dir"]) / project_name
    remote_log_dir = remote_project_dir / "logs"
    launcher_cmd = str(runtime.get("launcher_cmd") or "mpirun")
    tasks_flag = str(runtime.get("tasks_flag") or "-np")
    runtime_mode = runtime["mode"]
    remote_run_dir = str(runtime.get("remote_run_dir") or ".").strip() or "."
    if runtime_mode == "project":
        real_exe = "./real.exe"
        wrf_exe = "./wrf.exe"
    elif runtime_mode == "remote_run_dir":
        real_exe = remote_run_dir.rstrip("/") + "/real.exe"
        wrf_exe = remote_run_dir.rstrip("/") + "/wrf.exe"
    else:
        real_exe = ""
        wrf_exe = ""
    runtime_context = {
        "project_name": project_name,
        "backend": resolve_backend_name(config),
        "partition": hpc["partition"],
        "account": hpc["account"],
        "launcher_cmd": launcher_cmd,
        "launch_cmd": launcher_cmd,
        "mpi_cmd": launcher_cmd,
        "tasks_flag": tasks_flag,
        "mpi_tasks_flag": tasks_flag,
        "nodes": layout["nodes"],
        "tasks_per_node": layout["tasks_per_node"],
        "total_tasks": layout["total_tasks"],
        "walltime_hours": layout["walltime_hours"],
        "remote_project_dir": str(remote_project_dir),
        "remote_log_dir": str(remote_log_dir),
        "remote_run_dir": remote_run_dir,
        "remote_wrf_run_dir": remote_run_dir,
        "real_exe": real_exe,
        "wrf_exe": wrf_exe,
    }
    real_cmd = render_shell_command(
        runtime.get("real_cmd") or "{launch_cmd} {tasks_flag} {total_tasks} {real_exe}",
        runtime_context,
    )
    wrf_cmd = render_shell_command(
        runtime.get("wrf_cmd") or "{launch_cmd} {tasks_flag} {total_tasks} {wrf_exe}",
        runtime_context,
    )
    rendered = template.format(
        project_name=project_name,
        partition=hpc["partition"],
        account=hpc["account"],
        nodes=layout["nodes"],
        tasks_per_node=layout["tasks_per_node"],
        total_tasks=layout["total_tasks"],
        walltime_hours=layout["walltime_hours"],
        remote_project_dir=str(remote_project_dir),
        remote_log_dir=str(remote_log_dir),
        module_load_block=module_load_block(config, runtime_context),
        activate_block=activate_block(config),
        real_cmd=real_cmd,
        wrf_cmd=wrf_cmd,
    )

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8", newline="\n")
    return target


def extract_job_id(output: str) -> str | None:
    match = re.search(r"(\d+(?:\.[A-Za-z0-9._-]+)?)", output)
    return match.group(1) if match else None


def submit_job(config: dict[str, Any], script_path: Path | str) -> tuple[str | None, str]:
    submit_cmd = config["hpc"]["submit_cmd"]
    completed = subprocess.run(
        [submit_cmd, str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    output = stdout if stdout else stderr
    return extract_job_id(output), output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render or submit an HPC WRF job script")
    parser.add_argument("--project-json", required=True)
    parser.add_argument("--config", default="config/wrf_env.json")
    parser.add_argument("--templates-dir", default="templates")
    parser.add_argument("--out", required=True)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_state = load_json(args.project_json)
    config = load_json(args.config)
    script_path = render_job_script(project_state, config, args.templates_dir, args.out)

    payload: dict[str, Any] = {"script": str(script_path)}
    if args.submit and not args.dry_run:
        job_id, output = submit_job(config, script_path)
        payload["job_id"] = job_id
        payload["submit_output"] = output
    else:
        payload["submit_command"] = f"{config['hpc']['submit_cmd']} {script_path}"

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
