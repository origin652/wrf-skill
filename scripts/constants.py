"""Shared constants for WRF workflow scripts."""

from __future__ import annotations

# Task polling intervals (seconds)
LOCAL_POLL_INTERVAL_SECONDS = 0.5
HPC_QUEUED_POLL_INTERVAL_SECONDS = 15.0
HPC_RUNNING_POLL_INTERVAL_SECONDS = 10.0
HPC_DEFAULT_POLL_INTERVAL_SECONDS = 10.0

# Task states
TASK_STATES = (
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
)
BLOCKING_TASK_STATES = {"queued", "running"}
TERMINAL_TASK_STATES = {"completed", "failed", "canceled"}

# Project statuses
VALID_STATUSES = (
    "created",
    "env_checked",
    "configured",
    "data_ready",
    "wps_ready",
    "real_ready",
    "running",
    "completed",
    "failed",
)

# File I/O
TAIL_READ_BLOCK_SIZE = 8 * 1024

# Time formats
TIME_FORMAT = "%Y-%m-%d_%H:%M:%S"

# Host aliases
LOCAL_HOST_ALIASES = {"", "-", "local", "localhost", "127.0.0.1", "::1"}

# Task step names
TASK_STEPS = {"wrf-data", "wrf-wps", "wrf-run"}

# Multi-domain namelist keys
MULTI_DOMAIN_NAMELIST_KEYS = {
    "time_control": {"history_interval", "frames_per_outfile"},
    "domains": {"e_vert", "parent_time_step_ratio"},
    "physics": {
        "mp_physics",
        "cu_physics",
        "ra_lw_physics",
        "ra_sw_physics",
        "bl_pbl_physics",
        "sf_sfclay_physics",
        "sf_surface_physics",
    },
}

# Data source mappings
TEXT_TO_SOURCE = {"gfs": "gfs", "era5": "era5", "fnl": "fnl"}
TEXT_TO_MODE = {
    "local": "local",
    "wsl": "local",
    "本地": "local",
    "单机": "local",
    "hpc": "hpc",
    "slurm": "hpc",
    "pbs": "hpc",
    "cluster": "hpc",
    "集群": "hpc",
}
