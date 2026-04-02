# wrf-skill

`wrf-skill` is a user-facing toolkit for running the WRF workflow with less manual glue code.
It helps you go from a plain-language or structured experiment request to a runnable WRF project, then drive long steps asynchronously on a local machine or an HPC cluster.

[中文说明 / Simplified Chinese](README.zh-CN.md)

## What This Is

This repository is not a packaged copy of WRF itself.
It is a workflow layer around WRF/WPS that gives you:

- project initialization from templates
- structured configuration generation for `simulation_spec.json`, `namelist.wps`, and `namelist.input`
- asynchronous task execution for `wrf-data`, `wrf-wps`, and `wrf-run`
- HPC admission checks before submission
- scheduler abstraction for Slurm and PBS
- status, log, cancel, and collect commands for long-running jobs
- a clean bundle format for redistributing the skill without private configs or large data

## Why Use It

Compared with manually stitching together WRF scripts and run directories, `wrf-skill` gives you a few practical advantages:

- Less manual setup: it creates project structure, renders configs, and tracks artifacts for you.
- Better control of long jobs: data download, WPS, and WRF runs use a consistent async task model.
- Safer HPC usage: requests go through admission before submission instead of blindly calling the scheduler.
- More flexible execution: the same workflow supports local runs, login-node runs, and SSH-to-login-node submission.
- Better reproducibility: project state is written to `runs/<project>/project.json` instead of being spread across ad hoc notes and shell history.
- Easier sharing: the bundle packager exports only the files needed to reuse the workflow.

## Installation

### Prerequisites

You need to provide your own WRF/WPS runtime environment. This repository does not ship compiled WRF/WPS binaries or full geog datasets.

Typical prerequisites are:

- Python 3.10+
- a working WRF/WPS installation prepared separately
- required runtime tables and geog data on your machine or cluster
- optional HPC access if you want scheduler-backed execution

### Option A: Use the repository directly

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
python3 -m unittest tests.test_skill_bundle
```

If you plan to use HPC mode, copy the example config and fill in your cluster-specific values:

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

### Option B: Install from the generated bundle

Create a distributable bundle:

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
```

Then extract it somewhere and install it into a target workspace:

```bash
python3 scripts/install_skill_bundle.py --target /path/to/install-root
```

The bundle intentionally excludes private configs, `runs/`, compiled trees, and large geog data.

## Third-Party Files

This repository contains a small set of WPS support tables under `third_party/wps-support/`. See [THIRD_PARTY.md](THIRD_PARTY.md) for provenance and publishing notes.

## License

Project-authored files in this repository are released under [Apache-2.0](LICENSE). Third-party support files are documented separately in [THIRD_PARTY.md](THIRD_PARTY.md).

## Basic Usage

### 1. Initialize a project

```bash
python3 scripts/wrf_init.py --project-name demo
```

### 2. Generate the config from a request

```bash
python3 scripts/wrf_config.py   --project-name demo   --request-text "East China, GFS, 2024-07-20 00:00 to 2024-07-20 12:00, local"   --run-mode local
```

### 3. Start long tasks asynchronously

```bash
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
python3 scripts/wrf_task.py start --project-name demo --step wrf-run
```

### 4. Check progress later

```bash
python3 scripts/wrf_task.py status --project-name demo
python3 scripts/wrf_task.py logs --project-name demo --lines 80
```

## HPC Usage

`wrf-skill` distinguishes between execution mode and access route.

- `run_mode=local`: run directly on the current machine
- `run_mode=hpc` + `access_mode=login`: the current machine is already the login node
- `run_mode=hpc` + `access_mode=ssh`: the current machine SSHes to a login node before scheduler submission

Current built-in scheduler adapters:

- Slurm
- PBS

For HPC mode, start from [config/wrf_env.hpc.example.json](config/wrf_env.hpc.example.json) and create your own local `config/wrf_env.json`.

## Typical Workflow

```text
wrf_init -> wrf_config -> wrf_task start wrf-data -> wrf_task start wrf-wps -> wrf_task start wrf-run
```

Long steps return quickly after submission. State and artifact registration are persisted in `runs/<project>/project.json`.

## Repository Layout

```text
.claude/skills/           Skill prompts and orchestration guidance
config/                   Schemas, presets, and example HPC config
scripts/                  Workflow logic, task control, HPC adapters, and bundle tools
templates/                Project and job-script templates
tests/                    Regression tests
third_party/wps-support/  Lightweight WPS support tables
```

## What Is Not Included

To keep the repository portable and safe to publish, it does not include:

- your real `config/wrf_env.json`
- run outputs under `runs/`
- full `WPS_GEOG`
- compiled WRF/WPS build trees
- private cluster credentials or SSH settings

## Current Scope

This repository is currently aimed at a practical MVP for:

- structured WRF project generation
- async local/HPC orchestration
- SSH and login-node submission paths
- clean redistribution of the workflow layer

It is best viewed as a workflow toolkit around WRF, not a replacement for WRF/WPS itself.
