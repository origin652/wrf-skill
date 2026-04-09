---
name: wrf-workspace-init
description: Create a fresh WRF workspace in any target directory by unpacking the bundled minimal workspace template, writing a ready marker, and returning the absolute path for the next Codex or Claude session.
---

# WRF Workspace Init

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-workspace-init/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-workspace-init/SKILL.md` first and use it as the canonical workflow.
2. Run the canonical script at `.claude/skills/wrf-workspace-init/scripts/init_workspace.sh`.
3. Treat the canonical skill directory, not `plugins/wrf/`, as the implementation root for workspace bootstrap assets.

## Files

- `.claude/skills/wrf-workspace-init/SKILL.md`
- `.claude/skills/wrf-workspace-init/scripts/init_workspace.sh`
