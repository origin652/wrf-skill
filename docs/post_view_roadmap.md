# Post-Processing Generalized View Roadmap

Status date: 2026-04-10

This document tracks the next implementation steps for `wrf-post` as the runtime evolves beyond the current `schema_version=3` behavior.
It is not the protocol draft itself. The design draft remains in [post_view_protocol.md](/mnt/c/Users/dell/Documents/sepcific_skill/docs/post_view_protocol.md).

## Current State

The runtime now has these foundations:

- Internal layering is split into `FieldCube -> ResolvedViewField`
- Map views, `time-x`, path sections, and time-vertical sections all go through a unified view-resolution path
- Path sections support `distance_km` on either axis together with `bottom_top`, `height_m`, or `pressure_hpa`
- Time-vertical sections support `time` with `height_m` or `pressure_hpa` in either axis order
- Path sections use `sampling.path.kind=polyline`
- Path sampling now uses bilinear interpolation instead of nearest-grid-point lookup
- Default axis metadata now carries units for `distance_km`, `height_m`, and `pressure_hpa`
- Real `wrfout` smoke runs now confirm that `distance_km x height_m`, `height_m x distance_km`, and `time x pressure_hpa` sections can be rendered
- View selectors now support `nearest_index`, `value`, `nearest_value`, `mean`, `min`, `max`, and `sum`
- Path sections now support `distance_km` on either `x_axis` or `y_axis`
- Path-section vectors now support explicit `axis_projection` with `path_tangent`, `path_normal`, and `vertical`
- Real `wrfout` smoke also now confirms path-section vectors driven by native WRF `U`, `V`, and `W` after mass-grid destaggering
- The example template and README now include complete examples for pressure sections, reduction selectors, and path-section vectors

Known limits:

- Section vectors are currently limited to path views with explicit `axis_projection`
- Path sections are still limited to one `distance_km` axis plus one vertical axis

## Guiding Principles

- Keep the `layer -> view -> render` split intact
- Prefer extending the internal runtime before changing the external schema
- Add new capabilities in the view resolver, not by growing figure-kind branches in render code
- Every phase should ship with both synthetic tests and real `wrfout` smoke coverage

## Phase 1: Stabilize Path Sections

Status: completed

Goal:

- Move the current `distance_km x bottom_top | height_m` support from usable to stable

Tasks:

- Replace nearest-grid-point path sampling with bilinear interpolation
- Add explicit cell-edge handling or a more stable grid construction path to eliminate `pcolormesh` warnings
- Improve metadata and unit handling for `height_m`
- Add a fixed real-data smoke case for regression protection

Done when:

- Real-data path sections no longer emit coordinate warnings
- Synthetic and real-data path-section tests both stay green

## Phase 2: Derived Vertical Coordinates

Status: completed

Goal:

- Add `pressure_hpa`
- Unify vertical-coordinate resolution for `bottom_top`, `height_m`, and `pressure_hpa`

Tasks:

- Introduce a dedicated vertical-coordinate resolver in the runtime
- Add unified loaders for `PH/PHB` and `P/PB`
- Let `ResolvedViewField` carry either 1D or 2D vertical coordinates
- Add synthetic and real-data tests for `pressure_hpa`

Done when:

- `distance_km x pressure_hpa`
- `time x pressure_hpa`
- `time x height_m`

all render reliably.

## Phase 3: Richer Selectors

Status: completed

Goal:

- Move beyond index-only selection in views

Tasks:

- Add `nearest_index`
- Add `value`
- Add `nearest_value`
- Add reduction selectors
  - `mean`
  - `min`
  - `max`
  - `sum`

Done when:

- Users can select by physical coordinates
- Users can reduce along hidden dimensions instead of fixing only one index

## Phase 4: Relax Path-Axis Restrictions

Status: completed

Goal:

- Make path sections more general than the current `distance_km on x_axis` rule

Tasks:

- Support `distance_km` on `y_axis`
- Expand view-axis validation for swapped path and time-vertical layouts
- Normalize default axis metadata, labels, and units across view kinds

Done when:

- The same data layer can be reused across multiple path-axis layouts without special-case figure logic

## Phase 5: Section Vector Protocol

Status: completed

Goal:

- Add an explicit protocol for vector overlays outside map views

Tasks:

- Define an axis-space projection config
- Support projection of wind vectors onto path-tangent or path-normal components
- Make it explicit which views allow vectors and which remain scalar-only

Done when:

- Vector behavior no longer depends on hidden heuristics
- Section-vector semantics are explicit in the protocol

## Phase 6: Protocol and Documentation Convergence

Status: completed

Goal:

- Fold the stable runtime behavior back into the external protocol and examples

Tasks:

- Decide whether to promote the work into `schema_version=3`
- Update the English and Chinese protocol drafts
- Add complete examples for path sections, pressure sections, and reduction selectors
- Update the README post-processing section

Decision:

- Publish the current validated contract as `schema_version=3`
- Treat further arbitrary 2-axis expansion as work beyond the currently validated v3 scope

Done when:

- The template, README, and v3 guide all point to the same runnable `schema_version=3` contract
- The protocol notes clearly distinguish current v3 behavior from future extensions

## Recommended Order

Suggested priority:

1. Relax path-axis restrictions
2. Add section-vector protocol
3. Converge docs and schema versioning

## Work That Should Stay Out of Scope For Now

- 3D volumetric rendering
- Arbitrary reprojection
- A full GIS-grade coordinate engine
- Automatic vector inference on every possible view

## Main Code Touchpoints

Most of the remaining work belongs in:

- [plot_wrfout.py](/mnt/c/Users/dell/Documents/sepcific_skill/scripts/plot_wrfout.py)
- [post_spec.py](/mnt/c/Users/dell/Documents/sepcific_skill/scripts/post_spec.py)
- [test_wrf_post.py](/mnt/c/Users/dell/Documents/sepcific_skill/tests/test_wrf_post.py)
- [test_post_spec.py](/mnt/c/Users/dell/Documents/sepcific_skill/tests/test_post_spec.py)
