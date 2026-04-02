---
name: wrf-wps
description: Run or plan WPS preprocessing for a prepared WRF project through the async task layer, including geogrid, ungrib, metgrid, and per-step logging.
---

# WRF WPS

Use this skill when forcing data and namelists are ready and the user wants WPS preprocessing.

## Workflow

1. Read `project.json` to confirm the project is at least `configured` and has data inputs ready or partially ready.
2. Use `scripts/wrf_task.py start --step wrf-wps` as the default user-facing entry point.
3. In local mode the detached worker executes `scripts/wrf_wps.py`; in HPC mode the task layer runs `admission -> sync -> adapter.render_job -> adapter.submit -> status/query -> collect`.
4. Save logs under `runs/<project>/logs/` and task logs under `runs/<project>/tasks/<task-id>/`.
5. Register `met_em*` artifacts and move the project to `wps_ready` when complete.
6. Use `status` and `logs` to report progress in later turns.

## Examples

Start WPS asynchronously:
```bash
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
```

For a small test case, block until WPS finishes:
```bash
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps --wait
```

Inspect WPS progress:
```bash
python3 scripts/wrf_task.py status --project-name demo
python3 scripts/wrf_task.py logs --project-name demo --lines 120
```

## Notes

- WPS HPC runtime is selected from `hpc.wps_runtime`: use `project` for synced project-local binaries, `remote_wps_dir` for cluster-installed WPS, or `custom` for explicit `geogrid_cmd` / `link_grib_cmd` / `ungrib_cmd` / `metgrid_cmd`.
- Do not keep the AI turn waiting for large WPS runs by default.
- Treat `project.json.execution.active_task` as the current progress source.

## Files

- `scripts/wrf_task.py`
- `scripts/wrf_wps.py`
- `runs/<project>/project.json`
