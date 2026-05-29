---
name: wrf-config
description: Convert natural-language and structured research-grade WRF requirements into `schema_version=2` `simulation_spec.json`, validate them, render namelists, import or improve existing namelists through structured specs, and gate HPC requests with admission before final config is written.
---

# WRF Config

Use this skill when the user wants to define or update domains, timing, physics settings, advanced namelist sections, or execution mode for a project. Execution semantics are: `run_mode=local` for local direct execution, `run_mode=hpc` + `execution.access_mode=login` when the current machine is the login node, and `run_mode=hpc` + `execution.access_mode=ssh` when the current machine must SSH to a login node before calling the scheduler.

`simulation_spec.json` is now structure-first. The canonical shape is `schema_version=2` with nested `timing`, `execution`, `wps`, `model`, and `experimental`. Older v1 specs are auto-normalized on load, then written back as v2 when config is accepted.

## Workflow

1. Read `runs/<project>/project.json` and `simulation_spec.json`.
2. If `project.json.execution.active_task.state` is `queued` or `running`, do not mutate config.
3. Normalize the current spec to the v2 structured view before making decisions.
4. Prefer `scripts/wrf.py config` as the public entry point. `scripts/wrf_config.py` remains the implementation and compatibility entry.
5. For existing manual WRF cases, use `scripts/wrf.py import-namelists` to convert `namelist.input` and `namelist.wps` into an AI-readable `schema_version=2` spec before editing, or `scripts/wrf.py improve-namelists` to import, modify, validate, and re-render in one step.
6. For simple requests, use presets and `--request-text`. For research-grade requests, prefer `--spec-fragment-json` plus targeted `--override`.
7. Update the temporary spec from presets, request text, structured fragments, and supported override paths.
8. Use per-domain overrides when needed, for example `domains.<index>.physics.*` and `domains.<index>.geog_data_res`.
9. Validate through `scripts/render_config.py`. This now checks structured spec semantics and rendered namelist validity.
10. If `run_mode=hpc`, let the config layer run admission before writing final config.
11. If admission returns `rejected` or `unverified`, stop there and return the decision, reason codes, and alternatives.
12. If accepted, write `simulation_spec.json`, `namelist.wps`, and `namelist.input`, then reset downstream artifact registration.

## Examples

Preset-driven local config:
```bash
python3 scripts/wrf.py config \
  --project-name demo \
  --domain-preset east_china \
  --physics-preset tropical_cyclone \
  --start-time 2024-07-20_00:00:00 \
  --end-time 2024-07-20_12:00:00 \
  --data-source gfs \
  --run-mode local
```

Research-grade structured config with projection, per-domain physics, and an extra namelist section:
```bash
python3 scripts/wrf.py config \
  --project-name demo \
  --domain-preset east_china \
  --domain-preset shanghai_inner \
  --physics-preset deep_convection \
  --data-source gfs \
  --run-mode local \
  --spec-fragment-json /tmp/research_case.json \
  --override domains.0.physics.cu_physics=1 \
  --override domains.1.physics.cu_physics=0 \
  --override domains.1.geog_data_res=default+30s \
  --override model.namelist_input.domains.parent_time_step_ratio=[1,3]
```

Import existing namelists into a structured spec:
```bash
python3 scripts/wrf.py import-namelists \
  --project-name legacy_case \
  --namelist-input runs/legacy_case/wrf/namelist.input \
  --namelist-wps runs/legacy_case/wps/namelist.wps \
  --out runs/legacy_case/simulation_spec.json
```

Improve existing namelists with structured and direct overrides:
```bash
python3 scripts/wrf.py improve-namelists \
  --project-name legacy_case \
  --namelist-input runs/legacy_case/wrf/namelist.input \
  --namelist-wps runs/legacy_case/wps/namelist.wps \
  --override timing.history_interval_minutes=30 \
  --override physics.cu_physics=0 \
  --namelist-override dynamics.w_damping=1 \
  --out-dir runs/legacy_case/improved \
  --spec-out runs/legacy_case/improved/simulation_spec.json
```

