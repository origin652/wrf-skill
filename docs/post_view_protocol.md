# Generalized Post-Processing View Protocol Draft

Status: proposal only. This document does not describe the current runnable `schema_version=2` contract.

Phase 6 conclusion for this repository: keep the runnable contract on `schema_version=2` for now. `schema_version=3` is not being published yet, so this file remains a future-facing design draft.

Some ideas from this draft have already landed in the v2 runtime, including `view_defs`, `time-x`, `time-y`, `time-height`, `time-pressure`, richer selector modes, `distance_km` path sections, and explicit path-section vector projection.
This file still describes the future direction beyond the current validated v2 scope, especially arbitrary 2-axis combinations and section-vector behavior.

For the current runnable behavior, see `docs/post_runtime_v2.md`.

## Goal

This draft started from the earlier post-processing model where each layer was effectively forced into a 2D `south_north x west_east` field.
That is enough for map products, but it blocks:

- time-height sections
- west-east or south-north vs time sections
- arbitrary line distance-height sections
- other 2-axis views where the plotted plane is not the horizontal map plane

The next protocol should avoid adding separate figure kinds such as `map`, `time_height`, `distance_height`, and `time_x`.
Instead, every figure should be modeled as:

1. a reusable n-D data layer
2. a reusable 2-axis view definition
3. the existing render-layer stack on top of the resolved 2D view

## Core Idea

Keep `layer_defs` for data computation, but stop requiring every layer to collapse to 2D during evaluation.
Add `view_defs` that describe how to extract a 2D slice from an n-D field.
Then let each figure bind to exactly one view.

Proposed future shape:

```json
{
  "schema_version": 3,
  "layer_defs": {},
  "style_defs": {},
  "view_defs": {},
  "figures": []
}
```

## Proposed `view_defs`

A view definition answers one question:

"Which two axes become the plotted plane, and how are all remaining axes fixed, reduced, or sampled?"

Proposed shape:

```json
{
  "view_id": "time_height_point",
  "x_axis": {},
  "y_axis": {},
  "selectors": {},
  "sampling": {}
}
```

### Axis definitions

An axis definition should support three categories:

- native dimensions
  - `time`
  - `bottom_top`
  - `south_north`
  - `west_east`
- derived coordinates
  - `height_m`
  - `pressure_hpa`
  - `lat`
  - `lon`
- sampled coordinates
  - `distance_km` along a path

Proposed axis shape:

```json
{
  "kind": "native_dim | derived_coord | path_coord",
  "name": "time | bottom_top | south_north | west_east | height_m | pressure_hpa | distance_km",
  "label": "optional axis label override",
  "units": "optional axis units override"
}
```

### Selectors

Selectors define what happens to dimensions that are not chosen as x/y axes.

Supported selector intent should include:

- fixed discrete selection
  - `index`
  - `nearest_index`
- fixed physical selection
  - `value`
  - `nearest_value`
- runtime-bound selection
  - `current`
  - `first`
  - `last`
- reduction over a dimension
  - `mean`
  - `min`
  - `max`
  - `sum`

Proposed selector shape:

```json
{
  "time": {
    "mode": "current"
  },
  "bottom_top": {
    "mode": "index",
    "index": 0
  },
  "south_north": {
    "mode": "nearest_index",
    "index": 42
  }
}
```

### Sampling

Sampling is only needed when the plotted plane is not directly aligned with native grid axes.

First useful sampling mode:

- horizontal path sampling for `distance_km x height_m`

Proposed shape:

```json
{
  "path": {
    "kind": "polyline",
    "points": [
      {"lat": 31.20, "lon": 121.40},
      {"lat": 31.80, "lon": 122.10}
    ],
    "samples": 200
  }
}
```

## Figure binding

Each figure should point to one view.
The existing render-layer stack can remain mostly unchanged.

Proposed figure shape:

```json
{
  "figure_id": "theta_time_height",
  "view_id": "time_height_point",
  "layers": [
    {
      "layer_id": "theta",
      "style_id": "theta_raster"
    }
  ]
}
```

This keeps a clean split:

- `layer_defs` answers "what data"
- `view_defs` answers "which 2D slice"
- `style_defs` answers "how to draw"
- `figures` answers "which combination becomes an artifact"

## Example views

### 1. Standard map view

```json
{
  "view_id": "map_xy_current",
  "x_axis": {"kind": "native_dim", "name": "west_east"},
  "y_axis": {"kind": "native_dim", "name": "south_north"},
  "selectors": {
    "time": {"mode": "current"}
  }
}
```

### 2. Time-height at one point

```json
{
  "view_id": "time_height_point",
  "x_axis": {"kind": "native_dim", "name": "time"},
  "y_axis": {"kind": "derived_coord", "name": "height_m"},
  "selectors": {
    "south_north": {"mode": "nearest_index", "index": 30},
    "west_east": {"mode": "nearest_index", "index": 45}
  }
}
```

### 3. Distance-height along a line

```json
{
  "view_id": "distance_height_line",
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

### 4. West-east vs time at fixed y and level

```json
{
  "view_id": "time_x_fixed_y_level",
  "x_axis": {"kind": "native_dim", "name": "time"},
  "y_axis": {"kind": "native_dim", "name": "west_east"},
  "selectors": {
    "south_north": {"mode": "nearest_index", "index": 50},
    "bottom_top": {"mode": "index", "index": 0}
  }
}
```

## Execution semantics

The figure output mode should depend on unresolved runtime selectors, not on hard-coded map logic.

Suggested rule:

- if `time` is one of the plotted axes, emit one artifact for the selected time range
- if `time` is not a plotted axis and the resolved view still uses `current(time)`, emit one artifact per frame
- if `time` is reduced or resolved by `first/last/range`, emit one artifact for the selected range

This is the same idea as the current `current/first/last` semantics, but moved into the generalized view layer.

## Layer evaluation model

The runtime should move from "2D field only" to a small internal field object.

Suggested internal payload:

```text
FieldCube
- values: ndarray
- dims: ["time", "bottom_top", "south_north", "west_east"]
- coords: optional coord arrays or coord resolvers
- units
- metadata
```

`layer_defs` would compute `FieldCube` objects.
`view_defs` would convert them into 2D `ResolvedViewField` payloads.
Renderers would stay 2D.

## Recommended implementation order

1. Introduce internal n-D field metadata without changing the external `schema_version=2` contract.
2. Add `view_defs` and `figure.view_id` in a draft `schema_version=3`.
3. Implement only axis-aligned views first:
   - map `x/y`
   - `time/height`
   - `time/x`
   - `time/y`
4. Add derived vertical coordinates:
   - `height_m`
   - `pressure_hpa`
5. Add path sampling for `distance_km`.
6. Revisit vector overlays for non-map views after scalar sections are stable.

## What should stay out of scope at first

- 3D volumetric rendering
- isosurfaces
- arbitrary reprojection
- full geodesic/cartographic axis engines
- automatic vector projection in every possible view

## Practical note about vectors

Vector rendering becomes ambiguous outside map views.

Examples:

- map view: `u` and `v` already match x/y
- distance-height section: the horizontal component should usually be projected onto the path tangent
- time-height section: a vector overlay may not even make sense as arrows

So the first generalized-section phase should focus on scalar layers.
Vector support for generalized views should come later as an explicit axis-space projection feature, not as a hidden automatic rule.
