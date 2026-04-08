---
name: wrf-workspace-init
description: Create a fresh WRF workspace in any target directory by unpacking the bundled minimal workspace template, writing a ready marker, and returning the absolute path for the next Codex or Claude session.
---

# WRF Workspace Init

Use this skill when the user wants a new WRF workspace created in an arbitrary directory, especially when the current working directory is not already a WRF repo checkout.

## Workflow

1. Use the bundled `scripts/init_workspace.sh` from this skill directory. Do not rebuild the workspace tree by hand.
2. If the user provides a destination path, pass it as `--target-root`. Otherwise default to a new child directory such as `./wrf-workspace` under the current working directory.
3. Do not use `--force` unless the user explicitly allows replacing an existing target directory.
4. After creation, read `CODEX_WORKSPACE_READY.txt` and report the absolute workspace path.
5. Tell the user the generated workspace contains `scripts/`, `config/`, `templates/`, `third_party/wps-support`, `runs/.gitkeep`, and local `.claude/skills`, but excludes private configs, WPS geography datasets, compiled binaries, and prior run outputs.
6. If the user needs HPC mode, tell them to copy `config/wrf_env.hpc.example.json` to `config/wrf_env.json` and fill in cluster-specific values.
7. In follow-up turns, work from the generated workspace root instead of the original directory.

## Notes

- This skill is intended to bootstrap a portable WRF workspace and keep the working tree minimal.
- The generated workspace is compatible with the existing WRF scripts in this repository.
- If the workspace already exists and the user does not permit replacement, stop and report the conflict.

## Files

- `scripts/init_workspace.sh`
- `assets/workspace-bundle/`
