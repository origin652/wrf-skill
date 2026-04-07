---
name: wrf-wps
description: Run or plan WPS preprocessing for a prepared WRF project through the async task layer, including geogrid, ungrib, metgrid, and per-step logging.
---

# WRF WPS

Use this skill when forcing data and namelists are ready and the user wants WPS preprocessing.

## Workflow

1. Read `runs/<project>/project.json` to confirm the project is at least configured and has data inputs ready or partially ready.
2. Use `scripts/wrf_task.py start --step wrf-wps` as the default user-facing entry point.
3. In local mode the detached worker executes `scripts/wrf_wps.py`.
4. In HPC mode the task layer runs admission, sync, job render, submit, query, and collect.
5. Save logs under `runs/<project>/logs/` and task logs under `runs/<project>/tasks/<task-id>/`.
6. Register `met_em*` artifacts and move the project to `wps_ready` when complete.
7. Use status and logs to report progress in later turns.

## Notes

- WPS HPC runtime is selected from `hpc.wps_runtime`.
- Do not keep the AI turn waiting for large WPS runs by default.
- Treat `project.json.execution.active_task` as the current progress source.
- For local runtime customization, stay within `custom_safe` and structured argv templates.

## Files

- `scripts/wrf_task.py`
- `scripts/wrf_wps.py`
- `runs/<project>/project.json`
