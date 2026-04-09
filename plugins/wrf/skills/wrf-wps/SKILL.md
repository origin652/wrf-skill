---
name: wrf-wps
description: Run or plan WPS preprocessing for a prepared WRF project through the async task layer, including geogrid, ungrib, metgrid, and per-step logging.
---

# WRF WPS

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-wps/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-wps/SKILL.md` first and follow it as the authoritative workflow.
2. Use repo-root `scripts/wrf_task.py start --step wrf-wps` for execution.
3. Treat `runs/<project>/project.json` and `logs/` as the persisted WPS state.

## Files

- `.claude/skills/wrf-wps/SKILL.md`
- `scripts/wrf_task.py`
- `scripts/wrf_wps.py`
