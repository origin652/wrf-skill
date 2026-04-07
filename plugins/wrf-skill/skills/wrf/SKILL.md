---
name: wrf
description: Umbrella entrypoint for the native Codex WRF plugin. Route requests to project init, config, WPS, run, or status workflows instead of keeping all behavior in one skill.
---

# WRF

## Overview

Use this skill as the umbrella entrypoint for WRF work in the native Codex plugin.

This skill should classify the request quickly, then route to the narrower workflow instead of keeping all WRF logic in one place.

## Routing Rules

1. Resolve the operating context first:
   - identify the project name if the user gave one
   - inspect `runs/<project>/project.json` when project state matters
   - read `config/wrf_env.json` only when runtime or HPC routing matters
2. Classify the request before taking action:
   - project creation or environment validation: `../wrf-init/SKILL.md`
   - config rendering or updates: `../wrf-config/SKILL.md`
   - WPS preprocessing: `../wrf-wps/SKILL.md`
   - WRF execution: `../wrf-run/SKILL.md`
   - progress, logs, cancel, or collect: `../wrf-status/SKILL.md`
3. Route immediately once the category is clear.
4. If the user asks for an end-to-end flow, keep this skill as the umbrella and invoke the steps in order: init -> config -> data/WPS -> run -> status.

## Workspace Assumptions

- This plugin is intentionally thin and expects the workspace to still contain:
  - `scripts/`
  - `config/`
  - `templates/`
  - `runs/`
- WRF/WPS binaries, geog data, runtime tables, and cluster access come from the user environment.
- Prefer repository scripts under `scripts/` over ad hoc shell orchestration.

## Runtime Boundary

- Local customization stays inside `custom_safe`.
- Do not treat `custom_safe` as arbitrary shell execution.
- HPC visibility is limited to files, commands, and access paths already exposed in the session.
- Do not infer hidden cluster policy or hidden scheduler state.

## Examples

- "Create a new WRF project for this case."
- "Update the config for a local East China GFS run."
- "Run only the WPS flow."
- "Start the model run and show me status later."
- "Check the current HPC task and summarize the log tail."
