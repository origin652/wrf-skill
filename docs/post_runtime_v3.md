# Current WRF Post-Processing v3 Guide

Status date: 2026-04-10

This document describes the current runnable figure-oriented `schema_version=3` post-processing behavior in this repository.
`schema_version=4` now keeps the same figure contract and adds `region_defs` plus `charts` for native statistical graphics.
It is the companion to:

- `config/post_schema.json` for the machine-readable contract
- `templates/post_spec.example.json` for a fuller starter spec
- `docs/post_view_protocol.md` for design notes and future extensions beyond the current validated scope

Phase 6 conclusion in this repository: the validated figure runtime contract is published as `schema_version=3`, and the current chart extension layer now ships as `schema_version=4`.

## What Is Stable Today

The current v3 runtime supports these building blocks:

- reusable `layer_defs`
- reusable `style_defs`
- reusable `view_defs`
- `figures[*]` that bind inputs, selectors, a view, and render layers

Supported `layer_defs[*].source.kind` values:

- `wrf_native_2d`
- `wrf_native_3d`
- `wrf_native_3d_full`
- `wrf_diag`
- `wrf_native` as a compatibility alias of `wrf_native_2d`

Supported input modes:

- `project_artifacts`
- `explicit_paths`
- `glob`

Supported draw kinds:

- `raster`
- `contour`
- `categorical_fill`
- `vector`

Current vector scope:

- `draw.kind=vector` is supported for map views
- path views also support vectors when `draw.style.axis_projection.kind=path_section`
- path-section vectors use `u_layer_id` and `v_layer_id` for horizontal components
- path-section vectors use `vertical_layer_id` when `axis_projection` includes `vertical`
- path-section axis-space components currently support `path_tangent`, `path_normal`, and `vertical`
- native WRF `U`, `V`, and `W` loaded through `wrf_native_3d_full` are destaggered onto the mass grid before section resolution
- current vector styling supports `style.mode=quiver`
- time-axis and time-vertical views remain scalar-only

## Supported View Shapes

### 1. Map views

Stable map views use the horizontal grid:

- `west_east x south_north`
- `south_north x west_east`

If a figure does not provide a `view` or `view_id`, the default view is the horizontal map plane.

### 2. Axis-aligned time sections

The current runtime supports native-dimension sections such as:

- `time x west_east`
- `west_east x time`
- `time x south_north`
- `south_north x time`

These views are typically used with selectors that fix or reduce the remaining native dimensions.

### 3. Time-vertical sections

The current derived-coordinate section scope is:

- `time x height_m`
- `height_m x time`
- `time x pressure_hpa`
- `pressure_hpa x time`

Current boundary:

- derived-coordinate views must pair one `time` axis with one derived vertical axis
- the derived vertical axis must be `height_m` or `pressure_hpa`

### 4. Path sections

The current first-pass path-section scope is:

- `distance_km x bottom_top`
- `bottom_top x distance_km`
- `distance_km x height_m`
- `height_m x distance_km`
- `distance_km x pressure_hpa`
- `pressure_hpa x distance_km`

Required path sampling shape:

```json
{
  "sampling": {
    "path": {
      "kind": "polyline",
      "points": [
        {"lat": 31.20, "lon": 121.40},
        {"lat": 31.80, "lon": 122.10}
      ],
      "samples": 200
    }
  }
}
```

Current boundaries:

- exactly one plotted axis must be `path_coord` with `name=distance_km`
- the other plotted axis must be `bottom_top`, `height_m`, or `pressure_hpa`
- path sections do not currently support `time` as a plotted axis
- path-section vectors require explicit `draw.style.axis_projection`

## Supported Selector Modes

`view.selectors` currently supports these modes on native dimensions:

- `index`
- `nearest_index`
- `value`
- `nearest_value`
- `first`
- `last`
- `current`
- `mean`
- `min`
- `max`
- `sum`

Current boundary:

- `current` is only valid for `time`

Typical uses:

- fix one row: `south_north: { "mode": "index", "index": 50 }`
- pick a physical coordinate by value: `bottom_top: { "mode": "nearest_value", "value": 850 }`
- reduce an unused axis: `south_north: { "mode": "mean" }`

## Output Semantics

The runtime decides whether a figure emits one artifact for a frame range or one artifact per frame from the resolved view and layer usage:

- if `time` is a plotted axis, output mode is `frame_range`
- if `time` is not a plotted axis but the resolved view uses `time.mode=current`, output mode is `per_frame`
- otherwise the runtime falls back to layer usage and emits `per_frame` only when a rendered layer still depends on `current(...)`

