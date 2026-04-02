---
name: wrf-post
description: Post-process WRF output files, extract fields, and generate diagnostic plots into runs/<project>/output/plots/.
---

# WRF Post

Use this skill when the user wants diagnostics or graphics from finished WRF output.

## Workflow

1. Read `project.json` to locate `wrfout` artifacts.
2. Use `scripts/plot_wrfout.py` for supported plot types.
3. Save generated images to `runs/<project>/output/plots/`.
4. Record new artifacts in `project.json`.

