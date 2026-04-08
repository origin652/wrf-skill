#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$REPO_ROOT/scripts/install_codex_skills.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

CODEX_HOME="$TMP_ROOT/codex-home"
OUTPUT="$(bash "$INSTALLER" --source-root "$REPO_ROOT" --codex-home "$CODEX_HOME")"

for skill_name in wrf wrf-config wrf-data wrf-init wrf-post wrf-run wrf-status wrf-workspace-init wrf-wps; do
  [[ -f "$CODEX_HOME/skills/$skill_name/SKILL.md" ]]
done

[[ -f "$CODEX_HOME/skills/wrf-workspace-init/scripts/init_workspace.sh" ]]
[[ -f "$CODEX_HOME/skills/wrf-workspace-init/assets/workspace-bundle/scripts/wrf_task.py" ]]
[[ -f "$CODEX_HOME/skills/wrf-workspace-init/assets/workspace-bundle/scripts/post_spec.py" ]]
[[ -f "$CODEX_HOME/skills/wrf-workspace-init/assets/workspace-bundle/scripts/wrf_post.py" ]]
[[ -f "$CODEX_HOME/skills/wrf-workspace-init/assets/workspace-bundle/config/post_schema.json" ]]
[[ -f "$CODEX_HOME/skills/wrf-workspace-init/assets/workspace-bundle/config/simulation_schema.json" ]]
[[ -f "$CODEX_HOME/skills/wrf-workspace-init/assets/workspace-bundle/third_party/wps-support/Vtable.GFS" ]]

grep -Fq "Installed Codex skills into: $CODEX_HOME/skills" <<<"$OUTPUT"
grep -Fq "Installed skills (9):" <<<"$OUTPUT"

auto_fail=0
if bash "$INSTALLER" --source-root "$REPO_ROOT" --codex-home "$CODEX_HOME" >/dev/null 2>&1; then
  auto_fail=1
fi
[[ "$auto_fail" -eq 0 ]]

SUBSET_HOME="$TMP_ROOT/codex-home-subset"
bash "$INSTALLER" --source-root "$REPO_ROOT" --codex-home "$SUBSET_HOME" --skill wrf --skill wrf-wps
[[ -f "$SUBSET_HOME/skills/wrf/SKILL.md" ]]
[[ -f "$SUBSET_HOME/skills/wrf-wps/SKILL.md" ]]
[[ ! -e "$SUBSET_HOME/skills/wrf-run" ]]
