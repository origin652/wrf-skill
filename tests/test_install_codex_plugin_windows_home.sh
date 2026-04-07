#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$REPO_ROOT/scripts/install_codex_plugin_windows_home.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

WINDOWS_HOME="$TMP_ROOT/windows-home"
OUTPUT="$(bash "$INSTALLER" --source-root "$REPO_ROOT" --windows-home "$WINDOWS_HOME")"

[[ -f "$WINDOWS_HOME/plugins/wrf-skill/.codex-plugin/plugin.json" ]]
[[ -f "$WINDOWS_HOME/.agents/plugins/marketplace.json" ]]
[[ -f "$WINDOWS_HOME/codex-workspaces/wrf-skill-workspace/CODEX_WORKSPACE_READY.txt" ]]
grep -Fq "Resolved Windows home: $WINDOWS_HOME" <<<"$OUTPUT"
grep -Fq "$WINDOWS_HOME/codex-workspaces/wrf-skill-workspace" <<<"$OUTPUT"

NO_WS_HOME="$TMP_ROOT/windows-home-no-workspace"
bash "$INSTALLER" --source-root "$REPO_ROOT" --windows-home "$NO_WS_HOME" --no-workspace
[[ -f "$NO_WS_HOME/plugins/wrf-skill/.codex-plugin/plugin.json" ]]
[[ -f "$NO_WS_HOME/.agents/plugins/marketplace.json" ]]
[[ ! -e "$NO_WS_HOME/codex-workspaces/wrf-skill-workspace" ]]
