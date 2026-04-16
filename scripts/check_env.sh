#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/check_env.sh [--json] [config_path]

Validate the local WRF/WPS runtime configuration.
USAGE
}

JSON_MODE=0
CONFIG_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)
      JSON_MODE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$CONFIG_PATH" ]]; then
        echo "Unexpected extra argument: $1" >&2
        usage >&2
        exit 2
      fi
      CONFIG_PATH="$1"
      shift
      ;;
  esac
done

if [[ -z "$CONFIG_PATH" ]]; then
  CONFIG_PATH="config/wrf_env.json"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  if [[ "$JSON_MODE" -eq 1 ]]; then
    printf '{"valid":false,"errors":["Missing command: python3"]}\n'
  else
    echo "Missing command: python3" >&2
  fi
  exit 1
fi

exec "$PYTHON_CMD" - "$CONFIG_PATH" "$SCRIPT_DIR" "$PYTHON_CMD" "$JSON_MODE" <<'PY'
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

config_path = Path(sys.argv[1])
script_dir = Path(sys.argv[2]).resolve()
python_cmd = sys.argv[3]
json_mode = sys.argv[4] == "1"
sys.path.insert(0, str(script_dir))

from local_runtime import (  # pylint: disable=import-error
    LocalRuntimeConfigError,
    local_runtime_config,
    local_wps_runtime_config,
    required_local_external_commands,
    validate_local_runtime_sections,
)


def detect_host_kind() -> tuple[str, str]:
    system = platform.system()
    if system != "Linux":
        return "unsupported", system
    proc_version = Path("/proc/version")
    try:
        version_text = proc_version.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        version_text = ""
    if "microsoft" in version_text.lower():
        return "wsl", system
    return "linux", system


