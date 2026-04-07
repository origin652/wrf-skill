#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BASE_INSTALLER="${SCRIPT_DIR}/install_codex_plugin.sh"
WINDOWS_HOME=""
HAS_PLUGINS_DIR=0
HAS_MARKETPLACE_PATH=0
HAS_WORKSPACE_ROOT=0
FORWARD_ARGS=()

usage() {
  cat <<'USAGE'
Usage: bash scripts/install_codex_plugin_windows_home.sh [options]

Run the Codex installer from WSL, but target the Windows user home so the Codex UI can see the plugin.

Options:
  --windows-home PATH       Windows-side home directory mounted in WSL.
  --source-root PATH        Source repository root or extracted bundle root.
  --plugins-dir PATH        Override plugin installation directory.
  --marketplace-path PATH   Override marketplace file path.
  --workspace-root PATH     Override workspace deployment directory.
  --plugin-name NAME        Forwarded to the base installer.
  --no-workspace            Forwarded to the base installer.
  --force                   Forwarded to the base installer.
  -h, --help                Show this help message.

If no explicit paths are provided, this script installs to:
  <windows-home>/plugins
  <windows-home>/.agents/plugins/marketplace.json
  <windows-home>/codex-workspaces/wrf-skill-workspace
USAGE
}

die() {
  echo "$*" >&2
  exit 1
}

detect_windows_home() {
  local win_home_raw

  command -v cmd.exe >/dev/null 2>&1 || die "Missing command: cmd.exe"
  command -v wslpath >/dev/null 2>&1 || die "Missing command: wslpath"

  win_home_raw="$(cmd.exe /c echo %UserProfile% 2>/dev/null | tr -d '\r')"
  [[ -n "$win_home_raw" ]] || die "Failed to detect Windows user home from cmd.exe"
  wslpath "$win_home_raw"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --windows-home)
      [[ $# -ge 2 ]] || die "Missing value for --windows-home"
      WINDOWS_HOME="$2"
      shift 2
      ;;
    --source-root)
      [[ $# -ge 2 ]] || die "Missing value for --source-root"
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --plugins-dir)
      [[ $# -ge 2 ]] || die "Missing value for --plugins-dir"
      HAS_PLUGINS_DIR=1
      FORWARD_ARGS+=("--plugins-dir" "$2")
      shift 2
      ;;
    --marketplace-path)
      [[ $# -ge 2 ]] || die "Missing value for --marketplace-path"
      HAS_MARKETPLACE_PATH=1
      FORWARD_ARGS+=("--marketplace-path" "$2")
      shift 2
      ;;
    --workspace-root)
      [[ $# -ge 2 ]] || die "Missing value for --workspace-root"
      HAS_WORKSPACE_ROOT=1
      FORWARD_ARGS+=("--workspace-root" "$2")
      shift 2
      ;;
    --plugin-name)
      [[ $# -ge 2 ]] || die "Missing value for --plugin-name"
      FORWARD_ARGS+=("--plugin-name" "$2")
      shift 2
      ;;
    --no-workspace|--force)
      FORWARD_ARGS+=("$1")
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

[[ -x "$BASE_INSTALLER" ]] || die "Missing installer: $BASE_INSTALLER"
[[ -d "$SOURCE_ROOT" ]] || die "Missing source root directory: $SOURCE_ROOT"
SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"

if [[ -z "$WINDOWS_HOME" ]]; then
  WINDOWS_HOME="$(detect_windows_home)"
else
  mkdir -p "$WINDOWS_HOME"
  WINDOWS_HOME="$(cd "$WINDOWS_HOME" && pwd)"
fi

if [[ "$HAS_PLUGINS_DIR" -eq 0 ]]; then
  FORWARD_ARGS+=("--plugins-dir" "$WINDOWS_HOME/plugins")
fi
if [[ "$HAS_MARKETPLACE_PATH" -eq 0 ]]; then
  FORWARD_ARGS+=("--marketplace-path" "$WINDOWS_HOME/.agents/plugins/marketplace.json")
fi
if [[ "$HAS_WORKSPACE_ROOT" -eq 0 ]]; then
  FORWARD_ARGS+=("--workspace-root" "$WINDOWS_HOME/codex-workspaces/wrf-skill-workspace")
fi

printf 'Resolved Windows home: %s\n' "$WINDOWS_HOME"
exec bash "$BASE_INSTALLER" --source-root "$SOURCE_ROOT" "${FORWARD_ARGS[@]}"
