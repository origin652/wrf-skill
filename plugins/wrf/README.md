# WRF Codex Plugin

This repo-local plugin exposes the existing WRF skills in this repository to Codex plugin discovery.

The canonical skill instructions still live under `.claude/skills/`.
The plugin `skills/` tree is intentionally a thin wrapper layer so the workflow rules stay defined in one place.

Key paths:

- `.agents/plugins/marketplace.json`
- `plugins/wrf/.codex-plugin/plugin.json`
- `plugins/wrf/skills/`
- `.claude/skills/`
