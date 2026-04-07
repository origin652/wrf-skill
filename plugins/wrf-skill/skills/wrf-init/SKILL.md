---
name: wrf-init
description: Initialize a WRF project in this workspace, validate the environment, seed templates, and create runs/<project>/project.json for later workflow steps.
---

# WRF Init

Use this skill when the user wants to create a new WRF run directory or validate the execution environment before configuration.

## Workflow

1. Read `config/wrf_env.json` when present.
2. If not in dry-run, run `scripts/check_env.sh`.
3. Let `check_env.sh` validate filesystem paths, core WRF/WPS executables, support files, and local runtime config sections.
4. Create `runs/<project>/{data,wps,wrf,output,logs}`.
5. Seed `project.json`, `simulation_spec.json`, and namelist templates.
6. Set project status to `created` or `env_checked`.

## Notes

- Local runtime validation is config-driven:
  - `local.runtime.mode = project | custom_safe`
  - `local.wps_runtime.mode = project | custom_safe`
- `custom_safe` accepts structured argv templates only.
- Do not describe or generate arbitrary shell execution for local runtime config.
- If local WPS fails in `geogrid` with missing geography indexes, check `geog_data_res` against the datasets actually present under `WPS_GEOG`.

## Files

- `scripts/project_state.py`
- `scripts/wrf_init.py`
- `scripts/check_env.sh`
- `templates/project.json.template`
- `templates/namelist.wps.template`
- `templates/namelist.input.template`
