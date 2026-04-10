from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from namelist_parser import validate_namelist, write_namelist
    from spec_utils import (
        ALLOWED_DATA_SOURCES,
        ALLOWED_RUN_MODES,
        BASE_PHYSICS_KEYS,
        default_forcing_interval_seconds,
        deep_merge,
        normalize_spec,
        parse_time,
    )
except ImportError:  # pragma: no cover
    from .namelist_parser import validate_namelist, write_namelist
    from .spec_utils import (
        ALLOWED_DATA_SOURCES,
        ALLOWED_RUN_MODES,
        BASE_PHYSICS_KEYS,
        default_forcing_interval_seconds,
        deep_merge,
        normalize_spec,
        parse_time,
    )

SUPPORTED_MAP_PROJECTIONS = {"lambert", "mercator", "polar"}
CORE_DOMAIN_KEYS = (
    "name",
    "parent_id",
    "parent_grid_ratio",
    "dx_km",
    "dy_km",
    "e_we",
    "e_sn",
    "i_parent_start",
    "j_parent_start",
)


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _integer_ratio(parent_value: float, child_value: float) -> int:
    ratio = parent_value / child_value
    rounded = round(ratio)
    if rounded < 1 or abs(ratio - rounded) > 1e-6:
        raise ValueError(
            f"Child spacing must divide parent spacing exactly: parent={parent_value}, child={child_value}"
        )
    return int(rounded)


def _repeat(value: Any, count: int) -> list[Any]:
    return [value for _ in range(count)]


def _domain_values(domains: list[dict[str, Any]], key: str) -> list[Any]:
    return [domain[key] for domain in domains]


def _copy_without_none(values: dict[str, Any], *, drop_keys: set[str] | None = None) -> dict[str, Any]:
    blocked = drop_keys or set()
    return {
        key: value
        for key, value in values.items()
        if key not in blocked and value is not None
    }


def _coerce_section_value(base_value: Any, override: Any, domain_count: int) -> Any:
    if isinstance(base_value, list) and not isinstance(override, list):
        return [override for _ in range(domain_count)]
    return override


def _merge_section(base_section: dict[str, Any], overrides: dict[str, Any], domain_count: int) -> dict[str, Any]:
    merged = dict(base_section)
    for key, value in overrides.items():
        merged[key] = _coerce_section_value(merged.get(key), value, domain_count)
    return merged


def _collect_physics_keys(spec: dict[str, Any]) -> list[str]:
    keys = set(spec.get("physics", {}).keys())
    for domain in spec.get("domains", []):
        keys.update(domain.get("physics", {}).keys())
    keys.update(spec.get("model", {}).get("namelist_input", {}).get("physics", {}).keys())
    ordered = list(BASE_PHYSICS_KEYS)
    ordered.extend(sorted(key for key in keys if key not in ordered))
    return ordered


def _domain_physics_values(spec: dict[str, Any], key: str) -> list[Any]:
    values: list[Any] = []
    base_value = spec.get("physics", {}).get(key)
    for domain in spec.get("domains", []):
        domain_override = domain.get("physics", {}).get(key)
        values.append(domain_override if domain_override is not None else base_value)
    return values


def _validate_projection(geogrid: dict[str, Any], errors: list[str]) -> None:
    map_proj = str(geogrid.get("map_proj") or "").lower()
    if map_proj not in SUPPORTED_MAP_PROJECTIONS:
        errors.append(
            f"Unsupported map_proj: {geogrid.get('map_proj')} (expected one of {sorted(SUPPORTED_MAP_PROJECTIONS)})"
        )
        return

    if geogrid.get("ref_lat") is None or geogrid.get("ref_lon") is None:
        errors.append("wps.geogrid.ref_lat and ref_lon are required")
    if map_proj == "lambert":
        for key in ("truelat1", "truelat2", "stand_lon"):
            if geogrid.get(key) is None:
                errors.append(f"wps.geogrid.{key} is required for lambert projection")
    if map_proj == "mercator" and geogrid.get("truelat1") is None:
        errors.append("wps.geogrid.truelat1 is required for mercator projection")
    if map_proj == "polar":
        for key in ("truelat1", "stand_lon"):
            if geogrid.get(key) is None:
                errors.append(f"wps.geogrid.{key} is required for polar projection")


