from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from project_state import (
        create_project_state,
        posix_path,
        record_error,
        register_artifact,
        save_project,
        simulation_spec_template,
        transition,
    )
except ImportError:  # pragma: no cover
    from .project_state import (
        create_project_state,
        posix_path,
        record_error,
        register_artifact,
        save_project,
        simulation_spec_template,
        transition,
    )


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path | str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def build_plan(
    project_name: str,
    project_root: Path,
    templates_dir: Path,
    config_path: Path,
    *,
    skip_env_check: bool,
) -> dict[str, Any]:
    seed_files = [
        {
            "source": posix_path(templates_dir / "namelist.wps.template"),
            "target": posix_path(project_root / "wps" / "namelist.wps"),
        },
        {
            "source": posix_path(templates_dir / "namelist.input.template"),
            "target": posix_path(project_root / "wrf" / "namelist.input"),
        },
        {
            "source": "generated",
            "target": posix_path(project_root / "project.json"),
        },
        {
            "source": "generated",
            "target": posix_path(project_root / "simulation_spec.json"),
        },
    ]

    return {
        "project_name": project_name,
        "project_root": posix_path(project_root),
        "directories": [
            posix_path(project_root / "data"),
            posix_path(project_root / "wps"),
            posix_path(project_root / "wrf"),
            posix_path(project_root / "output"),
            posix_path(project_root / "logs"),
        ],
        "seed_files": seed_files,
        "env_check": {
            "enabled": not skip_env_check,
            "command": [
                "bash",
                posix_path(Path(__file__).with_name("check_env.sh")),
                posix_path(config_path),
            ],
        },
    }


def run_env_check(config_path: Path) -> tuple[bool, str]:
    command = ["bash", str(Path(__file__).with_name("check_env.sh")), str(config_path)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    output_parts = [part for part in (completed.stdout.strip(), completed.stderr.strip()) if part]
    output = "\n".join(output_parts)
    return completed.returncode == 0, output


def ensure_project_target(project_root: Path) -> None:
    if not project_root.exists():
        return
    if not project_root.is_dir():
        raise FileExistsError(f"Target exists and is not a directory: {project_root}")
    if any(project_root.iterdir()):
        raise FileExistsError(
            f"Project directory already exists and is not empty: {project_root}"
        )


def copy_templates(templates_dir: Path, project_root: Path) -> dict[str, str]:
    targets = {
        "namelist_wps": project_root / "wps" / "namelist.wps",
        "namelist_input": project_root / "wrf" / "namelist.input",
    }
    shutil.copyfile(templates_dir / "namelist.wps.template", targets["namelist_wps"])
    shutil.copyfile(templates_dir / "namelist.input.template", targets["namelist_input"])
    return {key: posix_path(path) for key, path in targets.items()}


def write_init_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines).rstrip() + "\n"
    log_path.write_text(content, encoding="utf-8", newline="\n")


def initialize_project(
    project_name: str,
    *,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
    templates_dir: Path | str = "templates",
    dry_run: bool = False,
    skip_env_check: bool = False,
) -> dict[str, Any]:
    config_path = Path(config_path)
    templates_dir = Path(templates_dir)
    config = load_json(config_path)
    project_root = Path(runs_dir) / project_name
    plan = build_plan(
        project_name,
        project_root,
        templates_dir,
        config_path,
        skip_env_check=skip_env_check,
    )

    state = create_project_state(
        project_name,
        project_root,
        platform=config.get("platform", "wsl"),
        execution_mode=config.get("run_mode", "local"),
        dry_run=dry_run,
    )
    spec = simulation_spec_template(project_name)

    if dry_run:
        return {
            "dry_run": True,
            "project": state,
            "simulation_spec": spec,
            "plan": plan,
        }

    ensure_project_target(project_root)
    Path(state["paths"]["project_root"]).mkdir(parents=True, exist_ok=True)
    Path(state["paths"]["log_dir"]).mkdir(parents=True, exist_ok=True)
    log_path = Path(state["paths"]["log_dir"]) / "wrf-init.log"
    log_lines = [
        f"wrf-init project={project_name}",
        f"project_root={posix_path(project_root)}",
        f"config={posix_path(config_path)}",
        f"skip_env_check={skip_env_check}",
    ]

    env_checked = False
    if not skip_env_check:
        ok, output = run_env_check(config_path)
        env_checked = ok
        log_lines.append("[env-check]")
        log_lines.append(output if output else "(no output)")
        if not ok:
            record_error(
                state,
                "wrf-init",
                "ENV_CHECK_FAILED",
                "Environment validation failed",
                posix_path(log_path),
            )
            save_project(state, project_root / "project.json")
            write_init_log(log_path, log_lines)
            raise RuntimeError("Environment validation failed")

    for directory in plan["directories"]:
        Path(directory).mkdir(parents=True, exist_ok=True)

    seeded_templates = copy_templates(templates_dir, project_root)
    register_artifact(state, "namelist_wps", seeded_templates["namelist_wps"])
    register_artifact(state, "namelist_input", seeded_templates["namelist_input"])

    spec_path = project_root / "simulation_spec.json"
    dump_json(spec_path, spec)
    save_project(state, project_root / "project.json")

    target_status = "env_checked" if env_checked else "created"
    transition(state, target_status, current_step="wrf-init")
    save_project(state, project_root / "project.json")

    log_lines.extend(
        [
            "[seeded]",
            seeded_templates["namelist_wps"],
            seeded_templates["namelist_input"],
            posix_path(spec_path),
            posix_path(project_root / "project.json"),
        ]
    )
    write_init_log(log_path, log_lines)

    return {
        "dry_run": False,
        "project": state,
        "simulation_spec_path": posix_path(spec_path),
        "project_json": posix_path(project_root / "project.json"),
        "log_path": posix_path(log_path),
        "plan": plan,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize a WRF project scaffold")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--config", default="config/wrf_env.json")
    parser.add_argument("--templates-dir", default="templates")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-env-check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = initialize_project(
        args.project_name,
        runs_dir=args.runs_dir,
        config_path=args.config,
        templates_dir=args.templates_dir,
        dry_run=args.dry_run,
        skip_env_check=args.skip_env_check,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
