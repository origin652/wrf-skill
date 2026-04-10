---
name: wrf-status
description: Inspect active or recent WRF task progress, summarize logs, and collect finished HPC outputs back into project state when needed.
---

# WRF Status

Use this skill when the user asks things like “现在到哪了”, “还在跑吗”, “看一下日志”, or “把 HPC 结果收回来”.

## Workflow

1. Read `runs/<project>/project.json`.
2. Look at `project.json.execution.active_task` first, then `last_task` if there is no active task.
3. Use `scripts/wrf.py status` to refresh live state.
4. If the user wants logs, use `scripts/wrf.py logs` and summarize the useful tail.
5. If the task is an HPC task in terminal state and outputs are not yet reflected locally, use `scripts/wrf.py collect`.
6. Report:
   - execution mode (`local`, or `hpc` with `login` / `ssh` access)
   - task step
   - backend
   - state
   - submitted/started/finished times
   - last progress
   - relevant log path

## Examples

Refresh the current task state:
```bash
python3 scripts/wrf.py status --project-name demo
```

Read the latest useful log tail:
```bash
python3 scripts/wrf.py logs --project-name demo --lines 100
```

Cancel an active task:
```bash
python3 scripts/wrf.py cancel --project-name demo --task-id <task-id>
```

Collect finished HPC outputs back into project state:
```bash
python3 scripts/wrf.py collect --project-name demo --config config/wrf_env.json
```

## Notes

- For local tasks, the task layer resolves progress from PID / exit code / local products.
- For HPC tasks, the task layer resolves progress through the scheduler adapter `query()` result.
- Use `project.json.execution.access_mode` to explain whether progress is coming from login-node-local scheduler access or SSH-mediated scheduler access.
- Do not guess progress from stale logs alone when `status` is available.
- If `active_task.state` is `queued` or `running`, warn that config-changing steps should not be started concurrently.

## Files

- `scripts/wrf.py`
- `scripts/wrf_task.py`
- `scripts/project_state.py`
- `runs/<project>/project.json`
