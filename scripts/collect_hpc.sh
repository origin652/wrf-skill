#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <access_mode> <remote_host> <remote_project_dir> <local_project_dir> [collect_scope] [--dry-run]" >&2
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
REMOTE_HOST="$2"
REMOTE_PROJECT_DIR="$3"
LOCAL_PROJECT_DIR="$4"
COLLECT_SCOPE="wrf-run"
DRY_RUN_FLAG=""

if [[ $# -ge 5 ]]; then
  if [[ "$5" == "--dry-run" ]]; then
    DRY_RUN_FLAG="$5"
  else
    COLLECT_SCOPE="$5"
    DRY_RUN_FLAG="${6:-}"
  fi
fi

case "$COLLECT_SCOPE" in
  wrf-run|wrf-wps|all)
    ;;
  *)
    echo "collect_hpc.sh: unsupported collect_scope=$COLLECT_SCOPE" >&2
    exit 2
    ;;
esac

RSYNC_ARGS=(-av)
if [[ "$DRY_RUN_FLAG" == "--dry-run" ]]; then
  RSYNC_ARGS+=(--dry-run)
fi

rsync_optional() {
  set +e
  rsync "$@"
  local status=$?
  set -e
  if [[ $status -ne 0 && $status -ne 23 ]]; then
    return $status
  fi
  return 0
}

sync_lightweight_output() {
  local source_path="$1"
  local target_path="$2"
  rsync_optional "${RSYNC_ARGS[@]}" \
    --include='*/' \
    --include='*.png' \
    --include='*.jpg' \
    --include='*.jpeg' \
    --include='*.svg' \
    --include='*.pdf' \
    --include='*.webp' \
    --include='*.json' \
    --include='*.csv' \
    --include='*.txt' \
    --include='*.html' \
    --exclude='wrfout_d*' \
    --exclude='*' \
    "$source_path" "$target_path"
}

mkdir -p "$LOCAL_PROJECT_DIR/logs" "$LOCAL_PROJECT_DIR/output" "$LOCAL_PROJECT_DIR/wrf" "$LOCAL_PROJECT_DIR/wps"
case "$ACCESS_MODE" in
  login)
    rsync "${RSYNC_ARGS[@]}" "$REMOTE_PROJECT_DIR/logs/" "$LOCAL_PROJECT_DIR/logs/"
    case "$COLLECT_SCOPE" in
      wrf-run)
        sync_lightweight_output "$REMOTE_PROJECT_DIR/output/" "$LOCAL_PROJECT_DIR/output/"
        rsync "${RSYNC_ARGS[@]}" \
          --include='wrfinput_d*' \
          --include='wrfbdy_d01' \
          --include='namelist.output' \
          --exclude='*' \
          "$REMOTE_PROJECT_DIR/wrf/" "$LOCAL_PROJECT_DIR/wrf/"
        ;;
      wrf-wps)
        rsync_optional "${RSYNC_ARGS[@]}" \
          --include='met_em.d*.nc' \
          --include='geo_em.d*.nc' \
          --include='FILE:*' \
          --include='GRIBFILE.*' \
          --include='namelist.wps' \
          --exclude='*' \
          "$REMOTE_PROJECT_DIR/wps/" "$LOCAL_PROJECT_DIR/wps/"
        ;;
      all)
        rsync_optional "${RSYNC_ARGS[@]}" "$REMOTE_PROJECT_DIR/output/" "$LOCAL_PROJECT_DIR/output/"
        rsync "${RSYNC_ARGS[@]}" \
          --include='wrfinput_d*' \
          --include='wrfbdy_d01' \
          --include='namelist.output' \
          --exclude='*' \
          "$REMOTE_PROJECT_DIR/wrf/" "$LOCAL_PROJECT_DIR/wrf/"
        rsync_optional "${RSYNC_ARGS[@]}" \
          --include='met_em.d*.nc' \
          --include='geo_em.d*.nc' \
          --include='FILE:*' \
          --include='GRIBFILE.*' \
          --include='namelist.wps' \
          --exclude='*' \
          "$REMOTE_PROJECT_DIR/wps/" "$LOCAL_PROJECT_DIR/wps/"
        ;;
    esac
    ;;
  ssh)
    if [[ -z "$REMOTE_HOST" || "$REMOTE_HOST" == "-" ]]; then
      echo "collect_hpc.sh: ssh mode requires remote_host" >&2
      exit 2
    fi
    rsync "${RSYNC_ARGS[@]}" "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/logs/" "$LOCAL_PROJECT_DIR/logs/"
    case "$COLLECT_SCOPE" in
      wrf-run)
        sync_lightweight_output "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/output/" "$LOCAL_PROJECT_DIR/output/"
        rsync "${RSYNC_ARGS[@]}" \
          --include='wrfinput_d*' \
          --include='wrfbdy_d01' \
          --include='namelist.output' \
          --exclude='*' \
          "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/wrf/" "$LOCAL_PROJECT_DIR/wrf/"
        ;;
      wrf-wps)
        rsync_optional "${RSYNC_ARGS[@]}" \
          --include='met_em.d*.nc' \
          --include='geo_em.d*.nc' \
          --include='FILE:*' \
          --include='GRIBFILE.*' \
          --include='namelist.wps' \
          --exclude='*' \
          "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/wps/" "$LOCAL_PROJECT_DIR/wps/"
        ;;
      all)
        rsync_optional "${RSYNC_ARGS[@]}" "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/output/" "$LOCAL_PROJECT_DIR/output/"
        rsync "${RSYNC_ARGS[@]}" \
          --include='wrfinput_d*' \
          --include='wrfbdy_d01' \
          --include='namelist.output' \
          --exclude='*' \
          "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/wrf/" "$LOCAL_PROJECT_DIR/wrf/"
        rsync_optional "${RSYNC_ARGS[@]}" \
          --include='met_em.d*.nc' \
          --include='geo_em.d*.nc' \
          --include='FILE:*' \
          --include='GRIBFILE.*' \
          --include='namelist.wps' \
          --exclude='*' \
          "${REMOTE_HOST}:${REMOTE_PROJECT_DIR}/wps/" "$LOCAL_PROJECT_DIR/wps/"
        ;;
    esac
    ;;
  *)
    echo "collect_hpc.sh: unsupported access_mode=$ACCESS_MODE" >&2
    exit 2
    ;;
esac
