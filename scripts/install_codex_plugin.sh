#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLUGIN_NAME="wrf-skill"
PLUGINS_DIR="${HOME}/plugins"
MARKETPLACE_PATH="${HOME}/.agents/plugins/marketplace.json"
WORKSPACE_ROOT="${HOME}/codex-workspaces/wrf-skill-workspace"
WORKSPACE_READY_FILE_NAME="CODEX_WORKSPACE_READY.txt"
DEPLOY_WORKSPACE=1
FORCE=0
WORKSPACE_INCLUDE_PATHS=(
  ".agents/plugins/marketplace.json"
  ".claude/skills"
  ".gitignore"
  "config/domains_presets.json"
  "config/physics_schemes.json"
  "config/simulation_schema.json"
  "config/wrf_env.hpc.example.json"
  "plugins/wrf-skill"
  "runs/.gitkeep"
  "scripts"
  "templates"
  "third_party/wps-support"
)

usage() {
  cat <<'USAGE'
Usage: bash scripts/install_codex_plugin.sh [options]

Register the native Codex WRF plugin in a home-level plugin directory and marketplace.
By default this script also deploys a compatible Codex workspace and prints an AI handoff block. Under WSL, <codex-home> resolves to the Windows user home.

Options:
  --source-root PATH        Source repository root or extracted bundle root.
  --plugins-dir PATH        Plugin installation directory. Default: <codex-home>/plugins
  --marketplace-path PATH   Marketplace file path. Default: <codex-home>/.agents/plugins/marketplace.json
  --workspace-root PATH     Workspace deployment directory. Default: <codex-home>/codex-workspaces/wrf-skill-workspace
  --plugin-name NAME        Plugin name. Default: wrf-skill
  --no-workspace            Skip workspace deployment and only register the global plugin.
  --force                   Replace an existing plugin directory, marketplace entry, or workspace.
  -h, --help                Show this help message.
USAGE
}

die() {
  echo "$*" >&2
  exit 1
}

resolve_parent_path() {
  local raw_path="$1"
  local parent_dir
  local base_name

  parent_dir="$(dirname "$raw_path")"
  base_name="$(basename "$raw_path")"
  mkdir -p "$parent_dir"
  parent_dir="$(cd "$parent_dir" && pwd)"
  printf '%s/%s\n' "$parent_dir" "$base_name"
}

