---
name: wrf-init
description: Initialize a Linux or WSL WRF project, check the environment, seed templates, and create runs/<project>/project.json for later WRF workflow steps.
---

# WRF Init

Use this skill when a user wants to create a new WRF run directory or validate the execution environment before configuration.

## Workflow

1. Read `config/wrf_env.json`.
2. If not in `dry-run`, run `scripts/check_env.sh`.
3. Create `runs/<project>/{data,wps,wrf,output,logs}`.
4. Seed `project.json`, `simulation_spec.json`, and namelist templates.
5. Set the project status to `created` or `env_checked`.

## Files

- `scripts/project_state.py`
- `scripts/wrf_init.py`
- `scripts/check_env.sh`
- `templates/project.json.template`
- `templates/namelist.wps.template`
- `templates/namelist.input.template`
