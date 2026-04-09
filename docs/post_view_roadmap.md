# Post-Processing Generalized View Roadmap

Status date: 2026-04-09

This document tracks the next implementation steps for `wrf-post` as the runtime evolves beyond the current `schema_version=2` behavior.
It is not the protocol draft itself. The design draft remains in [post_view_protocol.md](/mnt/c/Users/dell/Documents/sepcific_skill/docs/post_view_protocol.md).

## Current State

The runtime now has these foundations:

- Internal layering is split into `FieldCube -> ResolvedViewField`
- Map views, `time-x`, and `time-height` all go through a unified view-resolution path
- A first-pass path section is now available
  - `x_axis.kind=path_coord`
  - `x_axis.name=distance_km`
  - `y_axis.name=bottom_top | height_m`
  - `sampling.path.kind=polyline`
- Real `wrfout` smoke runs now confirm that `distance_km x height_m` sections can be rendered

Known limits:

- Path sampling still uses nearest-grid-point lookup, not bilinear interpolation
- `height_m` rendering is still first-pass, but explicit cell edges now keep real-data smoke runs free of `pcolormesh` coordinate warnings
- `pressure_hpa` is not implemented yet
- `nearest_value`, `value`, `mean`, `min`, `max`, and `sum` selectors are not implemented yet
- Section-vector projection is not implemented yet
- `distance_km` is currently restricted to `x_axis`

## Guiding Principles

- Keep the `layer -> view -> render` split intact
- Prefer extending the internal runtime before changing the external schema
- Add new capabilities in the view resolver, not by growing figure-kind branches in render code
- Every phase should ship with both synthetic tests and real `wrfout` smoke coverage

## Phase 1: Stabilize Path Sections

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

Goal:

- Make path sections more general than the current `distance_km on x_axis` rule

Tasks:

- Support `distance_km` on `y_axis`
- Expand view-axis validation for more combinations
- Normalize default axis metadata, labels, and units across view kinds

Done when:

- The same data layer can be reused across multiple path-axis layouts without special-case figure logic

## Phase 5: Section Vector Protocol

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

Goal:

- Fold the stable runtime behavior back into the external protocol and examples

Tasks:

- Decide whether to promote the work into `schema_version=3`
- Update the English and Chinese protocol drafts
- Add complete examples for path sections, pressure sections, and reduction selectors
- Update the README post-processing section

## Recommended Order

Suggested priority:

1. Stabilize path sections
2. Add `pressure_hpa`
3. Add richer selectors
4. Relax path-axis restrictions
5. Add section-vector protocol
6. Converge docs and schema versioning

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