Preview the exact namelist changes before writing:
```bash
python3 scripts/wrf.py improve-namelists \
  --project-name legacy_case \
  --namelist-input runs/legacy_case/wrf/namelist.input \
  --namelist-wps runs/legacy_case/wps/namelist.wps \
  --override timing.history_interval_minutes=30 \
  --dry-run \
  --diff
```

Safely improve an existing managed project in place:
```bash
python3 scripts/wrf.py improve-namelists \
  --project-name legacy_case \
  --runs-dir runs \
  --override timing.history_interval_minutes=30
```

HPC config preview without writing files yet:
```bash
python3 scripts/wrf.py config \
  --project-name demo \
  --config config/wrf_env.json \
  --request-text "East China, GFS, 12 hours" \
  --run-mode hpc \
  --dry-run
```

HPC config through the login node or SSH path: the selected config file must set `hpc.access_mode=login` or `hpc.access_mode=ssh`.
```bash
python3 scripts/wrf.py config \
  --project-name demo \
  --config config/wrf_env.json \
  --spec-fragment-json /tmp/hpc_case.json \
  --run-mode hpc
```

## Notes

- `import-namelists` infers timing, domains, nesting, physics, WPS projection fields, and data source where possible. It stores source snapshots in `experimental.imported_namelist_input` and `experimental.imported_namelist_wps` unless `--no-raw` is used; these snapshot fields are informational and do not override future rendering. Check `experimental.import_diagnostics` for inferred sources, defaulted fields, warnings, and unstructured fields carried forward under `model.namelist_input`.
- `improve-namelists` imports existing namelists, applies `--request-text`, presets, `--spec-fragment-json`, structured `--override`, and direct `--namelist-override`, validates the structured spec, then writes improved `namelist.input` and `namelist.wps` to `--out-dir`. Its JSON output includes `diagnostics` and a machine-readable `diff`; use `--diff` for a human-readable preview.
- Use `--runs-dir runs --project-name <project>` to improve a managed project in place. This reads `project.json`, refuses to mutate while a queued or running task exists, writes canonical namelists and `simulation_spec.json`, registers artifacts, resets downstream state, and writes `logs/wrf-improve-namelists.log`.
- Prefer preset-driven configuration first, then switch to structured v2 spec fields when the request becomes research-grade.
- Use `request-text` for common natural-language inputs such as time range, domain names, physics intent, and local/HPC mode. Do not expect it to infer detailed research settings.
- Use `--spec-fragment-json` for complex cases that need explicit `timing`, `wps`, `model.namelist_input`, or `experimental` content.
- `--override` is no longer limited to the old tiny whitelist. It can target any supported path inside the structured spec tree, including `timing.*`, `execution.*`, `wps.*`, `model.namelist_input.*`, and `domains.<index>.physics.*`.
- `wps.geogrid` now controls projection fields such as `map_proj`, `truelat1`, `truelat2`, `stand_lon`, `ref_lat`, `ref_lon`, and per-domain `geog_data_res`.
- `model.namelist_input.<section>` can render additional sections such as `fdda` without editing raw namelist files.
- `experimental.raw_namelist_input` and `experimental.raw_namelist_wps` are last-resort passthroughs. Use them sparingly and prefer structured fields first.
- `--namelist-override` is still available for small direct post-render tweaks, but it should not replace the structured spec.
- Reconfiguration now clears downstream state such as `data_manifest`, `met_em_files`, `wrfinput_files`, and `wrfout_files` to avoid mixed-product reuse.
- HPC admission facts are stored in `project.json.execution.last_admission`.
- Persisted execution routing is split between `project.json.execution.mode` (`local` or `hpc`) and `project.json.execution.access_mode` (`login` or `ssh` when `mode=hpc`).
- HPC runtime choice now belongs in the config file: `hpc.runtime` controls `wrf-run`, and `hpc.wps_runtime` controls `wrf-wps`; configure each with `project|remote_run_dir|custom` or `project|remote_wps_dir|custom` plus setup/modules/python_env as needed.

## Files

- `config/simulation_schema.json`
- `config/physics_schemes.json`
- `config/domains_presets.json`
- `scripts/wrf.py`
- `scripts/spec_utils.py`
- `scripts/wrf_config.py`
- `scripts/render_config.py`
- `scripts/namelist_to_spec.py`
- `scripts/project_state.py`
