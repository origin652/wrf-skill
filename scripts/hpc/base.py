from __future__ import annotations

import json
import math
import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


class CommandExecutionError(RuntimeError):
    pass


LOCAL_HOST_ALIASES = {"", "-", "local", "localhost", "127.0.0.1", "::1"}


def posix(path: Path | str) -> str:
    return Path(path).as_posix()


def resolve_backend_name(config_or_backend: dict[str, Any] | str) -> str:
    if isinstance(config_or_backend, str):
        return config_or_backend.strip().lower()
    hpc = config_or_backend.get("hpc", {})
    backend = hpc.get("backend") or hpc.get("scheduler") or "slurm"
    return str(backend).strip().lower()


def hpc_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.setdefault("hpc", {})


def is_local_host(host: str | None) -> bool:
    return str(host or "").strip().lower() in LOCAL_HOST_ALIASES


def normalize_access_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "": "",
        "local": "login",
        "login": "login",
        "login_node": "login",
        "login-node": "login",
        "loginnode": "login",
        "scheduler_local": "login",
        "scheduler-local": "login",
        "ssh": "ssh",
        "remote_ssh": "ssh",
        "remote-ssh": "ssh",
    }
    return aliases.get(normalized, normalized)


def resolve_access_mode(config: dict[str, Any]) -> str:
    hpc = hpc_config(config)
    raw_mode = hpc.get("access_mode") or hpc.get("scheduler_mode")
    normalized_mode = normalize_access_mode(raw_mode)
    if normalized_mode:
        return normalized_mode
    scheduler_host = hpc.get("scheduler_host") or hpc.get("remote_host")
    return "login" if is_local_host(str(scheduler_host or "")) else "ssh"


def resolve_scheduler_host(config: dict[str, Any]) -> str:
    hpc = hpc_config(config)
    return str(hpc.get("scheduler_host") or hpc.get("remote_host") or "").strip()


def resolve_transfer_host(config: dict[str, Any]) -> str:
    hpc = hpc_config(config)
    return str(hpc.get("remote_host") or hpc.get("sync_host") or hpc.get("scheduler_host") or "").strip()


def template_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "templates"


def _format_value(value: str, context: dict[str, Any]) -> str:
    if not context:
        return value
    rendered = value
    for key, replacement in context.items():
        rendered = rendered.replace("{" + str(key) + "}", str(replacement))
    return rendered


def render_command(command: list[str] | str, context: dict[str, Any] | None = None) -> list[str]:
    fmt = context or {}
    if isinstance(command, list):
        return [_format_value(str(part), fmt) for part in command]
    return shlex.split(_format_value(str(command), fmt))


def render_shell_command(command: list[str] | str, context: dict[str, Any] | None = None) -> str:
    if isinstance(command, list):
        return shlex.join(render_command(command, context))
    return _format_value(str(command), context or {})


def resolve_ssh_command(config: dict[str, Any]) -> list[str]:
    hpc = hpc_config(config)
    return render_command(hpc.get("scheduler_ssh_cmd") or hpc.get("ssh_command") or "ssh")