## Minimal Workflow

Generate a starter spec:

```bash
python3 scripts/post_spec.py --project-name demo --output post_spec.json
```

Start from the fuller example:

```bash
cp templates/post_spec.example.json post_spec.json
```

That template now includes complete runnable examples for:

- a mean-reduced `time-x` section
- a `time-pressure` column
- a `distance_km x height_m` scalar path section
- a path-section vector overlay using native WRF `U`, `V`, and `W`
- a regional mean temperature line chart
- a grouped final-frame temperature bar chart
- a grouped temperature boxplot built from per-frame regional means

Normalize and validate:

```bash
python3 scripts/post_spec.py --input post_spec.json --output post_spec.json
```

Inspect the resolved execution plan:

```bash
python3 scripts/post_spec.py --input post_spec.json --interpret
```

Render a named figure directly:

```bash
python3 scripts/plot_wrfout.py \
  --wrfout runs/demo/wrf/wrfout_d01_2024-07-20_00:00:00 \
  --figure-id surface_temperature \
  --post-spec post_spec.json \
  --out surface-temperature.png
```

Run project-level post-processing:

```bash
python3 scripts/wrf_post.py --project-name demo --post-spec runs/demo/post_spec.json
```

## Statistical Chart Examples (`schema_version=4`)

Regional mean time-series:

```json
{
  "chart_id": "west_box_t2_time_mean",
  "chart_kind": "line",
  "x": {"mode": "time", "label": "valid_time"},
  "series": [
    {
      "series_id": "west_mean",
      "label": "West Box Mean T2",
      "layer_id": "t2_c",
      "region_id": "west_box",
      "reduce": {"mode": "mean"}
    }
  ]
}
```

Grouped last-frame comparison:

```json
{
  "chart_id": "grouped_t2_last_frame",
  "chart_kind": "bar",
  "x": {"mode": "group", "group_ids": ["west_box", "east_box"], "label": "region"},
  "series": [
    {
      "series_id": "group_mean",
      "label": "Group Mean T2",
      "layer_id": "t2_c",
      "reduce": {"mode": "mean"}
    }
  ]
}
```

Grouped time-distribution boxplot:

```json
{
  "chart_id": "grouped_t2_time_distribution",
  "chart_kind": "boxplot",
  "x": {"mode": "group", "group_ids": ["west_box", "east_box"], "label": "region"},
  "series": [
    {
      "series_id": "group_distribution",
      "label": "Time Distribution of Group Mean T2",
      "layer_id": "t2_c",
      "reduce": {"mode": "mean"}
    }
  ]
}
```

## Example View Fragments

`time x west_east` with a fixed `south_north` row:

```json
{
  "x_axis": {"name": "time"},
  "y_axis": {"name": "west_east"},
  "selectors": {
    "south_north": {"mode": "index", "index": 50}
  }
}
```

`time x pressure_hpa` at a fixed column:

```json
{
  "x_axis": {"name": "time"},
  "y_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
  "selectors": {
    "south_north": {"mode": "index", "index": 50},
    "west_east": {"mode": "index", "index": 50}
  }
}
```

`distance_km x height_m` path section:

```json
{
  "x_axis": {"kind": "path_coord", "name": "distance_km"},
  "y_axis": {"kind": "derived_coord", "name": "height_m"},
  "selectors": {
    "time": {"mode": "current"}
  },
  "sampling": {
    "path": {
      "kind": "polyline",
      "points": [
        {"lat": 31.20, "lon": 121.40},
        {"lat": 31.80, "lon": 122.10}
      ],
      "samples": 200
    }
  }
}
```

Path-section vector overlay:

```json
{
  "u_layer_id": "u_path",
  "v_layer_id": "v_path",
  "vertical_layer_id": "w_path",
  "draw": {
    "kind": "vector",
    "style": {
      "mode": "quiver",
      "axis_projection": {
        "kind": "path_section",
        "x_component": "path_tangent",
        "y_component": "vertical"
      }
    }
  }
}
```

## What Is Still Future Work

These are still outside the current v3 scope:

- arbitrary 2-axis combinations beyond the validated view shapes above
- section-vector support outside explicit path-section axis_projection
- path sections with `time` as a plotted axis
- more general sampling models beyond `sampling.path.kind=polyline`

The runnable contract now uses `schema_version=3`. For future extensions beyond the validated scope above, see `docs/post_view_protocol.md` and `docs/post_view_roadmap.md`.
