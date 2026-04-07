---
name: wrf-config
description: Convert natural-language or structured WRF requirements into simulation_spec.json, validate them, render namelists, and gate HPC requests before final config is written.
---

# WRF Config

Use this skill when the user wants to define or update domains, timing, physics settings, advanced namelist sections, or execution mode for a project.

## Workflow

1. Read `runs/<project>/project.json` and `simulation_spec.json`.
2. If `project.json.execution.active_task.state` is `queued` or `running`, do not mutate config.
3. Normalize the current spec to the structured v2 view before making decisions.
4. For simple requests, prefer presets and `--request-text`.
5. For research-grade requests, prefer `--spec-fragment-json` plus targeted `--override`.
6. Validate through `scripts/render_config.py`.
7. If `run_mode=hpc`, let `scripts/wrf_config.py` run admission before writing final config.
8. If accepted, write `simulation_spec.json`, `namelist.wps`, and `namelist.input`, then reset downstream artifact registration.

## Notes

- Prefer structured spec updates over direct namelist editing.
- Use `request-text` for common timing, domain, source, and local/HPC intent.
- Use `--spec-fragment-json` for complex `timing`, `wps`, `model.namelist_input`, or `experimental` content.
- `--override` can target supported paths inside the structured spec tree, including per-domain physics and geog settings.
- Reconfiguration should clear downstream state such as `data_manifest`, `met_em_files`, `wrfinput_files`, and `wrfout_files`.

## Files

- `config/simulation_schema.json`
- `config/physics_schemes.json`
- `config/domains_presets.json`
- `scripts/spec_utils.py`
- `scripts/wrf_config.py`
- `scripts/render_config.py`
- `scripts/project_state.py`