def maybe_path(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def build_path_check(path_value: str) -> dict[str, Any]:
    return {
        "path": path_value,
        "exists": bool(path_value) and Path(path_value).exists(),
    }


def command_check(name: str) -> dict[str, Any]:
    resolved = shutil.which(name)
    return {
        "name": name,
        "found": resolved is not None,
        "resolved_path": resolved,
    }


def resolve_exec(name: str, search_dirs: list[Path]) -> str | None:
    candidates = (name, f"{name}.exe") if not name.endswith(".csh") else (name,)
    seen: set[str] = set()
    for directory in search_dirs:
        directory = Path(directory)
        if not str(directory).strip():
            continue
        key = directory.as_posix()
        if key in seen:
            continue
        seen.add(key)
        for candidate_name in candidates:
            candidate = directory / candidate_name
            if candidate.exists() and os.access(candidate, os.X_OK):
                return candidate.as_posix()
    return None


def has_geog_index(path_value: str) -> bool:
    if not path_value:
        return False
    root = Path(path_value)
    if not root.is_dir():
        return False
    for child in root.rglob("index"):
        try:
            depth = len(child.relative_to(root).parts)
        except ValueError:
            continue
        if depth <= 2 and child.is_file():
            return True
    return False


payload: dict[str, Any] = {
    "valid": False,
    "config_path": config_path.as_posix(),
    "python_cmd": python_cmd,
    "warnings": [],
    "errors": [],
}

if not config_path.exists():
    payload["errors"].append(f"Missing config file: {config_path}")
    if json_mode:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Missing config file: {config_path}", file=sys.stderr)
    raise SystemExit(1)

try:
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
except json.JSONDecodeError as exc:
    payload["errors"].append(f"Invalid JSON in config: {exc}")
    if json_mode:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Invalid JSON in config: {exc}", file=sys.stderr)
    raise SystemExit(1)

platform_name = maybe_path(config.get("platform"))
payload["platform"] = platform_name
payload["python_env"] = maybe_path(config.get("python_env"))

host_kind, host_os = detect_host_kind()
payload["host_kind"] = host_kind
payload["host_os"] = host_os
if host_kind == "unsupported":
    payload["errors"].append("This scaffold only supports Linux/WSL execution.")

local_runtime_mode = "project"
local_wps_runtime_mode = "project"
required_commands: list[str] = []
try:
    validate_local_runtime_sections(config)
    local_runtime_mode = local_runtime_config(config)["mode"]
    local_wps_runtime_mode = local_wps_runtime_config(config)["mode"]
    required_commands = required_local_external_commands(config)
except LocalRuntimeConfigError as exc:
    payload["errors"].append(f"Invalid local runtime configuration: {exc}")

payload["local_runtime_mode"] = local_runtime_mode
payload["local_wps_runtime_mode"] = local_wps_runtime_mode
payload["required_external_commands"] = required_commands

paths = {
    "wrf_dir": maybe_path(config.get("wrf_dir")),
    "wps_dir": maybe_path(config.get("wps_dir")),
    "geog_data_path": maybe_path(config.get("geog_data_path")),
    "wrf_run_dir": maybe_path(config.get("wrf_run_dir")),
    "wps_bin_dir": maybe_path(config.get("wps_bin_dir")),
}
payload["paths"] = {name: build_path_check(value) for name, value in paths.items()}

required_command_checks = [command_check(python_cmd)]
required_command_checks.extend(command_check(name) for name in required_commands)
payload["required_commands"] = required_command_checks
for command_payload in required_command_checks:
    if not command_payload["found"]:
        payload["errors"].append(f"Missing command: {command_payload['name']}")

for key in ("wrf_dir", "wps_dir", "geog_data_path"):
    item = payload["paths"][key]
    if not item["exists"]:
        payload["errors"].append(f"Missing path: {item['path']}")

if platform_name and host_kind in {"linux", "wsl"} and platform_name != host_kind:
    payload["warnings"].append(
        f"Configured platform '{platform_name}' does not match detected host '{host_kind}'."
    )

wps_search_dirs: list[Path] = []
if paths["wps_bin_dir"]:
    wps_search_dirs.append(Path(paths["wps_bin_dir"]))
if paths["wps_dir"]:
    wps_search_dirs.append(Path(paths["wps_dir"]))
    wps_search_dirs.append(Path(paths["wps_dir"]) / "bin")

wrf_search_dirs: list[Path] = []
if paths["wrf_run_dir"]:
    wrf_search_dirs.append(Path(paths["wrf_run_dir"]))
if paths["wrf_dir"]:
    wrf_root = Path(paths["wrf_dir"])
    wrf_search_dirs.extend([wrf_root / "run", wrf_root / "bin", wrf_root / "main"])

payload["executables"] = {"wps": {}, "wrf": {}}
for executable_name in ("geogrid", "ungrib", "metgrid", "link_grib.csh"):
    resolved = resolve_exec(executable_name, wps_search_dirs)
    item = {
        "name": executable_name,
        "found": resolved is not None,
        "resolved_path": resolved,
    }
    payload["executables"]["wps"][executable_name] = item
    if resolved is None:
        payload["errors"].append(
            f"Missing WPS executable: {executable_name} under {paths['wps_dir']} or {paths['wps_bin_dir']}"
        )

for executable_name in ("real", "wrf"):
    resolved = resolve_exec(executable_name, wrf_search_dirs)
    item = {
        "name": executable_name,
        "found": resolved is not None,
        "resolved_path": resolved,
    }
    payload["executables"]["wrf"][executable_name] = item
    if resolved is None:
        payload["errors"].append(
            "Missing WRF executable: "
            f"{executable_name} under {paths['wrf_run_dir']}, {paths['wrf_dir']}/run, "
            f"{paths['wrf_dir']}/bin, or {paths['wrf_dir']}/main"
        )

wps_tables = config.get("wps_tables", {}) if isinstance(config.get("wps_tables"), dict) else {}
payload["wps_support_files"] = {}
for table_name, table_path in (
    ("GEOGRID.TBL", maybe_path(wps_tables.get("geogrid"))),
    ("METGRID.TBL", maybe_path(wps_tables.get("metgrid"))),
    ("Vtable", maybe_path(wps_tables.get("vtable"))),
):
    entry = {
        "path": table_path,
        "exists": bool(table_path) and Path(table_path).exists(),
    }
    payload["wps_support_files"][table_name] = entry
    if table_path and not entry["exists"]:
        payload["errors"].append(f"Missing WPS support file {table_name}: {table_path}")

if paths["geog_data_path"] and payload["paths"]["geog_data_path"]["exists"] and not has_geog_index(paths["geog_data_path"]):
    payload["warnings"].append(
        "geog_data_path exists but no WPS geography index files were found: "
        f"{paths['geog_data_path']}"
    )

payload["valid"] = not payload["errors"]

if json_mode:
    print(json.dumps(payload, indent=2))
    raise SystemExit(0 if payload["valid"] else 1)

print(f"Detected host: {host_kind}")
print(f"Configured platform: {platform_name}")
print(f"Local WRF runtime mode: {local_runtime_mode}")
print(f"Local WPS runtime mode: {local_wps_runtime_mode}")

if required_commands:
    for command_payload in required_command_checks[1:]:
        if command_payload["found"]:
            print(
                f"Resolved external command {command_payload['name']} -> {command_payload['resolved_path']}"
            )
else:
    print("No extra external launcher commands are required by the current local runtime config.")

for executable_name, item in payload["executables"]["wps"].items():
    if item["found"]:
        print(f"Resolved WPS executable {executable_name} -> {item['resolved_path']}")

for executable_name, item in payload["executables"]["wrf"].items():
    if item["found"]:
        print(f"Resolved WRF executable {executable_name} -> {item['resolved_path']}")

for warning in payload["warnings"]:
    print(f"Warning: {warning}", file=sys.stderr)
for error in payload["errors"]:
    print(error, file=sys.stderr)

print(f"Configured Python environment: {payload['python_env']}")
if payload["valid"]:
    print("Environment check passed.")

raise SystemExit(0 if payload["valid"] else 1)
PY