def run_command(
    command: list[str] | str,
    *,
    context: dict[str, Any] | None = None,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess[str]:
    rendered = render_command(command, context)
    completed = subprocess.run(
        rendered,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed


def run_scheduler_command(
    command: list[str] | str,
    *,
    config: dict[str, Any],
    context: dict[str, Any] | None = None,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess[str]:
    rendered = render_command(command, context)
    access_mode = resolve_access_mode(config)
    if access_mode == "login":
        return subprocess.run(
            rendered,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    if access_mode != "ssh":
        raise CommandExecutionError(f"Unsupported HPC access_mode: {access_mode}")

    scheduler_host = resolve_scheduler_host(config)
    if not scheduler_host:
        raise CommandExecutionError("HPC access_mode=ssh requires hpc.remote_host or hpc.scheduler_host")

    remote_command = shlex.join(rendered)
    ssh_command = resolve_ssh_command(config) + [
        scheduler_host,
        "sh",
        "-c",
        shlex.quote(remote_command),
    ]
    return subprocess.run(
        ssh_command,
        capture_output=True,
        text=True,
        check=False,
    )


def run_json_command(
    command: list[str] | str,
    *,
    context: dict[str, Any] | None = None,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    completed = run_command(command, context=context, cwd=cwd)
    output = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0:
        raise CommandExecutionError(output or f"Command failed with exit code {completed.returncode}")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise CommandExecutionError(f"Probe output is not valid JSON: {output}") from exc


def _run_builtin_probe(
    rendered_command: list[str],
    *,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    if not rendered_command:
        return None
    probe_name = str(rendered_command[0]).strip().lower()
    if not probe_name.startswith("builtin:"):
        return None
    try:
        from . import slurm_probes
    except ImportError:  # pragma: no cover
        import slurm_probes  # type: ignore
    try:
        return slurm_probes.execute_builtin_probe(
            rendered_command,
            runner=lambda probe_command: run_scheduler_command(probe_command, config=config),
        )
    except Exception as exc:  # pragma: no cover - normalized below for callers
        raise CommandExecutionError(str(exc)) from exc


def run_scheduler_json_command(
    command: list[str] | str,
    *,
    config: dict[str, Any],
    context: dict[str, Any] | None = None,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    rendered = render_command(command, context)
    builtin_payload = _run_builtin_probe(rendered, config=config)
    if builtin_payload is not None:
        return builtin_payload
    completed = run_scheduler_command(rendered, config=config, cwd=cwd)
    output = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0:
        raise CommandExecutionError(output or f"Command failed with exit code {completed.returncode}")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise CommandExecutionError(f"Probe output is not valid JSON: {output}") from exc


def hpc_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    hpc = hpc_config(config)
    runtime = deepcopy(hpc.get("runtime", {}))

    if "setup_commands" not in runtime and hpc.get("setup_commands") is not None:
        runtime["setup_commands"] = deepcopy(hpc.get("setup_commands"))
    if "modules" not in runtime and hpc.get("modules") is not None:
        runtime["modules"] = deepcopy(hpc.get("modules"))
    if "python_env" not in runtime:
        if "python_env" in hpc:
            runtime["python_env"] = hpc.get("python_env")
        elif "python_env" in config:
            runtime["python_env"] = config.get("python_env")
    if "launcher_cmd" not in runtime:
        runtime["launcher_cmd"] = (
            runtime.get("mpi_cmd")
            or hpc.get("launcher_cmd")
            or hpc.get("mpi_cmd")
            or config.get("local", {}).get("mpi_cmd")
            or "mpirun"
        )
    if "tasks_flag" not in runtime:
        runtime["tasks_flag"] = (
            runtime.get("mpi_tasks_flag")
            or hpc.get("tasks_flag")
            or hpc.get("mpi_tasks_flag")
            or "-np"
        )
    if "remote_run_dir" not in runtime and hpc.get("remote_wrf_run_dir") is not None:
        runtime["remote_run_dir"] = hpc.get("remote_wrf_run_dir")
    if "real_cmd" not in runtime and hpc.get("real_cmd") is not None:
        runtime["real_cmd"] = deepcopy(hpc.get("real_cmd"))
    if "wrf_cmd" not in runtime and hpc.get("wrf_cmd") is not None:
        runtime["wrf_cmd"] = deepcopy(hpc.get("wrf_cmd"))

    if "mode" not in runtime:
        if runtime.get("real_cmd") or runtime.get("wrf_cmd"):
            runtime["mode"] = "custom"
        elif runtime.get("remote_run_dir"):
            runtime["mode"] = "remote_run_dir"
        else:
            runtime["mode"] = "project"

    runtime["mode"] = str(runtime.get("mode") or "project").strip().lower()
    return runtime


def hpc_wps_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    hpc = hpc_config(config)
    runtime = deepcopy(hpc.get("wps_runtime", {}))

    if "setup_commands" not in runtime and hpc.get("setup_commands") is not None:
        runtime["setup_commands"] = deepcopy(hpc.get("setup_commands"))
    if "modules" not in runtime:
        if hpc.get("wps_modules") is not None:
            runtime["modules"] = deepcopy(hpc.get("wps_modules"))
        elif hpc.get("modules") is not None:
            runtime["modules"] = deepcopy(hpc.get("modules"))
    if "python_env" not in runtime:
        if "wps_python_env" in hpc:
            runtime["python_env"] = hpc.get("wps_python_env")
        elif "python_env" in hpc:
            runtime["python_env"] = hpc.get("python_env")
        elif "python_env" in config:
            runtime["python_env"] = config.get("python_env")
    if "launcher_cmd" not in runtime:
        runtime["launcher_cmd"] = (
            runtime.get("mpi_cmd")
            or hpc.get("wps_launcher_cmd")
            or hpc.get("launcher_cmd")
            or hpc.get("mpi_cmd")
            or config.get("local", {}).get("mpi_cmd")
            or "mpirun"
        )
    if "tasks_flag" not in runtime:
        runtime["tasks_flag"] = (
            runtime.get("mpi_tasks_flag")
            or hpc.get("wps_tasks_flag")
            or hpc.get("tasks_flag")
            or hpc.get("mpi_tasks_flag")
            or "-np"
        )
    if "remote_wps_dir" not in runtime and hpc.get("remote_wps_dir") is not None:
        runtime["remote_wps_dir"] = hpc.get("remote_wps_dir")
    if "geogrid_cmd" not in runtime and hpc.get("geogrid_cmd") is not None:
        runtime["geogrid_cmd"] = deepcopy(hpc.get("geogrid_cmd"))
    if "link_grib_cmd" not in runtime and hpc.get("link_grib_cmd") is not None:
        runtime["link_grib_cmd"] = deepcopy(hpc.get("link_grib_cmd"))
    if "ungrib_cmd" not in runtime and hpc.get("ungrib_cmd") is not None:
        runtime["ungrib_cmd"] = deepcopy(hpc.get("ungrib_cmd"))
    if "metgrid_cmd" not in runtime and hpc.get("metgrid_cmd") is not None:
        runtime["metgrid_cmd"] = deepcopy(hpc.get("metgrid_cmd"))

    if "mode" not in runtime:
        if any(runtime.get(key) for key in ("geogrid_cmd", "link_grib_cmd", "ungrib_cmd", "metgrid_cmd")):
            runtime["mode"] = "custom"
        elif runtime.get("remote_wps_dir"):
            runtime["mode"] = "remote_wps_dir"
        else:
            runtime["mode"] = "project"

    runtime["mode"] = str(runtime.get("mode") or "project").strip().lower()
    return runtime


def resolve_named_runtime_config(config: dict[str, Any], runtime_key: str) -> dict[str, Any]:
    if runtime_key == "wps_runtime":
        return hpc_wps_runtime_config(config)
    return hpc_runtime_config(config)


def _iter_shell_lines(value: Any, context: dict[str, Any] | None = None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if item is None:
                continue
            lines.append(render_shell_command(item, context))
        return lines
    return [render_shell_command(value, context)]


def module_load_block(
    config: dict[str, Any],
    context: dict[str, Any] | None = None,
    *,
    runtime_key: str = "runtime",
) -> str:
    runtime = resolve_named_runtime_config(config, runtime_key)
    lines = _iter_shell_lines(runtime.get("setup_commands"), context)
    modules = runtime.get("modules") or []
    lines.extend(f"module load {_format_value(str(module), context or {})}" for module in modules)
    if not lines:
        return "# No runtime setup configured"
    return "\n".join(lines)


def activate_block(config: dict[str, Any], *, runtime_key: str = "runtime") -> str:
    python_env = resolve_named_runtime_config(config, runtime_key).get("python_env")
    if not python_env:
        return "# No Python environment activation configured"
    return f"source ~/.bashrc\nconda activate {python_env}"


def validate_runtime_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    runtime = hpc_runtime_config(config)
    mode = runtime["mode"]
    missing_fields: list[str] = []
    if mode not in {"project", "remote_run_dir", "custom"}:
        missing_fields.append("hpc.runtime.mode")
    if mode == "remote_run_dir" and not str(runtime.get("remote_run_dir") or "").strip():
        missing_fields.append("hpc.runtime.remote_run_dir")
    if mode == "custom":
        if not runtime.get("real_cmd"):
            missing_fields.append("hpc.runtime.real_cmd")
        if not runtime.get("wrf_cmd"):
            missing_fields.append("hpc.runtime.wrf_cmd")
    return runtime, missing_fields


def validate_wps_runtime_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    runtime = hpc_wps_runtime_config(config)
    mode = runtime["mode"]
    missing_fields: list[str] = []
    if mode not in {"project", "remote_wps_dir", "custom"}:
        missing_fields.append("hpc.wps_runtime.mode")
    if mode == "remote_wps_dir" and not str(runtime.get("remote_wps_dir") or "").strip():
        missing_fields.append("hpc.wps_runtime.remote_wps_dir")
    if mode == "custom":
        required = {
            "geogrid_cmd": "hpc.wps_runtime.geogrid_cmd",
            "link_grib_cmd": "hpc.wps_runtime.link_grib_cmd",
            "ungrib_cmd": "hpc.wps_runtime.ungrib_cmd",
            "metgrid_cmd": "hpc.wps_runtime.metgrid_cmd",
        }
        for key, field_name in required.items():
            if not runtime.get(key):
                missing_fields.append(field_name)
    return runtime, missing_fields


def merge_hpc_limits(config: dict[str, Any]) -> dict[str, Any]:
    hpc = hpc_config(config)
    limits = deepcopy(hpc.get("limits", {}))
    if limits.get("max_nodes") is None and hpc.get("max_nodes") is not None:
        limits["max_nodes"] = int(hpc["max_nodes"])
    if limits.get("max_total_tasks") is None:
        if hpc.get("max_total_tasks") is not None:
            limits["max_total_tasks"] = int(hpc["max_total_tasks"])
        elif limits.get("max_nodes") is not None and hpc.get("cores_per_node") is not None:
            limits["max_total_tasks"] = int(limits["max_nodes"]) * int(hpc["cores_per_node"])
    limits.setdefault("max_forecast_hours", None)
    limits.setdefault("max_domains", None)
    limits.setdefault("max_total_grid_points", None)
    limits.setdefault("max_walltime_hours", None)
    return limits


def choose_limit(*values: Any) -> int | None:
    valid = [int(value) for value in values if value is not None]
    if not valid:
        return None
    return min(valid)


def _extract_partition_payload(payload: dict[str, Any], partition: str) -> dict[str, Any]:
    partitions = payload.get("partitions")
    if isinstance(partitions, dict) and partition in partitions:
        item = partitions[partition]
        if isinstance(item, dict):
            return item
    return payload if isinstance(payload, dict) else {}


def _extract_account_payload(payload: dict[str, Any], account: str) -> dict[str, Any]:
    accounts = payload.get("accounts")
    if isinstance(accounts, dict) and account in accounts:
        item = accounts[account]
        if isinstance(item, dict):
            return item
    return payload if isinstance(payload, dict) else {}


def _command_with_target(
    command: list[str] | str,
    target_value: str,
    *,
    placeholder: str,
    extra_context: dict[str, Any] | None = None,
) -> list[str]:
    context = {placeholder: target_value, **(extra_context or {})}
    if isinstance(command, list):
        rendered = [_format_value(str(part), context) for part in command]
        placeholders = tuple("{" + key + "}" for key in context)
        if any(any(token in str(part) for token in placeholders) for part in command):
            return rendered
        return rendered + [target_value]
    if any("{" + key + "}" in str(command) for key in context):
        return render_command(command, context)
    return render_command(command) + [target_value]


class HpcSchedulerAdapter(ABC):
    backend_name = ""
    template_name = ""
    wps_template_name = ""

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        hpc = hpc_config(config)
        limits = merge_hpc_limits(config)
        probes = hpc.get("probes", {})
        access_mode = resolve_access_mode(config)
        runtime, runtime_missing = validate_runtime_config(config)
        missing_fields = [
            key
            for key in ("remote_base_dir", "partition", "account", "submit_cmd", "status_cmd", "cancel_cmd")
            if not hpc.get(key)
        ]
        missing_fields.extend(runtime_missing)
        if access_mode not in {"login", "ssh"}:
            missing_fields.append("access_mode")
        if access_mode == "ssh" and not resolve_scheduler_host(config):
            missing_fields.append("remote_host")
        summary = {
            "backend": resolve_backend_name(config),
            "enabled": bool(hpc.get("enabled", False)),
            "access_mode": access_mode,
            "partition": hpc.get("partition"),
            "account": hpc.get("account"),
            "remote_host": hpc.get("remote_host"),
            "scheduler_host": resolve_scheduler_host(config) or None,
            "transfer_host": resolve_transfer_host(config) or None,
            "remote_base_dir": hpc.get("remote_base_dir"),
            "runtime_mode": runtime["mode"],
            "wps_runtime_mode": hpc_wps_runtime_config(config)["mode"],
            "limits": limits,
            "supports_probes": bool(probes.get("cluster_state_cmd")) and bool(probes.get("account_state_cmd")),
            "missing_fields": missing_fields,
        }
        summary["valid"] = summary["enabled"] and access_mode in {"login", "ssh"} and not missing_fields
        return summary

    def template_name_for_step(self, step: str) -> str:
        if step == "wrf-wps" and self.wps_template_name:
            return self.wps_template_name
        return self.template_name

    def recommend_layout(self, request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        hpc = hpc_config(config)
        limits = merge_hpc_limits(config)
        cores_per_node = max(1, int(hpc.get("cores_per_node") or 1))
        max_nodes = max(1, int(limits.get("max_nodes") or hpc.get("max_nodes") or 1))
        max_total_tasks = max(
            1,
            int(
                limits.get("max_total_tasks")
                or hpc.get("max_total_tasks")
                or (max_nodes * cores_per_node)
            ),
        )
        default_tasks = max(1, int(hpc.get("default_tasks") or config.get("local", {}).get("default_np") or 1))
        grid_points = max(1, int(request.get("total_grid_points") or 1))
        heuristic_tasks = max(default_tasks, math.ceil(grid_points / 80000))
        total_tasks = min(max_total_tasks, heuristic_tasks)
        nodes = min(max_nodes, max(1, math.ceil(total_tasks / cores_per_node)))
        tasks_per_node = max(1, math.ceil(total_tasks / nodes))
        total_tasks = min(max_total_tasks, nodes * tasks_per_node)
        nodes = max(1, math.ceil(total_tasks / cores_per_node))
        tasks_per_node = max(1, math.ceil(total_tasks / nodes))
        return {
            "nodes": nodes,
            "tasks_per_node": tasks_per_node,
            "total_tasks": total_tasks,
            "walltime_hours": max(1, int(request.get("estimated_walltime_hours") or 1)),
        }

    def probe_resources(self, request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        summary = self.validate_config(config)
        if not summary["valid"]:
            return {
                "decision": "unverified",
                "reason_codes": ["HPC_CONFIG_INVALID"],
                "live_cluster": {"config_summary": summary},
            }

        probes = hpc_config(config).get("probes", {})
        partition = str(hpc_config(config).get("partition") or "")
        account = str(hpc_config(config).get("account") or "")
        probe_context = {"partition": partition, "account": account}
        try:
            cluster_raw = run_scheduler_json_command(probes["cluster_state_cmd"], config=config, context=probe_context)
            account_raw = run_scheduler_json_command(probes["account_state_cmd"], config=config, context=probe_context)
        except (KeyError, CommandExecutionError) as exc:
            return {
                "decision": "unverified",
                "reason_codes": ["PROBE_UNAVAILABLE"],
                "live_cluster": {"error": str(exc), "config_summary": summary},
            }

        cluster_partition = _extract_partition_payload(cluster_raw, partition)
        account_payload = _extract_account_payload(account_raw, account)
        account_partition = _extract_partition_payload(account_payload, partition)
        requested_layout = request["requested_layout"]
        requested_nodes = int(requested_layout["nodes"])
        requested_tasks = int(requested_layout["total_tasks"])
        live_limit_nodes = choose_limit(
            cluster_partition.get("max_nodes"),
            account_partition.get("max_nodes"),
            summary["limits"].get("max_nodes"),
        )
        live_limit_tasks = choose_limit(
            cluster_partition.get("max_total_tasks"),
            account_partition.get("max_total_tasks"),
            summary["limits"].get("max_total_tasks"),
        )

        reasons: list[str] = []
        allowed = (
            cluster_partition.get("available", True)
            and account_payload.get("allowed", True)
            and account_partition.get("allowed", True)
        )
        if not allowed:
            reasons.append("PARTITION_OR_ACCOUNT_DENIED")
        if live_limit_nodes is not None and requested_nodes > live_limit_nodes:
            reasons.append("LIVE_MAX_NODES_EXCEEDED")
        if live_limit_tasks is not None and requested_tasks > live_limit_tasks:
            reasons.append("LIVE_MAX_TASKS_EXCEEDED")
        if reasons:
            return {
                "decision": "rejected",
                "reason_codes": reasons,
                "live_cluster": {
                    "cluster": cluster_raw,
                    "account": account_raw,
                    "live_limits": {
                        "max_nodes": live_limit_nodes,
                        "max_total_tasks": live_limit_tasks,
                    },
                },
            }

        free_nodes = cluster_partition.get("free_nodes")
        free_tasks = cluster_partition.get("free_tasks")
        queue_allowed = bool(cluster_partition.get("queue_allowed", True)) and bool(
            account_partition.get("queue_allowed", True)
        )
        enough_nodes = free_nodes is None or requested_nodes <= int(free_nodes)
        enough_tasks = free_tasks is None or requested_tasks <= int(free_tasks)
        if enough_nodes and enough_tasks:
            decision = "admissible_now"
            decision_reasons: list[str] = []
        elif queue_allowed:
            decision = "admissible_with_queue"
            decision_reasons = ["QUEUE_EXPECTED"]
        else:
            decision = "rejected"
            decision_reasons = ["INSUFFICIENT_LIVE_CAPACITY"]

        return {
            "decision": decision,
            "reason_codes": decision_reasons,
            "live_cluster": {
                "cluster": cluster_raw,
                "account": account_raw,
                "live_limits": {
                    "max_nodes": live_limit_nodes,
                    "max_total_tasks": live_limit_tasks,
                    "free_nodes": free_nodes,
                    "free_tasks": free_tasks,
                    "queue_allowed": queue_allowed,
                },
            },
        }

    def render_job(self, project_state: dict[str, Any], plan: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        hpc = hpc_config(config)
        step = str(plan.get("step") or project_state.get("current_step") or "wrf-run").strip().lower()
        project_root = Path(project_state["paths"]["project_root"])
        script_dir = project_root / "hpc"
        script_dir.mkdir(parents=True, exist_ok=True)
        if step == "wrf-run":
            script_name = f"{project_state['project_name']}.{self.backend_name}.job.sh"
        else:
            script_name = f"{project_state['project_name']}.{step}.{self.backend_name}.job.sh"
        script_path = script_dir / script_name
        remote_project_dir = PurePosixPath(hpc["remote_base_dir"]) / project_state["project_name"]
        remote_log_dir = remote_project_dir / "logs"
        remote_script_path = remote_project_dir / "hpc" / script_path.name
        template = (template_dir() / self.template_name_for_step(step)).read_text(encoding="utf-8")

        if step == "wrf-wps":
            runtime, runtime_missing = validate_wps_runtime_config(config)
            if runtime_missing:
                raise RuntimeError(f"Invalid HPC WPS runtime configuration: {', '.join(runtime_missing)}")
            launcher_cmd = str(runtime.get("launcher_cmd") or "mpirun")
            tasks_flag = str(runtime.get("tasks_flag") or "-np")
            runtime_mode = runtime["mode"]
            remote_wps_dir = str(runtime.get("remote_wps_dir") or ".").strip() or "."
            if runtime_mode == "project":
                project_wps_dir = Path(project_state["paths"]["wps_dir"])
                missing_binaries = [
                    name
                    for name in ("geogrid.exe", "link_grib.csh", "ungrib.exe", "metgrid.exe")
                    if not (project_wps_dir / name).is_file()
                ]
                if missing_binaries:
                    raise RuntimeError(
                        "HPC WPS project runtime requires project-local WPS executables: "
                        + ", ".join(missing_binaries)
                    )
                geogrid_exe = "./geogrid.exe"
                link_grib_exe = "./link_grib.csh"
                ungrib_exe = "./ungrib.exe"
                metgrid_exe = "./metgrid.exe"
            elif runtime_mode == "remote_wps_dir":
                remote_wps_dir = remote_wps_dir.rstrip("/")
                geogrid_exe = remote_wps_dir + "/geogrid.exe"
                link_grib_exe = remote_wps_dir + "/link_grib.csh"
                ungrib_exe = remote_wps_dir + "/ungrib.exe"
                metgrid_exe = remote_wps_dir + "/metgrid.exe"
            else:
                geogrid_exe = ""
                link_grib_exe = ""
                ungrib_exe = ""
                metgrid_exe = ""

            remote_forcing_files: list[str] = []
            project_root_resolved = project_root.resolve()
            for raw_path in plan.get("forcing_files", []):
                local_path = Path(str(raw_path)).resolve()
                try:
                    relative = local_path.relative_to(project_root_resolved)
                    remote_path = remote_project_dir / PurePosixPath(relative.as_posix())
                except ValueError:
                    remote_path = remote_project_dir / "data" / local_path.name
                remote_forcing_files.append(str(remote_path))

            runtime_context = {
                "project_name": project_state["project_name"],
                "backend": self.backend_name,
                "partition": hpc["partition"],
                "account": hpc["account"],
                "launcher_cmd": launcher_cmd,
                "launch_cmd": launcher_cmd,
                "mpi_cmd": launcher_cmd,
                "tasks_flag": tasks_flag,
                "mpi_tasks_flag": tasks_flag,
                "nodes": plan["nodes"],
                "tasks_per_node": plan["tasks_per_node"],
                "total_tasks": plan["total_tasks"],
                "walltime_hours": plan["walltime_hours"],
                "remote_project_dir": str(remote_project_dir),
                "remote_log_dir": str(remote_log_dir),
                "remote_work_dir": str(remote_project_dir / "wps"),
                "remote_wps_dir": remote_wps_dir,
                "geogrid_exe": geogrid_exe,
                "link_grib_exe": link_grib_exe,
                "ungrib_exe": ungrib_exe,
                "metgrid_exe": metgrid_exe,
                "forcing_args": shlex.join(remote_forcing_files),
                "forcing_inputs": shlex.join(remote_forcing_files),
            }
            geogrid_cmd = render_shell_command(
                runtime.get("geogrid_cmd") or "{geogrid_exe}",
                runtime_context,
            ).strip()
            link_grib_cmd = render_shell_command(
                runtime.get("link_grib_cmd") or "{link_grib_exe} {forcing_args}",
                runtime_context,
            ).strip()
            ungrib_cmd = render_shell_command(
                runtime.get("ungrib_cmd") or "{ungrib_exe}",
                runtime_context,
            ).strip()
            metgrid_cmd = render_shell_command(
                runtime.get("metgrid_cmd") or "{metgrid_exe}",
                runtime_context,
            ).strip()
            rendered = template.format(
                project_name=project_state["project_name"],
                partition=hpc["partition"],
                account=hpc["account"],
                nodes=plan["nodes"],
                tasks_per_node=plan["tasks_per_node"],
                total_tasks=plan["total_tasks"],
                walltime_hours=plan["walltime_hours"],
                remote_project_dir=str(remote_project_dir),
                remote_log_dir=str(remote_log_dir),
                remote_work_dir=str(remote_project_dir / "wps"),
                module_load_block=module_load_block(config, runtime_context, runtime_key="wps_runtime"),
                activate_block=activate_block(config, runtime_key="wps_runtime"),
                geogrid_cmd=geogrid_cmd,
                link_grib_cmd=link_grib_cmd,
                ungrib_cmd=ungrib_cmd,
                metgrid_cmd=metgrid_cmd,
            )
        else:
            runtime, runtime_missing = validate_runtime_config(config)
            if runtime_missing:
                raise RuntimeError(f"Invalid HPC runtime configuration: {', '.join(runtime_missing)}")
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
                "project_name": project_state["project_name"],
                "backend": self.backend_name,
                "partition": hpc["partition"],
                "account": hpc["account"],
                "launcher_cmd": launcher_cmd,
                "launch_cmd": launcher_cmd,
                "mpi_cmd": launcher_cmd,
                "tasks_flag": tasks_flag,
                "mpi_tasks_flag": tasks_flag,
                "nodes": plan["nodes"],
                "tasks_per_node": plan["tasks_per_node"],
                "total_tasks": plan["total_tasks"],
                "walltime_hours": plan["walltime_hours"],
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
                project_name=project_state["project_name"],
                partition=hpc["partition"],
                account=hpc["account"],
                nodes=plan["nodes"],
                tasks_per_node=plan["tasks_per_node"],
                total_tasks=plan["total_tasks"],
                walltime_hours=plan["walltime_hours"],
                remote_project_dir=str(remote_project_dir),
                remote_log_dir=str(remote_log_dir),
                module_load_block=module_load_block(config, runtime_context),
                activate_block=activate_block(config),
                real_cmd=real_cmd,
                wrf_cmd=wrf_cmd,
            )
        script_path.write_text(rendered, encoding="utf-8", newline="\n")
        return {
            "backend": self.backend_name,
            "step": step,
            "runtime_mode": runtime_mode,
            "script_path": posix(script_path),
            "remote_script_path": str(remote_script_path),
            "remote_project_dir": str(remote_project_dir),
            "remote_log_dir": str(remote_log_dir),
            "plan": deepcopy(plan),
        }

    def submit(self, rendered_job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        submit_script_path = str(rendered_job.get("remote_script_path") or rendered_job["script_path"])
        command = _command_with_target(
            hpc_config(config)["submit_cmd"],
            submit_script_path,
            placeholder="remote_script_path",
            extra_context={
                "script_path": str(rendered_job["script_path"]),
                "remote_script_path": submit_script_path,
            },
        )
        completed = run_scheduler_command(command, config=config)
        output = completed.stdout.strip() or completed.stderr.strip()
        job_id = self.parse_submit_output(output)
        if completed.returncode != 0 or job_id is None:
            raise RuntimeError(output or "HPC job submission failed")
        return {
            "job_id": job_id,
            "submit_output": output,
            "state": "queued",
        }

    def query(self, job_handle: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        command = _command_with_target(
            hpc_config(config)["status_cmd"],
            str(job_handle["job_id"]),
            placeholder="job_id",
        )
        completed = run_scheduler_command(command, config=config)
        output = completed.stdout.strip() or completed.stderr.strip()
        if completed.returncode != 0:
            raise RuntimeError(output or "HPC job query failed")
        return self.parse_query_output(output, str(job_handle["job_id"]))

    def cancel(self, job_handle: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        command = _command_with_target(
            hpc_config(config)["cancel_cmd"],
            str(job_handle["job_id"]),
            placeholder="job_id",
        )
        completed = run_scheduler_command(command, config=config)
        output = completed.stdout.strip() or completed.stderr.strip()
        return {
            "canceled": completed.returncode == 0,
            "job_id": str(job_handle["job_id"]),
            "output": output,
        }

    @abstractmethod
    def parse_submit_output(self, output: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def parse_query_output(self, output: str, job_id: str) -> dict[str, Any]:
        raise NotImplementedError


def map_slurm_state(token: str) -> str | None:
    normalized = token.strip().upper()
    mapping = {
        "PD": "queued",
        "PENDING": "queued",
        "CF": "queued",
        "CONFIGURING": "queued",
        "R": "running",
        "RUNNING": "running",
        "CG": "running",
        "COMPLETING": "running",
        "CD": "completed",
        "COMPLETED": "completed",
        "CA": "canceled",
        "CANCELLED": "canceled",
        "CANCELED": "canceled",
        "BF": "failed",
        "BOOT_FAIL": "failed",
        "F": "failed",
        "FAILED": "failed",
        "NF": "failed",
        "NODE_FAIL": "failed",
        "OOM": "failed",
        "OUT_OF_MEMORY": "failed",
        "PR": "failed",
        "PREEMPTED": "failed",
        "TO": "failed",
        "TIMEOUT": "failed",
    }
    return mapping.get(normalized)


def map_pbs_state(token: str, *, exit_status: int | None = None) -> str | None:
    normalized = token.strip().upper()
    if normalized in {"Q", "H", "W", "B"}:
        return "queued"
    if normalized in {"R", "E"}:
        return "running"
    if normalized in {"X"}:
        return "canceled"
    if normalized in {"C", "F"}:
        if exit_status not in (None, 0):
            return "failed"
        return "completed"
    return None


def extract_json_if_possible(output: str) -> dict[str, Any] | None:
    stripped = output.strip()
    if not stripped.startswith("{"):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1) if match else None
