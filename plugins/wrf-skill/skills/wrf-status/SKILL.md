---
name: wrf-status
description: Inspect active or recent WRF task progress, summarize logs, cancel tasks when requested, and collect finished HPC outputs back into project state when needed.
---

# WRF Status

Use this skill when the user asks for current progress, recent logs, cancellation, or HPC result collection.

## Workflow

1. Read `runs/<project>/project.json`.
2. Look at `project.json.execution.active_task` first, then `last_task` if there is no active task.
3. Use `scripts/wrf_task.py status` to refresh live state.
4. If the user wants logs, use `scripts/wrf_task.py logs` and summarize the useful tail.
5. If the user wants cancellation, use `scripts/wrf_task.py cancel`.
6. If the task is an HPC task in terminal state and outputs are not yet reflected locally, use `scripts/wrf_task.py collect`.
7. Report execution mode, step, backend, state, times, last progress, and relevant log path.

## Notes

- For local tasks, progress comes from PID, exit code, and local products.
- For HPC tasks, progress comes from the scheduler adapter query result.
- Do not guess progress from stale logs alone when `status` is available.
- If `active_task.state` is `queued` or `running`, warn that config-changing steps should not be started concurrently.

## Files

- `scripts/wrf_task.py`
- `scripts/project_state.py`
- `runs/<project>/project.json`