copy_workspace_tree() {
  local source_root="$1"
  local target_root="$2"
  local plugin_root="$3"
  local marketplace_path="$4"
  local rel_path

  mkdir -p "$target_root"
  for rel_path in "${WORKSPACE_INCLUDE_PATHS[@]}"; do
    [[ -e "$source_root/$rel_path" ]] || continue
    (
      cd "$source_root"
      tar \
        --exclude='*/__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        -cf - "$rel_path"
    ) | (
      cd "$target_root"
      tar -xf -
    )
  done

  cat > "$target_root/$WORKSPACE_READY_FILE_NAME" <<EOF2
WRF Codex workspace is deployed and ready.

workspace_root: $TARGET_WORKSPACE_ROOT
source_root: $source_root
plugin_root: $plugin_root
marketplace_path: $marketplace_path

Codex handoff prompt:
Open the WRF workspace at: $TARGET_WORKSPACE_ROOT
Confirm the workspace is ready by reading: $WORKSPACE_READY_FILE_NAME
Use the native wrf-skill plugin available in this environment.
Then continue with the WRF task requested by the user.
EOF2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-root)
      [[ $# -ge 2 ]] || die "Missing value for --source-root"
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --plugins-dir)
      [[ $# -ge 2 ]] || die "Missing value for --plugins-dir"
      PLUGINS_DIR="$2"
      shift 2
      ;;
    --marketplace-path)
      [[ $# -ge 2 ]] || die "Missing value for --marketplace-path"
      MARKETPLACE_PATH="$2"
      shift 2
      ;;
    --workspace-root)
      [[ $# -ge 2 ]] || die "Missing value for --workspace-root"
      WORKSPACE_ROOT="$2"
      shift 2
      ;;
    --plugin-name)
      [[ $# -ge 2 ]] || die "Missing value for --plugin-name"
      PLUGIN_NAME="$2"
      shift 2
      ;;
    --no-workspace)
      DEPLOY_WORKSPACE=0
      shift
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

SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"
PLUGINS_DIR="$(resolve_parent_path "$PLUGINS_DIR")"
MARKETPLACE_PATH="$(resolve_parent_path "$MARKETPLACE_PATH")"
TARGET_WORKSPACE_ROOT="$(resolve_parent_path "$WORKSPACE_ROOT")"
PLUGIN_SOURCE="${SOURCE_ROOT}/plugins/${PLUGIN_NAME}"
SOURCE_MARKETPLACE="${SOURCE_ROOT}/.agents/plugins/marketplace.json"
TARGET_PLUGIN_ROOT="${PLUGINS_DIR%/}/${PLUGIN_NAME}"
WORKSPACE_READY_FILE="${TARGET_WORKSPACE_ROOT}/${WORKSPACE_READY_FILE_NAME}"
command -v perl >/dev/null 2>&1 || die "Missing command: perl"
command -v tar >/dev/null 2>&1 || die "Missing command: tar"

[[ -d "$PLUGIN_SOURCE" ]] || die "Missing plugin source directory: $PLUGIN_SOURCE"
[[ -f "$PLUGIN_SOURCE/.codex-plugin/plugin.json" ]] || die "Missing plugin manifest: $PLUGIN_SOURCE/.codex-plugin/plugin.json"

if [[ -e "$TARGET_PLUGIN_ROOT" && "$FORCE" -ne 1 ]]; then
  die "Target plugin directory already exists: $TARGET_PLUGIN_ROOT. Use --force to replace it."
fi

if [[ "$DEPLOY_WORKSPACE" -eq 1 ]]; then
  if [[ "$TARGET_WORKSPACE_ROOT" == "$SOURCE_ROOT" ]]; then
    die "Workspace root must differ from source root: $TARGET_WORKSPACE_ROOT"
  fi
  if [[ -e "$TARGET_WORKSPACE_ROOT" && "$FORCE" -ne 1 ]]; then
    die "Workspace directory already exists: $TARGET_WORKSPACE_ROOT. Use --force to replace it."
  fi
fi

if [[ -f "$MARKETPLACE_PATH" && "$FORCE" -ne 1 ]]; then
  if ! PLUGIN_NAME="$PLUGIN_NAME" TARGET_MARKETPLACE="$MARKETPLACE_PATH" perl -MJSON::PP -e '
use strict;
use warnings;
use JSON::PP;

my $path = $ENV{TARGET_MARKETPLACE};
my $plugin = $ENV{PLUGIN_NAME};
local $/;
open my $fh, "<", $path or die "Cannot read $path: $!\n";
my $payload = JSON::PP->new->decode(<$fh>);
my $plugins = ref($payload->{plugins}) eq "ARRAY" ? $payload->{plugins} : [];
for my $entry (@{$plugins}) {
    next unless ref($entry) eq "HASH";
    my $name = defined $entry->{name} ? $entry->{name} : "";
    my $source_path = ref($entry->{source}) eq "HASH" ? ($entry->{source}{path} // "") : "";
    if ($name eq $plugin || $source_path eq "./plugins/$plugin") {
        exit 10;
    }
}
exit 0;
' >/dev/null 2>&1; then
    status=$?
    if [[ "$status" -eq 10 ]]; then
      die "Marketplace already contains an entry for ${PLUGIN_NAME}. Use --force to replace it."
    fi
    exit "$status"
  fi
fi

mkdir -p "$PLUGINS_DIR"
TMP_PLUGIN_ROOT="${TARGET_PLUGIN_ROOT}.tmp.$$"
TMP_WORKSPACE_ROOT="${TARGET_WORKSPACE_ROOT}.tmp.$$"
TMP_MARKETPLACE="$(mktemp "${TMPDIR:-/tmp}/wrf-marketplace.XXXXXX")"
cleanup() {
  rm -f "$TMP_MARKETPLACE"
  rm -rf "$TMP_PLUGIN_ROOT" "$TMP_WORKSPACE_ROOT"
}
trap cleanup EXIT

rm -rf "$TMP_PLUGIN_ROOT" "$TMP_WORKSPACE_ROOT"
cp -a "$PLUGIN_SOURCE" "$TMP_PLUGIN_ROOT"
if [[ -e "$TARGET_PLUGIN_ROOT" ]]; then
  rm -rf "$TARGET_PLUGIN_ROOT"
fi
mv "$TMP_PLUGIN_ROOT" "$TARGET_PLUGIN_ROOT"

PLUGIN_NAME="$PLUGIN_NAME" \
TARGET_MARKETPLACE="$MARKETPLACE_PATH" \
SOURCE_MARKETPLACE="$SOURCE_MARKETPLACE" \
FORCE="$FORCE" \
perl -MJSON::PP -e '
use strict;
use warnings;
use JSON::PP;

sub slurp {
    my ($path) = @_;
    local $/;
    open my $fh, "<", $path or die "Cannot read $path: $!\n";
    return <$fh>;
}

my $plugin = $ENV{PLUGIN_NAME};
my $target_marketplace = $ENV{TARGET_MARKETPLACE};
my $source_marketplace = $ENV{SOURCE_MARKETPLACE};
my $force = $ENV{FORCE} eq "1";
my $codec = JSON::PP->new->ascii->pretty->canonical;
my $default_name = "wrf-skill-local";
my $default_display = "WRF Skill Local Plugins";
my $entry = {
    name => $plugin,
    source => {
        source => "local",
        path => "./plugins/$plugin",
    },
    policy => {
        installation => "AVAILABLE",
        authentication => "ON_INSTALL",
    },
    category => "Coding",
};

my $payload;
if (-f $target_marketplace) {
    $payload = $codec->decode(slurp($target_marketplace));
} else {
    my $source_payload = {};
    if (-f $source_marketplace) {
        eval {
            $source_payload = $codec->decode(slurp($source_marketplace));
            1;
        } or $source_payload = {};
    }
    $payload = {
        name => (defined $source_payload->{name} && !ref($source_payload->{name}) && length($source_payload->{name})
            ? $source_payload->{name}
            : $default_name),
        interface => {
            displayName => (
                ref($source_payload->{interface}) eq "HASH"
                && defined $source_payload->{interface}{displayName}
                && !ref($source_payload->{interface}{displayName})
                && length($source_payload->{interface}{displayName})
            )
                ? $source_payload->{interface}{displayName}
                : $default_display,
        },
        plugins => [],
    };
}

die "Marketplace root must be a JSON object\n" unless ref($payload) eq "HASH";
$payload->{plugins} = [] unless ref($payload->{plugins}) eq "ARRAY";

my $existing_index = -1;
for my $index (0 .. $#{$payload->{plugins}}) {
    my $item = $payload->{plugins}[$index];
    next unless ref($item) eq "HASH";
    my $name = defined $item->{name} ? $item->{name} : "";
    my $source_path = ref($item->{source}) eq "HASH" ? ($item->{source}{path} // "") : "";
    if ($name eq $plugin || $source_path eq "./plugins/$plugin") {
        $existing_index = $index;
        last;
    }
}

if ($existing_index >= 0) {
    die "Marketplace already contains an entry for $plugin\n" unless $force;
    $payload->{plugins}[$existing_index] = $entry;
} else {
    push @{$payload->{plugins}}, $entry;
}

print $codec->encode($payload);
' > "$TMP_MARKETPLACE"

mkdir -p "$(dirname "$MARKETPLACE_PATH")"
mv "$TMP_MARKETPLACE" "$MARKETPLACE_PATH"

if [[ "$DEPLOY_WORKSPACE" -eq 1 ]]; then
  copy_workspace_tree "$SOURCE_ROOT" "$TMP_WORKSPACE_ROOT" "$TARGET_PLUGIN_ROOT" "$MARKETPLACE_PATH"
  if [[ -e "$TARGET_WORKSPACE_ROOT" ]]; then
    rm -rf "$TARGET_WORKSPACE_ROOT"
  fi
  mv "$TMP_WORKSPACE_ROOT" "$TARGET_WORKSPACE_ROOT"
fi

trap - EXIT
cleanup

cat <<EOF2
Installed Codex plugin:
  source root: $SOURCE_ROOT
  plugin root: $TARGET_PLUGIN_ROOT
  marketplace: $MARKETPLACE_PATH
EOF2

if [[ "$DEPLOY_WORKSPACE" -eq 1 ]]; then
  cat <<EOF2

Deployed Codex workspace:
  workspace root: $TARGET_WORKSPACE_ROOT
  ready file: $WORKSPACE_READY_FILE

AI handoff prompt:
  Open the WRF workspace at: $TARGET_WORKSPACE_ROOT
  Confirm the workspace is ready by reading: $WORKSPACE_READY_FILE_NAME
  Use the native wrf-skill plugin available in this environment.
  Then continue with the WRF task requested by the user.
EOF2
else
  cat <<EOF2

Workspace deployment skipped.
  To deploy a compatible workspace later, rerun without --no-workspace.
EOF2
fi
