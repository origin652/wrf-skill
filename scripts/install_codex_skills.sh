#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SKILLS_SOURCE=""
CODEX_HOME="${CODEX_HOME:-}"
SKILLS_DIR=""
FORCE=0
SELECTED_SKILLS=()
WORKSPACE_BUNDLE_SKILL_NAME="wrf-workspace-init"
WORKSPACE_BUNDLE_INCLUDE_PATHS=(
  ".claude/skills"
  ".gitignore"
  "config/domains_presets.json"
  "config/physics_schemes.json"
  "config/post_schema.json"
  "config/simulation_schema.json"
  "config/wrf_env.hpc.example.json"
  "runs/.gitkeep"
  "scripts"
  "templates"
  "third_party/wps-support"
)

usage() {
  cat <<'USAGE'
Usage: bash scripts/install_codex_skills.sh [options]

Install the repository WRF skills into a Codex skills directory.
By default this script installs into the current user's WSL Codex home: $HOME/.codex/skills

Options:
  --source-root PATH     Source repository root. Default: repo root inferred from this script.
  --skills-source PATH   Source skill directory. Default: <source-root>/.claude/skills
  --codex-home PATH      Codex home directory. Default: $CODEX_HOME or $HOME/.codex
  --skills-dir PATH      Explicit Codex skills directory. Overrides --codex-home.
  --skill NAME           Install only the named skill. Repeatable.
  --force                Replace an existing installed skill directory.
  -h, --help             Show this help message.
USAGE
}

die() {
  echo "$*" >&2
  exit 1
}

resolve_dir_path() {
  local raw_path="$1"
  mkdir -p "$raw_path"
  (
    cd "$raw_path"
    pwd
  )
}

copy_tree_paths() {
  local source_root="$1"
  local target_root="$2"
  shift 2
  local rel_path

  mkdir -p "$target_root"
  for rel_path in "$@"; do
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
}

copy_skill_dir() {
  local source_dir="$1"
  local target_dir="$2"

  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  cp -a "$source_dir/." "$target_dir/"
}

populate_workspace_bundle() {
  local source_root="$1"
  local skill_root="$2"
  local bundle_root="$skill_root/assets/workspace-bundle"
  local rel_path

  command -v tar >/dev/null 2>&1 || die "Missing command: tar"
  rm -rf "$bundle_root"
  mkdir -p "$bundle_root"
  copy_tree_paths "$source_root" "$bundle_root" "${WORKSPACE_BUNDLE_INCLUDE_PATHS[@]}"

  {
    printf 'WRF workspace bundle for %s\n' "$WORKSPACE_BUNDLE_SKILL_NAME"
    printf 'Generated from: %s\n' "$source_root"
    printf 'Included paths:\n'
    for rel_path in "${WORKSPACE_BUNDLE_INCLUDE_PATHS[@]}"; do
      printf '  - %s\n' "$rel_path"
    done
  } > "$skill_root/assets/workspace-bundle/WORKSPACE_TEMPLATE.txt"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-root)
      [[ $# -ge 2 ]] || die "Missing value for --source-root"
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --skills-source)
      [[ $# -ge 2 ]] || die "Missing value for --skills-source"
      SKILLS_SOURCE="$2"
      shift 2
      ;;
    --codex-home)
      [[ $# -ge 2 ]] || die "Missing value for --codex-home"
      CODEX_HOME="$2"
      shift 2
      ;;
    --skills-dir)
      [[ $# -ge 2 ]] || die "Missing value for --skills-dir"
      SKILLS_DIR="$2"
      shift 2
      ;;
    --skill)
      [[ $# -ge 2 ]] || die "Missing value for --skill"
      SELECTED_SKILLS+=("$2")
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

[[ -d "$SOURCE_ROOT" ]] || die "Missing source root directory: $SOURCE_ROOT"
SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"

if [[ -z "$SKILLS_SOURCE" ]]; then
  SKILLS_SOURCE="$SOURCE_ROOT/.claude/skills"
fi
[[ -d "$SKILLS_SOURCE" ]] || die "Missing skills source directory: $SKILLS_SOURCE"
SKILLS_SOURCE="$(cd "$SKILLS_SOURCE" && pwd)"

if [[ -z "$SKILLS_DIR" ]]; then
  if [[ -z "$CODEX_HOME" ]]; then
    CODEX_HOME="$HOME/.codex"
  fi
  CODEX_HOME="$(resolve_dir_path "$CODEX_HOME")"
  SKILLS_DIR="$CODEX_HOME/skills"
fi
SKILLS_DIR="$(resolve_dir_path "$SKILLS_DIR")"

AVAILABLE_SKILLS=()
shopt -s nullglob
for candidate in "$SKILLS_SOURCE"/*; do
  [[ -d "$candidate" ]] || continue
  [[ -f "$candidate/SKILL.md" ]] || continue
  AVAILABLE_SKILLS+=("$(basename "$candidate")")
done
shopt -u nullglob

[[ ${#AVAILABLE_SKILLS[@]} -gt 0 ]] || die "No skills found in: $SKILLS_SOURCE"

if [[ ${#SELECTED_SKILLS[@]} -eq 0 ]]; then
  INSTALL_SKILLS=("${AVAILABLE_SKILLS[@]}")
else
  INSTALL_SKILLS=()
  for skill_name in "${SELECTED_SKILLS[@]}"; do
    [[ -f "$SKILLS_SOURCE/$skill_name/SKILL.md" ]] || die "Unknown skill: $skill_name"
    INSTALL_SKILLS+=("$skill_name")
  done
fi

for skill_name in "${INSTALL_SKILLS[@]}"; do
  target_dir="$SKILLS_DIR/$skill_name"
  if [[ -e "$target_dir" && "$FORCE" -ne 1 ]]; then
    die "Target skill already exists: $target_dir. Use --force to replace it."
  fi
done

INSTALLED_SKILLS=()
for skill_name in "${INSTALL_SKILLS[@]}"; do
  target_dir="$SKILLS_DIR/$skill_name"
  copy_skill_dir "$SKILLS_SOURCE/$skill_name" "$target_dir"
  if [[ "$skill_name" == "$WORKSPACE_BUNDLE_SKILL_NAME" ]]; then
    populate_workspace_bundle "$SOURCE_ROOT" "$target_dir"
  fi
  INSTALLED_SKILLS+=("$skill_name")
done

printf 'Installed Codex skills into: %s\n' "$SKILLS_DIR"
printf 'Source skills directory: %s\n' "$SKILLS_SOURCE"
printf 'Installed skills (%s):\n' "${#INSTALLED_SKILLS[@]}"
for skill_name in "${INSTALLED_SKILLS[@]}"; do
  printf '  - %s\n' "$skill_name"
done
