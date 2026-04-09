---
name: wrf-post
description: Post-process WRF output files, extract fields, and generate diagnostic plots into runs/<project>/output/plots/.
---

# WRF Post

Treat `post_spec.json` as the authoritative request when present. The canonical shape is `schema_version=2` with top-level `defaults`, `style_defs`, optional `view_defs`, `layer_defs`, and `figures`.

## Workflow

1. If the user does not provide a post spec, create a starter file with `python3 scripts/post_spec.py --project-name <project> --output post_spec.json`.
2. If the user wants a fuller example with reusable layers, map/vector figures, and a `time-x` section view, copy `templates/post_spec.example.json` to `post_spec.json`.
3. Normalize and validate an existing spec with `python3 scripts/post_spec.py --input post_spec.json --output post_spec.json`.
4. Interpret a spec before rendering with `python3 scripts/post_spec.py --input post_spec.json --interpret`.
5. Run project-level post-processing with `python3 scripts/wrf_post.py --project-name <project> [--post-spec <path>]`.
6. For one-off rendering of a single figure definition, use `python3 scripts/plot_wrfout.py --wrfout <path> [<path> ...] --figure-id <figure_id> --post-spec <path> --out <png>`.
7. Define reusable data layers in `layer_defs`; define reusable draw presets in `style_defs`; define actual outputs in `figures`.
8. Scalar render layers use `layer_id`; vector render layers use `u_layer_id` plus `v_layer_id` with `draw.kind=vector`.
9. Use `view_defs` with `figures[*].view_id` (or inline `figures[*].view`) for reusable 2-axis view extraction.
10. Current section scope is axis-aligned views across `time`, `bottom_top`, `south_north`, and `west_east`; vector draw is currently map-only.
11. The current vector renderer supports `style.mode=quiver`.
12. Use `layer_defs[*].source.kind=wrf_native_2d` for direct 2D fields, `wrf_native_3d` with `source.level_selector` for 3D slices, `wrf_native_3d_full` for full 3D fields, and `wrf_diag` for built-in diagnostics such as `wind_speed_10m`, `wind_dir_10m`, `total_precip`, `temp_c_2m`, and `rh2`.
13. Read `project.json` only to resolve implicit WRF outputs when `figures[*].inputs.mode=project_artifacts`.
14. Keep stable protocol sections in `defaults`, `style_defs`, `view_defs`, `layer_defs`, `figures[*].inputs`, `figures[*].selectors`, `figures[*].render`, and `figures[*].output`.
15. Save generated plots to `runs/<project>/output/plots/`.
16. Record generated plot paths in `project.json.artifacts.plots`.

## Files

- `config/post_schema.json`
- `scripts/post_spec.py`
- `scripts/wrf_post.py`
- `scripts/plot_wrfout.py`
- `runs/<project>/project.json`
