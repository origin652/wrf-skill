---
name: wrf-config
description: Convert natural-language and structured research-grade WRF requirements into `schema_version=2` `simulation_spec.json`, validate them, render namelists, and gate HPC requests with admission before final config is written.
---

# WRF Config

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-config/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-config/SKILL.md` first and use it as the canonical workflow.
2. Apply changes through repo-root `scripts/wrf_config.py` and `scripts/render_config.py`.
3. Treat `runs/<project>/simulation_spec.json` and `project.json` as the persisted config state.

## Files

- `.claude/skills/wrf-config/SKILL.md`
- `scripts/wrf_config.py`
- `scripts/render_config.py`
- `config/simulation_schema.json`
