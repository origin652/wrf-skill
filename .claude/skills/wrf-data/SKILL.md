---
name: wrf-data
description: Prepare forcing data for a WRF project through the async task layer, with the MVP focused on GFS download planning, manifest generation, and project state updates.
---

# WRF Data

Use this skill when the user needs forcing data for a configured project.

## Workflow

1. Read `runs/<project>/simulation_spec.json` and `project.json`.
2. Use `scripts/wrf_task.py start --step wrf-data` as the default user-facing entry point.
3. For implementation details, let the worker call `scripts/wrf_data.py`.
4. Write or update `data_manifest.json` and the retry shell script in the project data directory.
5. Register forcing files and manifest in `project.json` when the worker finishes.
6. Use `scripts/wrf_task.py status` and `logs` for progress follow-up instead of keeping the same AI turn blocked.

## Examples

Start the async data task:
```bash
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
```

Tune downloader worker settings:
```bash
python3 scripts/wrf_task.py start \
  --project-name demo \
  --step wrf-data \
  --task-kwargs-json '{"max_workers": 4}'
```

Check progress and logs:
```bash
python3 scripts/wrf_task.py status --project-name demo
python3 scripts/wrf_task.py logs --project-name demo --lines 80
```

## Notes

- In the current scaffold, support only `gfs`.
- Non-`dry-run` execution downloads automatically, writes `data_manifest.json`, inventories local GRIB files, and also writes a download shell script for retry/debugging.
- During async execution, `runs/<project>/logs/wrf-data.log` is updated incrementally with per-file progress lines, and `status` surfaces the latest progress line through `active_task.last_progress`.
- The project moves to `data_ready` only when all planned files are present locally after the task completes.
- The active task summary is stored in `project.json.execution.active_task`.

## Files

- `scripts/wrf_task.py`
- `scripts/wrf_data.py`
- `scripts/download_gfs.py`
- `runs/<project>/project.json`
