#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$REPO_ROOT/scripts/install_codex_skills.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

CODEX_HOME="$TMP_ROOT/codex-home"
bash "$INSTALLER" --source-root "$REPO_ROOT" --codex-home "$CODEX_HOME" >/dev/null

SKILL_SCRIPT="$CODEX_HOME/skills/wrf-workspace-init/scripts/init_workspace.sh"
WORKSPACE_ROOT="$TMP_ROOT/generated-workspace"
OUTPUT="$(bash "$SKILL_SCRIPT" --target-root "$WORKSPACE_ROOT")"

[[ -f "$WORKSPACE_ROOT/CODEX_WORKSPACE_READY.txt" ]]
[[ -f "$WORKSPACE_ROOT/scripts/wrf_task.py" ]]
[[ -f "$WORKSPACE_ROOT/config/simulation_schema.json" ]]
[[ -f "$WORKSPACE_ROOT/.claude/skills/wrf/SKILL.md" ]]
[[ -f "$WORKSPACE_ROOT/third_party/wps-support/Vtable.GFS" ]]

grep -Fq "workspace root: $WORKSPACE_ROOT" <<<"$OUTPUT"
grep -Fq "workspace_root: $WORKSPACE_ROOT" "$WORKSPACE_ROOT/CODEX_WORKSPACE_READY.txt"

conflict_fail=0
if bash "$SKILL_SCRIPT" --target-root "$WORKSPACE_ROOT" >/dev/null 2>&1; then
  conflict_fail=1
fi
[[ "$conflict_fail" -eq 0 ]]
