#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$REPO_ROOT/scripts/install_codex_plugin.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PLUGINS_DIR="$TMP_ROOT/plugins"
MARKETPLACE_PATH="$TMP_ROOT/.agents/plugins/marketplace.json"
WORKSPACE_ROOT="$TMP_ROOT/workspace"

INSTALL_OUTPUT="$(bash "$INSTALLER" \
  --source-root "$REPO_ROOT" \
  --plugins-dir "$PLUGINS_DIR" \
  --marketplace-path "$MARKETPLACE_PATH" \
  --workspace-root "$WORKSPACE_ROOT")"

[[ -f "$PLUGINS_DIR/wrf-skill/.codex-plugin/plugin.json" ]]
[[ -f "$WORKSPACE_ROOT/CODEX_WORKSPACE_READY.txt" ]]
[[ -f "$WORKSPACE_ROOT/scripts/install_codex_plugin.sh" ]]
grep -Eq '"path"[[:space:]]*:[[:space:]]*"\./plugins/wrf-skill"' "$MARKETPLACE_PATH"
grep -Fq "AI handoff prompt:" <<<"$INSTALL_OUTPUT"
grep -Fq "$WORKSPACE_ROOT" <<<"$INSTALL_OUTPUT"
grep -Fq "Codex handoff prompt:" "$WORKSPACE_ROOT/CODEX_WORKSPACE_READY.txt"
grep -Fq "$WORKSPACE_ROOT" "$WORKSPACE_ROOT/CODEX_WORKSPACE_READY.txt"

if bash "$INSTALLER" \
  --source-root "$REPO_ROOT" \
  --plugins-dir "$PLUGINS_DIR" \
  --marketplace-path "$MARKETPLACE_PATH" \
  --workspace-root "$WORKSPACE_ROOT" >/dev/null 2>&1; then
  echo "Expected second install without --force to fail" >&2
  exit 1
fi

OTHER_MARKETPLACE="$TMP_ROOT/other-marketplace.json"
cat > "$OTHER_MARKETPLACE" <<'JSON'
{
  "name": "custom-marketplace",
  "interface": {
    "displayName": "Custom Plugins"
  },
  "plugins": [
    {
      "name": "other-plugin",
      "source": {
        "source": "local",
        "path": "./plugins/other-plugin"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Coding"
    }
  ]
}
JSON

OTHER_PLUGINS_DIR="$TMP_ROOT/other-plugins"
OTHER_WORKSPACE_ROOT="$TMP_ROOT/other-workspace"
bash "$INSTALLER" \
  --source-root "$REPO_ROOT" \
  --plugins-dir "$OTHER_PLUGINS_DIR" \
  --marketplace-path "$OTHER_MARKETPLACE" \
  --workspace-root "$OTHER_WORKSPACE_ROOT" \
  --no-workspace

grep -Eq '"name"[[:space:]]*:[[:space:]]*"other-plugin"' "$OTHER_MARKETPLACE"
grep -Eq '"path"[[:space:]]*:[[:space:]]*"\./plugins/wrf-skill"' "$OTHER_MARKETPLACE"
[[ ! -e "$OTHER_WORKSPACE_ROOT" ]]
