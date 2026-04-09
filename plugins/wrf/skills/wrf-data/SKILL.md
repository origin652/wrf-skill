---
name: wrf-data
description: Prepare forcing data for a WRF project through the async task layer, with support for GFS planning, manifest generation, project state updates, and reuse of local file mirrors or already-present data.
---

# WRF Data

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-data/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-data/SKILL.md` first and follow it as the authoritative workflow.
2. Use repo-root `scripts/wrf_task.py start --step wrf-data` as the normal entry point.
3. Keep data artifacts and manifest updates under `runs/<project>/`.

## Files

- `.claude/skills/wrf-data/SKILL.md`
- `scripts/wrf_task.py`
- `scripts/wrf_data.py`
- `scripts/download_gfs.py`
