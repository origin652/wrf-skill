---
name: wrf-post
description: Post-process WRF output files, extract fields, and generate diagnostic plots into runs/<project>/output/plots/.
---

# WRF Post

Treat `post_spec.json` as the authoritative request when present. The canonical shape is `schema_version=2` with top-level `defaults`, `layer_defs`, and `figures`.

## Workflow

1. If the user does not provide a post spec, create a starter file with `python3 scripts/post_spec.py --project-name <project> --output post_spec.json`.
2. Normalize and validate an existing spec with `python3 scripts/post_spec.py --input post_spec.json --output post_spec.json`.
3. Run project-level post-processing with `python3 scripts/wrf_post.py --project-name <project> [--post-spec <path>]`.
4. For one-off rendering of a single figure definition, use `python3 scripts/plot_wrfout.py --wrfout <path> [<path> ...] --figure-id <figure_id> --post-spec <path> --out <png>`.
5. Define reusable data layers in `layer_defs`; define actual outputs in `figures`.
6. Read `project.json` only to resolve implicit WRF outputs when `figures[*].inputs.mode=project_artifacts`.
7. Keep stable protocol sections in `defaults`, `layer_defs`, `figures[*].inputs`, `figures[*].selectors`, `figures[*].render`, and `figures[*].output`.
8. Save generated plots to `runs/<project>/output/plots/`.
9. Record generated plot paths in `project.json.artifacts.plots`.

## Files

- `config/post_schema.json`
- `scripts/post_spec.py`
- `scripts/wrf_post.py`
- `scripts/plot_wrfout.py`
- `runs/<project>/project.json`
