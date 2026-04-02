#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-config/wrf_env.json}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Missing config file: $CONFIG_PATH" >&2
  exit 1
fi

PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "Missing command: python3" >&2
  exit 1
fi

readarray -t CONFIG_VALUES < <(
"$PYTHON_CMD" - "$CONFIG_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    config = json.load(handle)

fields = [
    config.get("platform", ""),
    config.get("wrf_dir", ""),
    config.get("wps_dir", ""),
    config.get("geog_data_path", ""),
    config.get("python_env", ""),
    config.get("wps_bin_dir", ""),
    config.get("wrf_run_dir", ""),
    config.get("wps_tables", {}).get("geogrid", ""),
    config.get("wps_tables", {}).get("metgrid", ""),
    config.get("wps_tables", {}).get("vtable", ""),
]
for field in fields:
    print(field)
PY
)

PLATFORM="${CONFIG_VALUES[0]}"
WRF_DIR="${CONFIG_VALUES[1]}"
WPS_DIR="${CONFIG_VALUES[2]}"
GEOG_DATA_PATH="${CONFIG_VALUES[3]}"
PYTHON_ENV_NAME="${CONFIG_VALUES[4]}"
WPS_BIN_DIR="${CONFIG_VALUES[5]}"
WRF_RUN_DIR="${CONFIG_VALUES[6]}"
GEOGRID_TBL="${CONFIG_VALUES[7]}"
METGRID_TBL="${CONFIG_VALUES[8]}"
VTABLE_FILE="${CONFIG_VALUES[9]}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This scaffold only supports Linux/WSL execution." >&2
  exit 1
fi

if grep -qi microsoft /proc/version 2>/dev/null; then
  HOST_KIND="wsl"
else
  HOST_KIND="linux"
fi

echo "Detected host: $HOST_KIND"
echo "Configured platform: $PLATFORM"

resolve_wps_exec() {
  local name="$1"
  local search_dirs=()

  if [[ -n "$WPS_BIN_DIR" ]]; then
    search_dirs+=("$WPS_BIN_DIR")
  fi
  if [[ -n "$WPS_DIR" ]]; then
    search_dirs+=("$WPS_DIR" "$WPS_DIR/bin")
  fi

  for dir in "${search_dirs[@]}"; do
    [[ -n "$dir" ]] || continue
    for candidate in "$dir/$name" "$dir/$name.exe"; do
      if [[ -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
  done

  return 1
}

resolve_wrf_exec() {
  local name="$1"
  local search_dirs=()

  if [[ -n "$WRF_RUN_DIR" ]]; then
    search_dirs+=("$WRF_RUN_DIR")
  fi
  if [[ -n "$WRF_DIR" ]]; then
    search_dirs+=("$WRF_DIR/run" "$WRF_DIR/bin" "$WRF_DIR/main")
  fi

  for dir in "${search_dirs[@]}"; do
    [[ -n "$dir" ]] || continue
    for candidate in "$dir/$name" "$dir/$name.exe"; do
      if [[ -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
  done

  return 1
}

MISSING=0
for command_name in "$PYTHON_CMD" mpirun; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing command: $command_name" >&2
    MISSING=1
  fi
done

for path_name in "$WRF_DIR" "$WPS_DIR" "$GEOG_DATA_PATH"; do
  if [[ ! -e "$path_name" ]]; then
    echo "Missing path: $path_name" >&2
    MISSING=1
  fi
done

for executable_name in geogrid ungrib metgrid link_grib.csh; do
  if ! resolved_path="$(resolve_wps_exec "$executable_name")"; then
    echo "Missing WPS executable: $executable_name under $WPS_DIR or $WPS_BIN_DIR" >&2
    MISSING=1
  else
    echo "Resolved WPS executable $executable_name -> $resolved_path"
  fi
done

for executable_name in real wrf; do
  if ! resolved_path="$(resolve_wrf_exec "$executable_name")"; then
    echo "Missing WRF executable: $executable_name under $WRF_RUN_DIR, $WRF_DIR/run, $WRF_DIR/bin, or $WRF_DIR/main" >&2
    MISSING=1
  else
    echo "Resolved WRF executable $executable_name -> $resolved_path"
  fi
done

for table_entry in \
  "GEOGRID.TBL:$GEOGRID_TBL" \
  "METGRID.TBL:$METGRID_TBL" \
  "Vtable:$VTABLE_FILE"; do
  table_name="${table_entry%%:*}"
  table_path="${table_entry#*:}"
  if [[ -n "$table_path" && ! -e "$table_path" ]]; then
    echo "Missing WPS support file $table_name: $table_path" >&2
    MISSING=1
  fi
done

if [[ -d "$GEOG_DATA_PATH" ]]; then
  if ! find "$GEOG_DATA_PATH" -maxdepth 2 -type f -name index | grep -q .; then
    echo "Warning: geog_data_path exists but no WPS geography index files were found: $GEOG_DATA_PATH" >&2
  fi
fi

echo "Configured Python environment: $PYTHON_ENV_NAME"

if [[ "$MISSING" -ne 0 ]]; then
  exit 1
fi

echo "Environment check passed."
