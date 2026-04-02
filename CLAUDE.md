# WRF Skill Workspace

This repository contains a Linux/WSL-only WRF skill scaffold.

## Core rules

- Treat `runs/<project>/project.json` as the single source of truth for project state.
- Prefer `dry-run` before any destructive or expensive execution step.
- Keep skill behavior idempotent where possible so failed runs can resume safely.
- Do not assume Windows-native WRF/WPS support. Real execution happens in Linux or WSL.
- Record logs under `runs/<project>/logs/` and keep error reporting machine-readable.

## Execution model

- `wrf-init` seeds a project and validates the environment.
- `wrf-config` converts user intent into `simulation_spec.json`, then renders namelists.
- `wrf-data` downloads or plans forcing data.
- `wrf-wps` runs WPS preprocessing.
- `wrf-run` runs `real.exe` and `wrf.exe` locally or renders/submits HPC jobs.
- `wrf-post` performs plotting and post-processing.
- `wrf` orchestrates the full flow through project state transitions.

