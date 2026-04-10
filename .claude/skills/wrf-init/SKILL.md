---
name: wrf-init
description: Initialize a Linux or WSL WRF project, validate the environment and local runtime config, seed templates, and create runs/<project>/project.json for later WRF workflow steps.
---

# WRF Init

Use this skill when a user wants to create a new WRF run directory or validate the execution environment before configuration.

## Workflow

1. Read `config/wrf_env.json`.
2. Prefer `scripts/wrf.py init` as the public entry point. `scripts/wrf_init.py` remains the implementation and compatibility entry.
3. If not in `dry-run`, run `scripts/check_env.sh`.
4. Let `check_env.sh` validate filesystem paths, core WRF/WPS executables, support files, and local runtime config sections.
5. Create `runs/<project>/{data,wps,wrf,output,logs}`.
6. Seed `project.json`, `simulation_spec.json`, and namelist templates.
7. Set the project status to `created` or `env_checked`.

## Notes

- Local runtime validation is config-driven:
  - `local.runtime.mode = project | custom_safe`
  - `local.wps_runtime.mode = project | custom_safe`
- `custom_safe` accepts only structured argv templates. Do not treat it as arbitrary shell execution.
- `check_env.sh` validates `custom_safe` sections before project creation and only requires external launcher commands that the current config actually references.
- `check_env.sh` no longer hard-requires `mpirun` when the selected local runtime does not need it.
- If local WPS fails in `geogrid` with missing geography indexes, check `geog_data_res` against the datasets actually present under `WPS_GEOG`.

## Files

- `scripts/wrf.py`
- `scripts/project_state.py`
- `scripts/wrf_init.py`
- `scripts/check_env.sh`
- `templates/project.json.template`
- `templates/namelist.wps.template`
- `templates/namelist.input.template`
