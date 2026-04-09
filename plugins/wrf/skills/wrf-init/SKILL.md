---
name: wrf-init
description: Initialize a Linux or WSL WRF project, validate the environment and local runtime config, seed templates, and create runs/<project>/project.json for later WRF workflow steps.
---

# WRF Init

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-init/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-init/SKILL.md` first and follow it as the authoritative workflow.
2. Use repo-root `scripts/wrf_init.py` and `scripts/check_env.sh`.
3. Treat generated state under `runs/<project>/` as the source of truth.

## Files

- `.claude/skills/wrf-init/SKILL.md`
- `scripts/wrf_init.py`
- `scripts/check_env.sh`
- `templates/project.json.template`
