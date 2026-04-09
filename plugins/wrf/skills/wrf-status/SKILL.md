---
name: wrf-status
description: Inspect active or recent WRF task progress, summarize logs, and collect finished HPC outputs back into project state when needed.
---

# WRF Status

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-status/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-status/SKILL.md` first and use it as the canonical workflow.
2. Refresh state through repo-root `scripts/wrf_task.py status`, `logs`, and `collect`.
3. Treat `runs/<project>/project.json` as the source of truth for active task state.

## Files

- `.claude/skills/wrf-status/SKILL.md`
- `scripts/wrf_task.py`
- `scripts/project_state.py`
