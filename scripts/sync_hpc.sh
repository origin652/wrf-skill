#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <access_mode> <local_project_dir> <remote_host> <remote_project_dir> [sync_scope] [--dry-run]" >&2
}

normalize_access_mode() {
  local mode="${1:-}"
  case "${mode,,}" in
    login|login_node|login-node|local)
      echo "login"
      ;;
    ssh|remote_ssh|remote-ssh)
      echo "ssh"
      ;;
    *)
      echo "${mode,,}"
      ;;
  esac
}

if [[ $# -lt 4 ]]; then
  usage
  exit 1
fi

ACCESS_MODE="$(normalize_access_mode "$1")"
LOCAL_PROJECT_DIR="$2"
REMOTE_HOST="$3"
REMOTE_PROJECT_DIR="$4"
SYNC_SCOPE="wrf-run"
DRY_RUN_FLAG=""
SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ge 5 ]]; then
  if [[ "$5" == "--dry-run" ]]; then
    DRY_RUN_FLAG="$5"
  else
    SYNC_SCOPE="$5"
    DRY_RUN_FLAG="${6:-}"
  fi
fi

case "$SYNC_SCOPE" in
  wrf-run)
    RSYNC_ARGS=(-av --delete --exclude .wrf-skill --exclude data --exclude output)
    ;;
  wrf-wps)
    RSYNC_ARGS=(-av --delete --exclude .wrf-skill --exclude output)
    ;;
  all)
    RSYNC_ARGS=(-av --delete --exclude .wrf-skill)
    ;;
  *)
    echo "sync_hpc.sh: unsupported sync_scope=$SYNC_SCOPE" >&2
    exit 2
    ;;
esac

SKILL_RSYNC_ARGS=(-av --delete --exclude __pycache__ --exclude '*.pyc')

if [[ "$DRY_RUN_FLAG" == "--dry-run" ]]; then
  RSYNC_ARGS+=(--dry-run)
  SKILL_RSYNC_ARGS+=(--dry-run)
fi

case "$ACCESS_MODE" in
  login)
    mkdir -p "$REMOTE_PROJECT_DIR/.wrf-skill"
    rsync "${RSYNC_ARGS[@]}" "$LOCAL_PROJECT_DIR/" "$REMOTE_PROJECT_DIR/"
    rsync "${SKILL_RSYNC_ARGS[@]}" "$SCRIPT_SOURCE_DIR/" "$REMOTE_PROJECT_DIR/.wrf-skill/scripts/"
    ;;
  ssh)
    if [[ -z "$REMOTE_HOST" || "$REMOTE_HOST" == "-" ]]; then
      echo "sync_hpc.sh: ssh mode requires remote_host" >&2
      exit 2
    fi
    ssh "$REMOTE_HOST" "mkdir -p \"$REMOTE_PROJECT_DIR/.wrf-skill\""
    rsync "${RSYNC_ARGS[@]}" "$LOCAL_PROJECT_DIR/" "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/"
    rsync "${SKILL_RSYNC_ARGS[@]}" "$SCRIPT_SOURCE_DIR/" "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/.wrf-skill/scripts/"
    ;;
  *)
    echo "sync_hpc.sh: unsupported access_mode=$ACCESS_MODE" >&2
    exit 2
    ;;
esac