def validate_spec(spec: dict[str, Any]) -> list[str]:
    normalized = normalize_spec(spec)
    errors: list[str] = []

    try:
        start_time = parse_time(normalized["timing"]["start_time"])
        end_time = parse_time(normalized["timing"]["end_time"])
    except ValueError as exc:
        errors.append(f"Invalid time format: {exc}")
        return errors

    if end_time <= start_time:
        errors.append("timing.end_time must be later than timing.start_time")

    if str(normalized["data_source"]).lower() not in ALLOWED_DATA_SOURCES:
        errors.append(
            f"Unsupported data_source: {normalized['data_source']} (expected one of {sorted(ALLOWED_DATA_SOURCES)})"
        )
    if str(normalized["execution"]["run_mode"]).lower() not in ALLOWED_RUN_MODES:
        errors.append(
            f"Unsupported run_mode: {normalized['execution']['run_mode']} (expected one of {sorted(ALLOWED_RUN_MODES)})"
        )

    source = str(normalized["data_source"]).lower()
    interval_seconds = int(normalized["timing"]["forcing_interval_seconds"])
    if source == "fnl":
        fnl_step_seconds = default_forcing_interval_seconds("fnl")
        if interval_seconds % fnl_step_seconds != 0:
            errors.append("FNL forcing_interval_seconds must be a whole multiple of 21600 seconds (6 hours)")
        if start_time.minute != 0 or start_time.second != 0 or start_time.hour % 6 != 0:
            errors.append("FNL start_time must align to 00/06/12/18 UTC analyses")

    domains = normalized.get("domains", [])
    if not domains:
        errors.append("At least one domain is required")
        return errors

    physics = normalized.get("physics", {})
    for key in _collect_physics_keys(normalized):
        value = physics.get(key)
        if value is None and key in BASE_PHYSICS_KEYS:
            errors.append(f"physics.{key} must be set")
            continue
        if value is not None and (not isinstance(value, int) or value < 0):
            errors.append(f"physics.{key} must be a non-negative integer")

    _validate_projection(normalized["wps"]["geogrid"], errors)

    for index, domain in enumerate(domains, start=1):
        for key in CORE_DOMAIN_KEYS:
            if key not in domain:
                errors.append(f"Domain {index} is missing {key}")

        try:
            dx_km = float(domain["dx_km"])
            dy_km = float(domain["dy_km"])
            e_we = int(domain["e_we"])
            e_sn = int(domain["e_sn"])
            parent_id = int(domain["parent_id"])
            parent_grid_ratio = int(domain["parent_grid_ratio"])
            i_parent_start = int(domain["i_parent_start"])
            j_parent_start = int(domain["j_parent_start"])
        except (TypeError, ValueError, KeyError):
            errors.append(f"Domain {index} contains invalid grid settings")
            continue

        if dx_km <= 0 or dy_km <= 0:
            errors.append(f"Domain {index} dx_km and dy_km must be positive")
        if e_we < 3 or e_sn < 3:
            errors.append(f"Domain {index} e_we and e_sn must be at least 3")
        if i_parent_start < 1 or j_parent_start < 1:
            errors.append(f"Domain {index} i_parent_start and j_parent_start must be at least 1")

        for physics_key, physics_value in domain.get("physics", {}).items():
            if not isinstance(physics_value, int) or physics_value < 0:
                errors.append(f"domains.{index - 1}.physics.{physics_key} must be a non-negative integer")

        if index == 1:
            if parent_id != 1:
                errors.append("Domain 1 parent_id must be 1")
            if parent_grid_ratio != 1:
                errors.append("Domain 1 parent_grid_ratio must be 1")
            continue

        if parent_id < 1 or parent_id >= index:
            errors.append(f"Domain {index} parent_id must reference a previous domain")
            continue
        if parent_grid_ratio < 1:
            errors.append(f"Domain {index} parent_grid_ratio must be at least 1")
            continue

        parent_domain = domains[parent_id - 1]
        try:
            expected_dx_ratio = _integer_ratio(float(parent_domain["dx_km"]), dx_km)
            expected_dy_ratio = _integer_ratio(float(parent_domain["dy_km"]), dy_km)
        except ValueError as exc:
            errors.append(f"Domain {index} {exc}")
            continue

        if expected_dx_ratio != parent_grid_ratio or expected_dy_ratio != parent_grid_ratio:
            errors.append(
                f"Domain {index} parent_grid_ratio={parent_grid_ratio} does not match dx/dy ratio"
            )

    return errors


