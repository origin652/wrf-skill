---
name: wrf-post
description: Post-process WRF output files, extract fields, and generate diagnostic plots into runs/<project>/output/plots/.
---

# WRF Post

This plugin skill is a thin wrapper that exposes the canonical workspace skill at `.claude/skills/wrf-post/SKILL.md`.

## Workflow

1. Open `.claude/skills/wrf-post/SKILL.md` first and use it as the canonical workflow.
2. Use repo-root `scripts/post_spec.py`, `scripts/wrf_post.py`, and `scripts/plot_wrfout.py`.
3. Treat `post_spec.json`, `runs/<project>/output/plots/`, and `project.json.artifacts.plots` as the persisted post-processing state.

## Files

- `.claude/skills/wrf-post/SKILL.md`
- `scripts/post_spec.py`
- `scripts/wrf_post.py`
- `scripts/plot_wrfout.py`
