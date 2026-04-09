---
name: wrf-post
description: Post-process WRF output files, extract fields, and generate diagnostic plots into runs/<project>/output/plots/.
---

# WRF Post

Treat `post_spec.json` as the authoritative request when present. The canonical shape is `schema_version=2` with top-level `defaults`, `style_defs`, `layer_defs`, and `figures`.

## Workflow

1. If the user does not provide a post spec, create a starter file with `python3 scripts/post_spec.py --project-name <project> --output post_spec.json`.
2. If the user wants a fuller example with reusable layers, a per-frame figure, and a range-only figure, copy `templates/post_spec.example.json` to `post_spec.json`.
3. Normalize and validate an existing spec with `python3 scripts/post_spec.py --input post_spec.json --output post_spec.json`.
4. Interpret a spec before rendering with `python3 scripts/post_spec.py --input post_spec.json --interpret`.
5. Run project-level post-processing with `python3 scripts/wrf_post.py --project-name <project> [--post-spec <path>]`.
6. For one-off rendering of a single figure definition, use `python3 scripts/plot_wrfout.py --wrfout <path> [<path> ...] --figure-id <figure_id> --post-spec <path> --out <png>`.
7. Define reusable data layers in `layer_defs`; define reusable draw presets in `style_defs`; define actual outputs in `figures`.
8. Scalar render layers use `layer_id`; vector render layers use `u_layer_id` plus `v_layer_id` with `draw.kind=vector`.
9. The current vector renderer supports `style.mode=quiver`.
10. Use `layer_defs[*].source.kind=wrf_native_2d` for direct 2D fields, `wrf_native_3d` with `source.level_selector` for 3D slices, and `wrf_diag` for built-in diagnostics such as `wind_speed_10m`, `wind_dir_10m`, `total_precip`, `temp_c_2m`, and `rh2`.
11. Read `project.json` only to resolve implicit WRF outputs when `figures[*].inputs.mode=project_artifacts`.
12. Keep stable protocol sections in `defaults`, `style_defs`, `layer_defs`, `figures[*].inputs`, `figures[*].selectors`, `figures[*].render`, and `figures[*].output`.
13. Save generated plots to `runs/<project>/output/plots/`.
14. Record generated plot paths in `project.json.artifacts.plots`.

## Files

- `config/post_schema.json`
- `scripts/post_spec.py`
- `scripts/wrf_post.py`
- `scripts/plot_wrfout.py`
- `runs/<project>/project.json`
