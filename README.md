# WRF Skill for Claude Code and Codex

`wrf-skill` is a workflow layer for people who already have a usable WRF/WPS environment and want Claude Code or Codex to help operate it.
It is not a WRF/WPS installer, compiler, or full distribution.

[中文说明 / Simplified Chinese](README.zh-CN.md)

## What This Repository Does

This repository gives the agent a reusable WRF workflow:

- initialize a project
- render `simulation_spec.json`, `namelist.wps`, and `namelist.input`
- run data, WPS, and WRF steps
- inspect logs and status
- support local and optional HPC workflows

This repository does not:

- compile WRF or WPS for you
- download arbitrary new data sources automatically
- ship full `WPS_GEOG`
- discover hidden HPC policy by itself
- allow arbitrary local shell chains

## What You Still Need

Before using this repo, prepare these pieces yourself:

- Linux or WSL
- Python 3.10+
- compiled WRF and WPS already available on disk
- required `WPS_GEOG` data and support files
- your own forcing-data access path
- if using HPC: scheduler access, login path, and site-specific runtime settings

## Recommended Paths

### Claude Code

This repo already ships Claude-style skills under `.claude/skills/`.
The simplest path is to open the repository directly in Claude Code.

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
```

If you need HPC mode, start from the example config:

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

Then open this repository in Claude Code.

### Codex

For Codex, the recommended path is now skill-based, not plugin-first.
Install the WRF skills into Codex, then use `wrf-workspace-init` to create a clean working workspace anywhere.

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
bash scripts/install_codex_skills.sh
```

If Codex is already open, start a new window or new session after installation so it reloads the skill list.

Then ask Codex to create a workspace, for example:

- `Use wrf-workspace-init to create a workspace at /path/to/my-wrf-workspace.`
- `Use wrf-workspace-init to create a WRF workspace in the current directory.`

If you want to run the bundled workspace-init script directly:

```bash
bash ~/.codex/skills/wrf-workspace-init/scripts/init_workspace.sh \
  --target-root /path/to/my-wrf-workspace
```

After that, open the generated workspace path in Codex and continue the actual WRF work there.

## What `wrf-workspace-init` Creates

The generated workspace is a portable minimal working tree for this repository's workflow layer.
It includes:

- `.claude/skills/`
- `config/`
- `scripts/`
- `templates/`
- `third_party/wps-support/`
- `runs/.gitkeep`

It intentionally excludes:

- private `config/wrf_env.json`
- compiled WRF/WPS trees
- full `WPS_GEOG`
- existing run outputs
- private SSH or scheduler credentials

If you need HPC mode inside the generated workspace:

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

Then fill in your site-specific values.

## Bundle Deployment

If you need to hand this workflow to someone else, you can still build a redistribution bundle:

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
```

Install the extracted bundle into another workspace:

```bash
tar -xzf dist/wrf-skill-bundle.tar.gz
cd wrf-skill-bundle
python3 scripts/install_skill_bundle.py --target /path/to/workspace
```

Use `--force` only when you intentionally want to overwrite the bundled files already present in the target workspace.

## Runtime Boundaries

### Local runtime

Local customization is intentionally constrained.
Use `custom_safe` only when you explicitly need local runtime customization.

Important boundaries:

- only structured argv templates are allowed
- raw shell strings are not allowed
- `bash -lc`, `sh -c`, pipes, redirects, `&&`, `;`, `source`, `module load`, and similar shell chaining are intentionally blocked

### HPC runtime

The agent can work with HPC settings only if you expose them through files and commands that already exist in the environment.
That means it can usually:

- read `config/wrf_env.json`
- inspect project state and logs
- use scheduler access that is already available in the current session

It cannot automatically:

- infer hidden cluster policy
- see real-time capacity unless the environment exposes it
- install missing dependencies for you

## First Useful Flow

A minimal local workflow looks like this:

```bash
python3 scripts/wrf_init.py --project-name demo
python3 scripts/wrf_config.py \
  --project-name demo \
  --request-text "East China, GFS, 2024-07-20 00:00 to 2024-07-20 12:00, local" \
  --run-mode local
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
python3 scripts/wrf_task.py start --project-name demo --step wrf-run
```

If you only want preprocessing first, stop after `wrf-wps`.

## Legacy Plugin Path

This repository still contains Codex plugin-related files and compatibility scripts.
They are no longer the recommended deployment path.
If you are starting fresh, use:

- `bash scripts/install_codex_skills.sh`
- `wrf-workspace-init`
- a generated workspace opened directly in Codex

## Scope

This repository is best understood as:

- a Claude Code skill workspace
- a Codex skill bundle plus workspace bootstrapper
- a redistribution-friendly WRF workflow layer

It is not a replacement for a real WRF/WPS installation.

## Third-Party Files and License

Lightweight WPS support files are documented in [THIRD_PARTY.md](THIRD_PARTY.md).
Project-authored files are released under [Apache-2.0](LICENSE).
