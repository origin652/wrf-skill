# WRF Skill Deployment Guide

`wrf-skill` is a deployment-friendly workflow layer for people using Claude Code or Codex with an existing WRF/WPS environment.
It helps the agent create projects, render configs, run WPS/WRF steps, and manage optional HPC submission without turning this repository into a full WRF distribution.

[中文说明 / Simplified Chinese](README.zh-CN.md)

## What "deploy" means here

This repository is not a WRF/WPS installer.
Deploying it means making this workflow layer visible to your agent:

- Claude Code: put this repository, or an installed bundle copy of it, inside a workspace that contains `.claude/skills/`.
- Codex: put this repository, or an installed bundle copy of it, inside a workspace that Codex can read and execute from.
- In both cases, you still provide your own WRF/WPS binaries, geog data, runtime tables, and cluster access.

## Before you deploy

Prepare these pieces first:

- Linux or WSL environment
- Python 3.10+
- compiled WRF and WPS already available on disk
- required `WPS_GEOG` data and runtime support files
- forcing data access appropriate for your workflow
- if you use HPC: scheduler access, login path, and site-specific runtime settings

This repository does not do these jobs for you:

- compile WRF or WPS
- attach arbitrary new data sources automatically
- discover hidden cluster policy or topology by itself
- run arbitrary local shell chains

## Deploy to Claude Code

Claude Code is the most direct deployment target because this repository already ships Claude-style skills under `.claude/skills/`.

### Option A: use the repository directly

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
```

If you need HPC mode, start from the example config:

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

Then open this workspace in Claude Code. Claude Code can discover the skills from `.claude/skills/`.

### Option B: deploy a clean bundle into another workspace

Create a distributable archive:

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
```

Extract it, then install it into the workspace you want Claude Code to use:

```bash
tar -xzf dist/wrf-skill-bundle.tar.gz
cd wrf-skill-bundle
python3 scripts/install_skill_bundle.py --target /path/to/claude-workspace
```

Use `--force` only if you intentionally want to overwrite bundled files already present in the target workspace.

## Deploy to Codex

This repository now ships a repo-local native Codex plugin under `plugins/wrf-skill/`.
It is a thin wrapper around the repository workflow scripts, and `.agents/plugins/marketplace.json` makes it discoverable as a local plugin in this workspace.

### Option A: use the repository directly

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
```

Open the repository in Codex. The local marketplace file points Codex at `plugins/wrf-skill/`.

### Option B: install the bundle into a clean workspace

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
tar -xzf dist/wrf-skill-bundle.tar.gz
cd wrf-skill-bundle
python3 scripts/install_skill_bundle.py --target /path/to/codex-workspace
```

After that, open the target workspace in Codex. The bundle includes the native plugin files as well as the workflow scripts, so Codex can use the plugin and still operate directly on workspace state.

The plugin is intentionally thin. It expects the same workspace to still contain:

- `scripts/`
- `config/`
- `templates/`
- `runs/`

After that, tell Codex which WRF step you want it to operate.
Practical examples:

- `Use scripts/wrf_init.py to create a project called demo.`
- `Use scripts/wrf_config.py to render a local WPS case from this request.`
- `Run only the WPS flow for runs/<project>.`

## What the bundle contains

The bundle is meant for redistribution. It includes the workflow layer only:

- `.agents/plugins/marketplace.json`
- `.claude/skills/`
- `plugins/wrf-skill/`
- `scripts/`
- `templates/`
- selected config presets and schema files
- lightweight WPS support files under `third_party/wps-support/`

The bundle intentionally excludes environment-specific or heavy assets:

- `config/wrf_env.json`
- `runs/` outputs
- compiled WRF/WPS trees
- full `WPS_GEOG`
- private SSH or scheduler credentials

## Minimum configuration after deployment

### Local runs

Use your existing local WRF/WPS installation.
The runtime configuration can point at project-local executables or a safe custom local runtime.

Important boundary:

- local customization is limited to `custom_safe`
- `custom_safe` accepts structured argv templates only
- raw shell strings, pipes, redirects, `bash -lc`, `source`, and similar shell chaining are intentionally blocked

### HPC runs

Start from the example file and make your site-specific copy:

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

Then fill in the details that this repository cannot guess for you, such as:

- scheduler type
- login node access mode
- remote run directories
- executable paths
- queue or account defaults
- any cluster-specific path conventions

## What the agent can actually see

After deployment, the agent can work only with the information and commands that exist in the current workspace and runtime environment.

It can usually:

- read project files such as `runs/<project>/project.json`
- read `config/wrf_env.json` if you provide it
- inspect logs generated by previous runs
- call local commands or HPC access paths that are already available in the session

It cannot automatically:

- see hidden cluster configuration that is not exposed through files or commands
- know real-time scheduler capacity unless the environment exposes that information
- install missing WRF/WPS dependencies for you
- turn arbitrary shell snippets into trusted runtime config

## First useful flow after deployment

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

## Repository scope

This repository is best treated as:

- a skill and workflow layer for Claude Code
- a native Codex plugin plus WRF workflow workspace
- a redistribution-friendly bundle for users who need the same orchestration logic without your private environment

It is not a replacement for a real WRF/WPS installation.

## Third-party files and license

Lightweight WPS support tables are documented in [THIRD_PARTY.md](THIRD_PARTY.md).
Project-authored files are released under [Apache-2.0](LICENSE).