def build_namelist_wps(
    spec: dict[str, Any],
    geog_data_path: str,
    geog_data_res: str = "default",
) -> dict[str, dict[str, Any]]:
    normalized = normalize_spec(spec)
    domains = normalized["domains"]
    timing = normalized["timing"]
    wps = normalized["wps"]
    count = len(domains)

    share = {
        **_copy_without_none(wps.get("share", {}), drop_keys={"max_dom", "start_date", "end_date"}),
        "max_dom": count,
        "start_date": _repeat(timing["start_time"], count),
        "end_date": _repeat(timing["end_time"], count),
        "interval_seconds": int(timing["forcing_interval_seconds"]),
    }
    geogrid = {
        **_copy_without_none(
            wps.get("geogrid", {}),
            drop_keys={
                "parent_id",
                "parent_grid_ratio",
                "i_parent_start",
                "j_parent_start",
                "e_we",
                "e_sn",
                "dx",
                "dy",
                "geog_data_path",
                "geog_data_res",
            },
        ),
        "parent_id": _domain_values(domains, "parent_id"),
        "parent_grid_ratio": _domain_values(domains, "parent_grid_ratio"),
        "i_parent_start": _domain_values(domains, "i_parent_start"),
        "j_parent_start": _domain_values(domains, "j_parent_start"),
        "e_we": _domain_values(domains, "e_we"),
        "e_sn": _domain_values(domains, "e_sn"),
        "geog_data_res": [
            domain.get("geog_data_res") or wps.get("geogrid", {}).get("geog_data_res") or geog_data_res
            for domain in domains
        ],
        "dx": int(float(domains[0]["dx_km"]) * 1000),
        "dy": int(float(domains[0]["dy_km"]) * 1000),
        "geog_data_path": geog_data_path,
    }
    ungrib = _copy_without_none(wps.get("ungrib", {}))
    metgrid = _copy_without_none(wps.get("metgrid", {}))

    rendered = {
        "share": share,
        "geogrid": geogrid,
        "ungrib": ungrib,
        "metgrid": metgrid,
    }
    raw_overrides = normalized.get("experimental", {}).get("raw_namelist_wps", {})
    return deep_merge(rendered, raw_overrides)


