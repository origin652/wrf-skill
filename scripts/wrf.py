from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

__version__ = "0.1.0"

COMMAND_HELP = {
    "init": "Initialize a project scaffold.",
    "config": "Render simulation_spec.json and namelists.",
    "import-namelists": "Import existing namelists into a structured simulation_spec.json.",
    "improve-namelists": "Improve existing namelists through the structured spec renderer.",
    "data": "Start the async forcing-data task.",
    "wps": "Start the async WPS task.",
    "run": "Start the async WRF task.",
    "status": "Read the current task state.",
    "logs": "Read the current task logs.",
    "monitor": "Stream live progress events for the current task (one per line; designed for the Monitor tool).",
    "cancel": "Cancel the current task.",
    "collect": "Collect terminal HPC outputs back into project state (lightweight by default for wrf-run).",
    "post": "Run post-processing for a project. HPC wrf-run already post-processes remotely by default.",
    "cleanup": "Clean up temporary and stale directories.",
}

TASK_STEP_COMMANDS = {
    "data": "wrf-data",
    "wps": "wrf-wps",
    "run": "wrf-run",
}

TASK_ACTION_COMMANDS = {
    "status": "status",
    "logs": "logs",
    "monitor": "monitor",
    "cancel": "cancel",
    "collect": "collect",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def scripts_dir() -> Path:
    return repo_root() / "scripts"


def build_forward_command(command: str, extra_args: Sequence[str]) -> list[str]:
    script_root = scripts_dir()
    if command == "init":
        return [sys.executable, str(script_root / "wrf_init.py"), *extra_args]
    if command == "config":
        return [sys.executable, str(script_root / "wrf_config.py"), *extra_args]
    if command == "import-namelists":
        return [sys.executable, str(script_root / "namelist_to_spec.py"), *extra_args]
    if command == "improve-namelists":
        return [sys.executable, str(script_root / "namelist_to_spec.py"), "improve", *extra_args]
    if command in TASK_STEP_COMMANDS:
        return [
            sys.executable,
            str(script_root / "wrf_task.py"),
            "start",
            "--step",
            TASK_STEP_COMMANDS[command],
            *extra_args,
        ]
    if command in TASK_ACTION_COMMANDS:
        return [
            sys.executable,
            str(script_root / "wrf_task.py"),
            TASK_ACTION_COMMANDS[command],
            *extra_args,
        ]
    if command == "post":
        return [sys.executable, str(script_root / "wrf_post.py"), *extra_args]
    if command == "cleanup":
        return [sys.executable, str(script_root / "cleanup.py"), *extra_args]
    raise ValueError(f"Unsupported command: {command}")


def print_help() -> None:
    print(f"Unified WRF workflow entry point (v{__version__})")
    print()
    print("Usage:")
    print("  python3 scripts/wrf.py <command> [args...]")
    print("  python3 scripts/wrf.py help [command]")
    print("  python3 scripts/wrf.py --version")
    print()
    print("Commands:")
    for name in (
        "init",
        "config",
        "import-namelists",
        "improve-namelists",
        "data",
        "wps",
        "run",
        "status",
        "logs",
        "monitor",
        "cancel",
        "collect",
        "post",
        "cleanup",
    ):
        print(f"  {name:<18}{COMMAND_HELP[name]}")
    print()
    print("Global options:")
    print("  --version    Show version and exit")
    print("  --help, -h   Show this help message")
    print()
    print("Compatibility:")
    print("  Existing scripts/wrf_init.py, scripts/wrf_config.py, scripts/wrf_task.py, and")
    print("  scripts/wrf_post.py remain supported. This wrapper is the preferred front door.")


def forward_command(command: str, extra_args: Sequence[str]) -> int:
    completed = subprocess.run(
        build_forward_command(command, extra_args),
        check=False,
    )
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Handle version flag
    if args and args[0] in {"-v", "--version"}:
        print(f"wrf-skill v{__version__}")
        return 0

    # Handle help
    if not args or args[0] in {"-h", "--help"}:
        print_help()
        return 0

    if args[0] == "help":
        if len(args) == 1:
            print_help()
            return 0
        command = args[1]
        if command not in COMMAND_HELP:
            print(f"Unknown command for help: {command}", file=sys.stderr)
            return 2
        return forward_command(command, ["--help"])

    command = args[0]
    if command not in COMMAND_HELP:
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Run `python3 scripts/wrf.py --help` for the supported command list.", file=sys.stderr)
        return 2
    return forward_command(command, args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
