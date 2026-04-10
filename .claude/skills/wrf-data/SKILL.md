---
name: wrf-data
description: Prepare forcing data for a WRF project through the async task layer, with support for GFS, FNL, and ERA5 planning, manifest generation, project state updates, and reuse of local file mirrors or already-present data.
---

# WRF Data

Use this skill when the user needs forcing data for a configured project.

## Workflow

1. Read `runs/<project>/simulation_spec.json` and `project.json`.
2. Use `scripts/wrf_task.py start --step wrf-data` as the default user-facing entry point.
3. For implementation details, let the worker call `scripts/wrf_data.py`.
4. Prefer reusing local files when the user already has forcing data. `--base-url file:///...` is valid for local mirrors.
5. Write or update `data_manifest.json` and the retry shell script in the project data directory.
6. Register forcing files and manifest in `project.json` when the worker finishes.
7. Use `scripts/wrf_task.py status` and `logs` for progress follow-up instead of keeping the same AI turn blocked.

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

Use a local mirror instead of the public source:
```bash
python3 scripts/wrf_task.py start \
  --project-name demo \
  --step wrf-data \
  --task-kwargs-json '{"base_url": "file:///mnt/data/gfs-mirror", "max_workers": 1}'
```

Check progress and logs:
```bash
python3 scripts/wrf_task.py status --project-name demo
python3 scripts/wrf_task.py logs --project-name demo --lines 80
```

## Notes

- Built-in forcing handlers currently support `gfs`, `fnl`, and `era5`.
- `gfs` and `fnl` use direct URL/file-mirror downloads; `era5` supports CDS and local mirror flows.
- `fnl` expects 6-hour analysis spacing and start times aligned to `00/06/12/18` UTC.
- Non-`dry-run` execution downloads or copies files automatically, writes `data_manifest.json`, inventories local GRIB files, and also writes a download shell script for retry/debugging.
- Existing local files that already match the planned manifest are reused.
- During async execution, `runs/<project>/logs/wrf-data.log` is updated incrementally with per-file progress lines, and `status` surfaces the latest progress line through `active_task.last_progress`.
- The project moves to `data_ready` only when all planned files are present locally after the task completes.
- `data_ready` confirms forcing availability only. It does not validate WPS geography data or local runtime command config.
- The active task summary is stored in `project.json.execution.active_task`.

## Files

- `scripts/wrf_task.py`
- `scripts/wrf_data.py`
- `scripts/download_gfs.py`
- `scripts/download_fnl.py`
- `scripts/download_era5.py`
- `runs/<project>/project.json`
