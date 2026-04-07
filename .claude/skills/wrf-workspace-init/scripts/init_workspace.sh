#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUNDLE_ROOT="${SKILL_ROOT}/assets/workspace-bundle"
WORKSPACE_READY_FILE_NAME="CODEX_WORKSPACE_READY.txt"
TARGET_ROOT=""
FORCE=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/init_workspace.sh [options]

Create a WRF workspace from the bundled template shipped with the wrf-workspace-init skill.

Options:
  --target-root PATH   Destination directory. Default: ./wrf-workspace
  --force              Replace an existing target directory.
  -h, --help           Show this help message.
USAGE
}

die() {
  echo "$*" >&2
  exit 1
}

resolve_target_path() {
  local raw_path="$1"
  local parent_dir
  local base_name

  parent_dir="$(dirname "$raw_path")"
  base_name="$(basename "$raw_path")"
  mkdir -p "$parent_dir"
  parent_dir="$(cd "$parent_dir" && pwd)"
  printf '%s/%s\n' "$parent_dir" "$base_name"
}

copy_bundle_tree() {
  local source_root="$1"
  local target_root="$2"

  mkdir -p "$target_root"
  (
    cd "$source_root"
    tar \
      --exclude='*/__pycache__' \
      --exclude='*.pyc' \
      --exclude='*.pyo' \
      -cf - .
  ) | (
    cd "$target_root"
    tar -xf -
  )
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-root)
      [[ $# -ge 2 ]] || die "Missing value for --target-root"
      TARGET_ROOT="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

command -v tar >/dev/null 2>&1 || die "Missing command: tar"
[[ -d "$BUNDLE_ROOT" ]] || die "Missing bundled workspace template: $BUNDLE_ROOT. Reinstall the wrf-workspace-init skill."

if [[ -z "$TARGET_ROOT" ]]; then
  TARGET_ROOT="$PWD/wrf-workspace"
fi
TARGET_ROOT="$(resolve_target_path "$TARGET_ROOT")"

case "$TARGET_ROOT" in
  /|"$HOME"|"$SKILL_ROOT"|"$BUNDLE_ROOT")
    die "Unsafe target directory: $TARGET_ROOT"
    ;;
esac

if [[ -e "$TARGET_ROOT" ]]; then
  if [[ "$FORCE" -ne 1 ]]; then
    die "Target workspace already exists: $TARGET_ROOT. Use --force to replace it."
  fi
  rm -rf "$TARGET_ROOT"
fi

copy_bundle_tree "$BUNDLE_ROOT" "$TARGET_ROOT"

cat > "$TARGET_ROOT/$WORKSPACE_READY_FILE_NAME" <<EOF2
WRF Codex workspace is deployed and ready.

workspace_root: $TARGET_ROOT
created_from_skill: $SKILL_ROOT
bundle_root: $BUNDLE_ROOT

This workspace includes:
- .claude/skills
- config
- scripts
- templates
- third_party/wps-support
- runs/.gitkeep

This workspace excludes:
- private config overrides
- WPS_GEOG datasets
- compiled WRF/WPS binaries
- existing runs outputs

If you need HPC mode:
1. Copy config/wrf_env.hpc.example.json to config/wrf_env.json
2. Fill in cluster-specific values before running wrf-config or wrf-task

Codex handoff prompt:
Open the WRF workspace at: $TARGET_ROOT
Confirm the workspace is ready by reading: $WORKSPACE_READY_FILE_NAME
Then continue with the user's WRF task from this workspace root.
EOF2

cat <<EOF2
Created WRF workspace:
  workspace root: $TARGET_ROOT
  ready file: $TARGET_ROOT/$WORKSPACE_READY_FILE_NAME
  template source: $BUNDLE_ROOT

AI handoff prompt:
  Open the WRF workspace at: $TARGET_ROOT
  Confirm the workspace is ready by reading: $WORKSPACE_READY_FILE_NAME
  Then continue with the user's WRF task from this workspace root.
EOF2
