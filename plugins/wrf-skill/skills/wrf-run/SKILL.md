---
name: wrf-run
description: Execute WRF through the async task layer, using local detached workers for local mode and scheduler adapters plus collect/sync hooks for HPC mode.
---

# WRF Run

Use this skill when WPS output exists and the user wants to execute the model.

## Workflow

1. Read `runs/<project>/project.json` and determine `execution.mode`.
2. Use `scripts/wrf_task.py start --step wrf-run` as the default user-facing entry point.
3. In local mode, the task layer starts a detached worker and the worker calls `scripts/wrf_run.py`.
4. In HPC mode, the task layer performs admission, sync, job render, submit, query, and collect.
5. Runtime choice comes from config: local `runtime.mode` or HPC `hpc.runtime`.
6. Use `scripts/wrf_task.py status` and logs to report queued, running, completed, failed, or canceled state.
7. When an HPC run is terminal, use `scripts/wrf_task.py collect` to pull logs and outputs back into local project state.

## Notes

- `scripts/wrf_run.py` is the local synchronous execution engine, not the user-facing long-task controller.
- Do not call scheduler commands directly from the skill unless the user explicitly asks for scheduler debugging.
- If a run is already active, do not mutate upstream config or inputs in the same turn.
- For local runtime customization, stay within `custom_safe` and structured argv templates.

## Files

- `scripts/wrf_task.py`
- `scripts/wrf_run.py`
- `scripts/hpc/base.py`
- `scripts/hpc/slurm.py`
- `scripts/hpc/pbs.py`
- `scripts/sync_hpc.sh`
- `scripts/collect_hpc.sh`
