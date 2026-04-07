---
name: wrf
description: Orchestrate the end-to-end WRF workflow from natural-language intent or structured research spec through init, config, async data/WPS/run tasks, runtime selection, and status follow-up.
---

# WRF Orchestrator

Use this skill when the user asks for an end-to-end WRF workflow from a natural-language request or a structured research configuration.

## Workflow

1. If the current directory does not already contain a compatible WRF workspace and the user wants one created, use `wrf-workspace-init` first to generate a workspace, then continue from that workspace root.
2. Create the project with `scripts/wrf_init.py` if it does not exist.
3. Treat `simulation_spec.json` as the authoritative scientific config. The preferred format is `schema_version=2` with `timing`, `execution`, `wps`, `model`, and `experimental`.
4. Use `scripts/wrf_config.py` to turn the request into `simulation_spec.json`, `namelist.wps`, and `namelist.input`.
5. For simple cases, use presets and `--request-text`. For research-grade cases, prefer `--spec-fragment-json` plus targeted `--override`.
6. If `run_mode=hpc`, rely on `wrf-config` admission before final config is written, and treat `project.json.execution.access_mode` as either `login` or `ssh`.
7. Runtime selection is config-driven:
   - Local WRF execution: `local.runtime.mode = project | custom_safe`
   - Local WPS execution: `local.wps_runtime.mode = project | custom_safe`
   - HPC WRF execution: `hpc.runtime.mode = project | remote_run_dir | custom`
   - HPC WPS execution: `hpc.wps_runtime.mode = project | remote_wps_dir | custom`
8. Start long steps through `scripts/wrf_task.py start`:
   - `wrf-data`
   - `wrf-wps`
   - `wrf-run`
9. For long-running work, return immediately after `start` unless the user explicitly wants blocking wait.
10. For follow-up questions such as "现在到哪了", use `scripts/wrf_task.py status` and `scripts/wrf_task.py logs`.
11. If an HPC run is terminal and remote outputs still need to be registered locally, use `scripts/wrf_task.py collect`.

## Local Runtime Notes

- Prefer `project` mode by default.
- Use `custom_safe` only when the user explicitly wants local runtime customization.
- `custom_safe` is a constrained template mode, not arbitrary local shell execution.
- Allowed shape: structured argv arrays only.
- Disallowed patterns: raw shell strings, `sh -c`, `bash -lc`, pipes, redirects, `&&`, `;`, `setup_commands`, `source`, `module load`, `conda activate`.
- The first command token must be a trusted executable path or an allowed launcher such as `mpirun`, `mpiexec`, or `srun`.
- Do not mutate local runtime config automatically unless the user explicitly asks for it.
- If local WPS fails on missing geography data, validate `geog_data_res` and the available `WPS_GEOG` dataset layout before changing runtime commands.

## Examples

Local end-to-end:
```bash
python3 scripts/wrf_init.py --project-name demo
python3 scripts/wrf_config.py --project-name demo --request-text "East China, GFS, 2024-07-20 00:00 to 2024-07-20 12:00, local" --run-mode local
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
python3 scripts/wrf_task.py start --project-name demo --step wrf-run
```

Research-grade local config:
```bash
python3 scripts/wrf_init.py --project-name demo
python3 scripts/wrf_config.py \
  --project-name demo \
  --domain-preset east_china \
  --domain-preset shanghai_inner \
  --physics-preset deep_convection \
  --spec-fragment-json /tmp/research_case.json \
  --override domains.1.physics.cu_physics=0 \
  --override domains.1.geog_data_res=default+30s \
  --override model.namelist_input.domains.parent_time_step_ratio=[1,3]
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
python3 scripts/wrf_task.py start --project-name demo --step wrf-run
```

Local `custom_safe` runtime config excerpt:
```json
{
  "local": {
    "default_np": 2,
    "runtime": {
      "mode": "custom_safe",
      "real_cmd": ["{real_exe}"],
      "wrf_cmd": ["mpirun", "-np", "{np}", "{wrf_exe}"]
    },
    "wps_runtime": {
      "mode": "custom_safe",
      "geogrid_cmd": ["{geogrid_exe}"],
      "link_grib_cmd": ["{link_grib_exe}", "{forcing_args}"],
      "ungrib_cmd": ["{ungrib_exe}"],
      "metgrid_cmd": ["{metgrid_exe}"]
    }
  }
}
```

HPC via login node: use a config whose `hpc.access_mode=login`. The generic example is `config/wrf_env.hpc.example.json`; copy it and fill in cluster-specific values.
```bash
python3 scripts/wrf_config.py --project-name demo --config config/wrf_env.json --run-mode hpc --spec-fragment-json /tmp/hpc_case.json
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps --config config/wrf_env.json
python3 scripts/wrf_task.py start --project-name demo --step wrf-run --config config/wrf_env.json
python3 scripts/wrf_task.py status --project-name demo --config config/wrf_env.json
```

HPC via SSH to a login node: use a config whose `hpc.access_mode=ssh`.
```bash
python3 scripts/wrf_task.py logs --project-name demo --config config/wrf_env.json --lines 80
python3 scripts/wrf_task.py collect --project-name demo --config config/wrf_env.json
```

## Rules

- Do not wait in the same AI turn for high-resolution or long-forecast WRF work unless the user explicitly asks to block.
- When the user needs high freedom, prefer structured spec updates over direct namelist editing.
- Use `--request-text` only for common intent extraction. Use `--spec-fragment-json` or explicit `--override` for advanced projection, per-domain physics, and custom sections such as `fdda`.
- Treat existing v1 specs as compatible input only. New accepted configs should be written back as structured v2.
- Treat `project.json.execution.active_task` as the source of truth for current progress.
- If `active_task.state` is `queued` or `running`, do not issue destructive config changes; report the conflict first.
- On HPC requests, do not bypass admission by directly calling scheduler commands.
- For local runtime customization, stay inside `custom_safe`; do not describe or generate arbitrary local shell command chains.

## Files

- `scripts/wrf_init.py`
- `scripts/spec_utils.py`
- `scripts/wrf_config.py`
- `scripts/wrf_task.py`
- `scripts/project_state.py`
- `runs/<project>/project.json`
