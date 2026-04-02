# Third-Party Files

This repository includes a small set of third-party support tables under `third_party/wps-support/`:

- `GEOGRID.TBL.ARW`
- `METGRID.TBL.ARW`
- `Vtable.GFS`

## Why They Are Included

These files are lightweight runtime support tables used by the workflow layer around WPS. Keeping them in the repository makes the bundle and local setup materially easier.

## Provenance

These files are derived from the WPS / WRF preprocessing ecosystem rather than being original project code written specifically for `wrf-skill`.

## Publishing Notes

- They should be treated as third-party support files, not as original `wrf-skill` source code.
- Repository-level licensing should take these files into account.
- If you want the cleanest possible top-level license boundary for this repository, the safest option is to remove these files from version control and fetch them from upstream during installation.
- The top-level [LICENSE](LICENSE) applies to project-authored files in this repository; treat these bundled third-party tables as separately documented support assets.

## Current Recommendation

Until the provenance and redistribution terms are documented more explicitly, keep third-party attribution separate and avoid claiming that every file in this repository is covered by a single project-authored copyright statement.
