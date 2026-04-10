---
name: wrf-run
description: Execute WRF through the async task layer, using local detached workers for local mode and scheduler adapters plus collect/sync hooks for HPC mode.
---

# WRF Run

Use this skill when WPS output exists and the user wants to execute the model.

## Workflow

1. Read `project.json` and determine `execution.mode`.
2. Use `scripts/wrf.py run` as the default user-facing entry point.
3. In local mode, the task layer starts a detached worker and the worker calls `scripts/wrf_run.py`.
4. In HPC mode, the task layer performs `admission -> sync -> adapter.render_job -> adapter.submit -> status/query -> collect`. `execution.access_mode=login` means scheduler commands run on the current login node; `execution.access_mode=ssh` means the task layer SSHes to the login node before calling the scheduler.
5. The actual WRF runtime is selected entirely from `hpc.runtime`: `project`, `remote_run_dir`, or `custom`.
6. Use `scripts/wrf.py status` and `scripts/wrf.py logs` to report queued/running/completed/failed/canceled.
7. When an HPC run is terminal, use `scripts/wrf.py collect` to pull logs and outputs back into local project state.

## Examples

Start a local run:
```bash
python3 scripts/wrf.py run --project-name demo
```

Start an HPC run through the login node: use a config whose `hpc.access_mode=login`.
```bash
python3 scripts/wrf.py run --project-name demo --config config/wrf_env.json
python3 scripts/wrf.py status --project-name demo --config config/wrf_env.json
```

Start an HPC run through SSH to the login node: use a config whose `hpc.access_mode=ssh`.
```bash
python3 scripts/wrf.py logs --project-name demo --lines 100
python3 scripts/wrf.py collect --project-name demo --config config/wrf_env.json
```

## Notes

- `scripts/wrf_run.py` is now the local synchronous execution engine, not the user-facing long-task controller.
- Do not call scheduler commands directly from the skill; go through `scripts/wrf_task.py` and the adapter layer.
- `run_mode=local` stays outside the scheduler path entirely; only `run_mode=hpc` uses `login` or `ssh` access modes.
- Prefer file-based runtime setup under `hpc.runtime.setup_commands`, `hpc.runtime.modules`, `hpc.runtime.launcher_cmd`, `hpc.runtime.tasks_flag`, and either `hpc.runtime.remote_run_dir` or custom commands.
- HPC state normalization comes from `scripts/hpc/` adapters, not from ad hoc parsing in the skill.
- If a run is already active, do not mutate upstream config or inputs in the same turn.

## Files

- `scripts/wrf.py`
- `scripts/wrf_task.py`
- `scripts/wrf_run.py`
- `scripts/hpc/base.py`
- `scripts/hpc/slurm.py`
- `scripts/hpc/pbs.py`
- `scripts/sync_hpc.sh`
- `scripts/collect_hpc.sh`
- `templates/slurm_wrf.sh.template`
- `templates/pbs_wrf.sh.template`
