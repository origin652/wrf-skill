---
name: wrf-run
description: Execute WRF through the async task layer, using local detached workers for local mode and scheduler adapters plus collect/sync hooks for HPC mode.
---

# WRF Run

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-run/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-run/SKILL.md` first and follow it as the authoritative workflow.
2. Prefer repo-root `scripts/wrf.py run` plus the related `status`, `logs`, and `collect` commands.
3. Keep execution state in `runs/<project>/project.json` and task logs under `runs/<project>/tasks/`.

## Files

- `.claude/skills/wrf-run/SKILL.md`
- `scripts/wrf.py`
- `scripts/wrf_task.py`
- `scripts/wrf_run.py`
- `scripts/hpc/`
