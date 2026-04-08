---
name: wrf-post
description: Post-process WRF output files, extract fields, and generate diagnostic plots into runs/<project>/output/plots/.
---

# WRF Post

Treat `post_spec.json` as the authoritative request when present. The canonical shape is `schema_version=1` with top-level `defaults` and `products`.

## Workflow

1. If the user does not provide a post spec, create a starter file with `python3 scripts/post_spec.py --project-name <project> --output post_spec.json`.
2. Normalize and validate an existing spec with `python3 scripts/post_spec.py --input post_spec.json --output post_spec.json`.
3. Run project-level post-processing with `python3 scripts/wrf_post.py --project-name <project> [--post-spec <path>]`.
4. Use `scripts/plot_wrfout.py` only as the low-level single-product renderer or for backward-compatible direct CLI usage.
5. Read `project.json` only to resolve implicit WRF outputs when `inputs.mode=project_artifacts`.
6. Keep stable protocol sections in `inputs`, `selectors`, `render`, and `output`; keep product-specific knobs in `options`.
7. Save generated plots to `runs/<project>/output/plots/`.
8. Record generated plot paths in `project.json.artifacts.plots`.

## Files

- `config/post_schema.json`
- `scripts/post_spec.py`
- `scripts/wrf_post.py`
- `scripts/plot_wrfout.py`
- `runs/<project>/project.json`