def build_namelist_input(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized = normalize_spec(spec)
    domains = normalized["domains"]
    timing = normalized["timing"]
    count = len(domains)
    start_time = parse_time(timing["start_time"])
    end_time = parse_time(timing["end_time"])
    duration_hours = int((end_time - start_time).total_seconds() // 3600)
    outer_dx = int(float(domains[0]["dx_km"]) * 1000)
    outer_dy = int(float(domains[0]["dy_km"]) * 1000)
    time_step = max(6, int(float(domains[0]["dx_km"]) * 6))
    input_model = normalized["model"]["namelist_input"]

    base_sections: dict[str, dict[str, Any]] = {
        "time_control": {
            "run_days": duration_hours // 24,
            "run_hours": duration_hours % 24,
            "start_year": _repeat(start_time.year, count),
            "start_month": _repeat(start_time.month, count),
            "start_day": _repeat(start_time.day, count),
            "start_hour": _repeat(start_time.hour, count),
            "end_year": _repeat(end_time.year, count),
            "end_month": _repeat(end_time.month, count),
            "end_day": _repeat(end_time.day, count),
            "end_hour": _repeat(end_time.hour, count),
            "interval_seconds": int(timing["forcing_interval_seconds"]),
            "input_from_file": _repeat(True, count),
            "history_interval": _repeat(int(timing["history_interval_minutes"]), count),
            "frames_per_outfile": _repeat(int(timing["frames_per_outfile"]), count),
            "restart": bool(timing["restart"]),
            "io_form_history": 2,
            "io_form_restart": 2,
            "io_form_input": 2,
            "io_form_boundary": 2,
        },
        "domains": {
            "time_step": time_step,
            "max_dom": count,
            "e_we": _domain_values(domains, "e_we"),
            "e_sn": _domain_values(domains, "e_sn"),
            "e_vert": _repeat(int(input_model["domains"].get("e_vert", 50)), count),
            "dzstretch_s": input_model["domains"].get("dzstretch_s", 1.1),
            "p_top_requested": input_model["domains"].get("p_top_requested", 5000),
            "num_metgrid_levels": input_model["domains"].get("num_metgrid_levels", 34),
            "num_metgrid_soil_levels": input_model["domains"].get("num_metgrid_soil_levels", 4),
            "dx": outer_dx,
            "dy": outer_dy,
            "grid_id": list(range(1, count + 1)),
            "parent_id": _domain_values(domains, "parent_id"),
            "i_parent_start": _domain_values(domains, "i_parent_start"),
            "j_parent_start": _domain_values(domains, "j_parent_start"),
            "parent_grid_ratio": _domain_values(domains, "parent_grid_ratio"),
            "parent_time_step_ratio": _domain_values(domains, "parent_grid_ratio"),
            "feedback": input_model["domains"].get("feedback", 1),
            "smooth_option": input_model["domains"].get("smooth_option", 0),
        },
        "physics": {},
        "dynamics": {
            "hybrid_opt": 2,
            "w_damping": 0,
            "diff_opt": 1,
            "km_opt": 4,
        },
        "bdy_control": {
            "spec_bdy_width": 5,
            "specified": True,
            "nested": count > 1,
        },
        "namelist_quilt": {
            "nio_tasks_per_group": 0,
            "nio_groups": 1,
        },
    }

    for key in _collect_physics_keys(normalized):
        values = _domain_physics_values(normalized, key)
        if any(value is not None for value in values):
            base_sections["physics"][key] = values

    for section, overrides in input_model.items():
        if not isinstance(overrides, dict):
            continue
        base_sections[section] = _merge_section(base_sections.get(section, {}), overrides, count)

    raw_overrides = normalized.get("experimental", {}).get("raw_namelist_input", {})
    return deep_merge(base_sections, raw_overrides)


def render_from_spec(
    spec: dict[str, Any],
    geog_data_path: str,
    geog_data_res: str = "default",
) -> dict[str, dict[str, dict[str, Any]]]:
    normalized = normalize_spec(spec)
    return {
        "namelist.wps": build_namelist_wps(normalized, geog_data_path, geog_data_res),
        "namelist.input": build_namelist_input(normalized),
    }


def write_rendered_files(
    rendered: dict[str, dict[str, dict[str, Any]]],
    out_dir: Path | str,
) -> list[str]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for file_name, config in rendered.items():
        target = out_path / file_name
        errors = validate_namelist(config)
        if errors:
            raise ValueError(f"{file_name} failed validation: {errors}")
        write_namelist(config, target)
        written.append(str(target))
    return written


def write_rendered_targets(
    rendered: dict[str, dict[str, dict[str, Any]]],
    targets: dict[str, Path | str],
) -> list[str]:
    written: list[str] = []
    for file_name, config in rendered.items():
        if file_name not in targets:
            raise KeyError(f"Missing target path for {file_name}")
        target = Path(targets[file_name])
        target.parent.mkdir(parents=True, exist_ok=True)
        errors = validate_namelist(config)
        if errors:
            raise ValueError(f"{file_name} failed validation: {errors}")
        write_namelist(config, target)
        written.append(str(target))
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render WRF namelists from a structured spec")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--geog-data-path", default="/data/WPS_GEOG")
    parser.add_argument("--out-dir", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    spec = load_json(args.spec)
    errors = validate_spec(spec)
    if errors:
        raise SystemExit("\n".join(errors))
    rendered = render_from_spec(spec, args.geog_data_path)
    written = write_rendered_files(rendered, args.out_dir)
    print(json.dumps({"written": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
