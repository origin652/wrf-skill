---
name: wrf
description: Orchestrate the end-to-end WRF workflow from natural-language intent or structured research spec through init, config, async data/WPS/run tasks, runtime selection, and status follow-up.
---

# WRF Orchestrator

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf/SKILL.md` first and use it as the single source of truth.
2. Run repo-root workflow commands from `scripts/`, not from inside `plugins/wrf/`.
3. Treat repo-root `config/`, `templates/`, and `runs/` as the active workspace state.

## Files

- `.claude/skills/wrf/SKILL.md`
- `scripts/wrf_init.py`
- `scripts/wrf_config.py`
- `scripts/wrf_task.py`
