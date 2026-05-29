from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

try:
    from namelist_parser import read_namelist
    from render_config import render_from_spec, validate_spec, write_rendered_files, write_rendered_targets
    from spec_utils import (
        BASE_PHYSICS_KEYS,
        DEFAULT_SPEC_VERSION,
        TIME_FORMAT,
        default_forcing_interval_seconds,
        normalize_spec,
    )
    from wrf_config import (
        apply_inputs_to_spec,
        apply_namelist_overrides,
        load_spec_fragment,
        parse_namelist_overrides,
        parse_overrides,
        parse_request_text,
    )
    from project_state import (
        assert_mutation_allowed,
        load_project,
        register_artifact,
        reset_after_reconfigure,
        save_project,
    )
except ImportError:  # pragma: no cover
    from .namelist_parser import read_namelist
    from .render_config import render_from_spec, validate_spec, write_rendered_files, write_rendered_targets
    from .spec_utils import (
        BASE_PHYSICS_KEYS,
        DEFAULT_SPEC_VERSION,
        TIME_FORMAT,
        default_forcing_interval_seconds,
        normalize_spec,
    )
    from .wrf_config import (
        apply_inputs_to_spec,
        apply_namelist_overrides,
        load_spec_fragment,
        parse_namelist_overrides,
        parse_overrides,
        parse_request_text,
    )
    from .project_state import (
        assert_mutation_allowed,
        load_project,
        register_artifact,
        reset_after_reconfigure,
        save_project,
    )

WPS_DOMAIN_KEYS = {
    "parent_id",
    "parent_grid_ratio",
    "i_parent_start",
    "j_parent_start",
    "e_we",
    "e_sn",
    "geog_data_res",
}

INPUT_DOMAIN_KEYS = {
    "max_dom",
    "e_we",
    "e_sn",
    "dx",
    "dy",
    "grid_id",
    "parent_id",
    "i_parent_start",
    "j_parent_start",
    "parent_grid_ratio",
}

OWNED_INPUT_KEYS = {
    "time_control": {
        "run_days",
        "run_hours",
        "run_minutes",
        "run_seconds",
        "start_year",
        "start_month",
        "start_day",
        "start_hour",
        "start_minute",
        "start_second",
        "end_year",
        "end_month",
        "end_day",
        "end_hour",
        "end_minute",
        "end_second",
        "interval_seconds",
        "input_from_file",
        "history_interval",
        "frames_per_outfile",
        "restart",
    },
    "domains": {
        "time_step",
        "max_dom",
        "e_we",
        "e_sn",
        "dx",
        "dy",
        "grid_id",
        "parent_id",
        "i_parent_start",
        "j_parent_start",
        "parent_grid_ratio",
        "parent_time_step_ratio",
    },
    "physics": set(BASE_PHYSICS_KEYS),
}

MODEL_DOMAIN_SCALAR_KEYS = {
    "e_vert",
    "dzstretch_s",
    "p_top_requested",
    "num_metgrid_levels",
    "num_metgrid_soil_levels",
    "feedback",
    "smooth_option",
}


def _first(value: Any, default: Any = None) -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return default if value is None else value


def _as_list(value: Any, count: int, default: Any = None) -> list[Any]:
    if isinstance(value, list):
        if len(value) >= count:
            return value[:count]
        return [*value, *[default for _ in range(count - len(value))]]
    return [default if value is None else value for _ in range(count)]


def _int_value(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _float_value(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _format_time(year: Any, month: Any, day: Any, hour: Any, minute: Any = 0, second: Any = 0) -> str:
    return datetime(
        int(year),
        int(month),
        int(day),
        int(hour),
        int(minute or 0),
        int(second or 0),
    ).strftime(TIME_FORMAT)


def _parse_wps_time(value: Any) -> str | None:
    token = _first(value)
    if token is None:
        return None
    text = str(token).strip()
    for fmt in (TIME_FORMAT, "%Y-%m-%d_%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime(TIME_FORMAT)
        except ValueError:
            continue
    return None


def _time_from_input(time_control: dict[str, Any], prefix: str) -> str | None:
    year = _first(time_control.get(f"{prefix}_year"))
    month = _first(time_control.get(f"{prefix}_month"))
    day = _first(time_control.get(f"{prefix}_day"))
    hour = _first(time_control.get(f"{prefix}_hour"), 0)
    minute = _first(time_control.get(f"{prefix}_minute"), 0)
    second = _first(time_control.get(f"{prefix}_second"), 0)
    if year is None or month is None or day is None:
        return None
    return _format_time(year, month, day, hour, minute, second)


def _infer_end_from_duration(time_control: dict[str, Any], start_time: str | None) -> str | None:
    if not start_time:
        return None
    start = datetime.strptime(start_time, TIME_FORMAT)
    delta = timedelta(
        days=_int_value(time_control.get("run_days"), 0),
        hours=_int_value(time_control.get("run_hours"), 0),
        minutes=_int_value(time_control.get("run_minutes"), 0),
        seconds=_int_value(time_control.get("run_seconds"), 0),
    )
    if delta.total_seconds() <= 0:
        return None
    return (start + delta).strftime(TIME_FORMAT)


def _infer_data_source(wps: dict[str, dict[str, Any]], explicit: str | None) -> str:
    if explicit:
        return explicit.lower()
    candidates = [
        _first(wps.get("ungrib", {}).get("prefix")),
        _first(wps.get("metgrid", {}).get("fg_name")),
    ]
    for candidate in candidates:
        token = str(candidate or "").strip().lower()
        if token in {"gfs", "era5", "fnl"}:
            return token
    return "gfs"


def _domain_count(
    namelist_input: dict[str, dict[str, Any]],
    namelist_wps: dict[str, dict[str, Any]],
) -> int:
    candidates = [
        namelist_input.get("domains", {}).get("max_dom"),
        namelist_wps.get("share", {}).get("max_dom"),
        namelist_wps.get("geogrid", {}).get("max_dom"),
    ]
    for candidate in candidates:
        if candidate is not None:
            return int(_first(candidate))
    for source in (namelist_input.get("domains", {}), namelist_wps.get("geogrid", {})):
        for key in ("e_we", "parent_id", "parent_grid_ratio"):
            value = source.get(key)
            if isinstance(value, list) and value:
                return len(value)
    return 1


def _build_domains(
    namelist_input: dict[str, dict[str, Any]],
    namelist_wps: dict[str, dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    input_domains = namelist_input.get("domains", {})
    geogrid = namelist_wps.get("geogrid", {})
    outer_dx_m = _float_value(input_domains.get("dx", geogrid.get("dx")), 27000.0)
    outer_dy_m = _float_value(input_domains.get("dy", geogrid.get("dy")), 27000.0)
    parent_ids = _as_list(input_domains.get("parent_id", geogrid.get("parent_id")), count, 1)
    parent_grid_ratios = _as_list(
        input_domains.get("parent_grid_ratio", geogrid.get("parent_grid_ratio")),
        count,
        1,
    )
    i_parent_start = _as_list(
        input_domains.get("i_parent_start", geogrid.get("i_parent_start")),
        count,
        1,
    )
    j_parent_start = _as_list(
        input_domains.get("j_parent_start", geogrid.get("j_parent_start")),
        count,
        1,
    )
    e_we = _as_list(input_domains.get("e_we", geogrid.get("e_we")), count, 100)
    e_sn = _as_list(input_domains.get("e_sn", geogrid.get("e_sn")), count, 100)
    geog_data_res = _as_list(geogrid.get("geog_data_res"), count, None)

    dx_km: list[float] = []
    dy_km: list[float] = []
    for index in range(count):
        if index == 0:
            dx_km.append(outer_dx_m / 1000.0)
            dy_km.append(outer_dy_m / 1000.0)
            continue
        parent_index = max(0, int(parent_ids[index]) - 1)
        ratio = max(1, int(parent_grid_ratios[index]))
        dx_km.append(dx_km[parent_index] / ratio)
        dy_km.append(dy_km[parent_index] / ratio)

    domains: list[dict[str, Any]] = []
    for index in range(count):
        domains.append(
            {
                "name": f"d{index + 1:02d}",
                "parent_id": _int_value(parent_ids[index], 1),
                "parent_grid_ratio": _int_value(parent_grid_ratios[index], 1),
                "dx_km": dx_km[index],
                "dy_km": dy_km[index],
                "e_we": _int_value(e_we[index], 100),
                "e_sn": _int_value(e_sn[index], 100),
                "i_parent_start": _int_value(i_parent_start[index], 1),
                "j_parent_start": _int_value(j_parent_start[index], 1),
                "ref_lat": _float_value(geogrid.get("ref_lat"), 31.2),
                "ref_lon": _float_value(geogrid.get("ref_lon"), 121.5),
                "geog_data_res": geog_data_res[index],
                "physics": {},
            }
        )
    return domains


def _split_physics(physics_section: dict[str, Any], domains: list[dict[str, Any]]) -> dict[str, Any]:
    domain_count = len(domains)
    structured: dict[str, Any] = {}
    ordered_keys = list(BASE_PHYSICS_KEYS)
    ordered_keys.extend(sorted(key for key in physics_section if key not in ordered_keys))
    for key in ordered_keys:
        if key not in physics_section:
            continue
        values = _as_list(physics_section[key], domain_count, None)
        base_value = values[0]
        if all(value == base_value for value in values):
            if base_value is not None:
                structured[key] = int(base_value)
            continue
        if base_value is not None:
            structured[key] = int(base_value)
        for index, value in enumerate(values):
            if value is not None and value != base_value:
                domains[index].setdefault("physics", {})[key] = int(value)
    return structured


def _model_namelist_input(
    namelist_input: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    model: dict[str, dict[str, Any]] = {}
    for section, values in namelist_input.items():
        if section == "domains":
            domain_values: dict[str, Any] = {}
            for key in MODEL_DOMAIN_SCALAR_KEYS:
                if key in values:
                    domain_values[key] = _first(values[key])
            for key, value in values.items():
                if key not in OWNED_INPUT_KEYS["domains"] and key not in MODEL_DOMAIN_SCALAR_KEYS:
                    domain_values[key] = deepcopy(value)
            if domain_values:
                model[section] = domain_values
            continue

        if section in {"dynamics", "bdy_control", "namelist_quilt"}:
            model[section] = deepcopy(values)
            continue

        owned_keys = OWNED_INPUT_KEYS.get(section)
        if owned_keys is None:
            model[section] = deepcopy(values)
            continue

        extras = {key: deepcopy(value) for key, value in values.items() if key not in owned_keys}
        if extras:
            model[section] = extras
    return model


def _wps_sections(namelist_wps: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    wps: dict[str, dict[str, Any]] = {}
    for section, values in namelist_wps.items():
        wps[section] = deepcopy(values)
    return wps


def _load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path | str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8", newline="\n")


def _geog_data_path(namelist_wps: dict[str, dict[str, Any]], explicit: str | None) -> str:
    if explicit:
        return explicit
    value = _first(namelist_wps.get("geogrid", {}).get("geog_data_path"))
    return str(value or "/data/WPS_GEOG")


def _geog_data_res(namelist_wps: dict[str, dict[str, Any]], explicit: str | None) -> str:
    if explicit:
        return explicit
    value = _first(namelist_wps.get("geogrid", {}).get("geog_data_res"))
    return str(value or "default")


def _time_source(time_control: dict[str, Any], wps_share: dict[str, Any], prefix: str) -> str:
    if _time_from_input(time_control, prefix) is not None:
        return "namelist.input"
    if _parse_wps_time(wps_share.get(f"{prefix}_date")) is not None:
        return "namelist.wps"
    if prefix == "end" and _infer_end_from_duration(time_control, _time_from_input(time_control, "start")) is not None:
        return "duration"
    return "default"


def _data_source_source(wps_config: dict[str, dict[str, Any]], explicit: str | None) -> str:
    if explicit:
        return "explicit"
    for section, key in (("ungrib", "prefix"), ("metgrid", "fg_name")):
        token = str(_first(wps_config.get(section, {}).get(key)) or "").strip().lower()
        if token in {"gfs", "era5", "fnl"}:
            return f"namelist.wps.{section}.{key}"
    return "default"


def _domain_count_source(
    namelist_input: dict[str, dict[str, Any]],
    namelist_wps: dict[str, dict[str, Any]],
) -> str:
    if namelist_input.get("domains", {}).get("max_dom") is not None:
        return "namelist.input.domains.max_dom"
    if namelist_wps.get("share", {}).get("max_dom") is not None:
        return "namelist.wps.share.max_dom"
    if namelist_wps.get("geogrid", {}).get("max_dom") is not None:
        return "namelist.wps.geogrid.max_dom"
    return "derived_from_domain_arrays" if _domain_count(namelist_input, namelist_wps) > 1 else "default"


def _import_diagnostics(
    namelist_input: dict[str, dict[str, Any]],
    namelist_wps: dict[str, dict[str, Any]],
    spec: dict[str, Any],
    *,
    data_source: str | None,
    run_mode: str,
) -> dict[str, Any]:
    time_control = namelist_input.get("time_control", {})
    wps_share = namelist_wps.get("share", {})
    data_source_origin = _data_source_source(namelist_wps, data_source)
    start_origin = _time_source(time_control, wps_share, "start")
    end_origin = _time_source(time_control, wps_share, "end")
    defaults: list[str] = []
    warnings: list[str] = []
    if data_source_origin == "default":
        defaults.append("data_source")
        warnings.append("data_source could not be inferred from WPS prefix or fg_name; defaulted to gfs")
    if start_origin == "default":
        defaults.append("timing.start_time")
        warnings.append("start_time could not be inferred from namelists; defaulted to 2024-07-20_00:00:00")
    if end_origin == "default":
        defaults.append("timing.end_time")
        warnings.append("end_time could not be inferred from namelists; defaulted to 2024-07-20_06:00:00")
    if run_mode == "local":
        defaults.append("execution.run_mode")
    model_sections = spec.get("model", {}).get("namelist_input", {})
    unstructured = [
        f"model.namelist_input.{section}.{key}"
        for section, values in sorted(model_sections.items())
        for key in sorted(values)
    ]
    return {
        "sources": {
            "data_source": data_source_origin,
            "timing.start_time": start_origin,
            "timing.end_time": end_origin,
            "domains.count": _domain_count_source(namelist_input, namelist_wps),
            "execution.run_mode": "explicit" if run_mode != "local" else "default",
        },
        "defaults": defaults,
        "warnings": warnings,
        "unstructured_fields": unstructured,
    }


def _flatten_namelist(file_name: str, namelist: dict[str, dict[str, Any]]) -> dict[tuple[str, str, str], Any]:
    return {
        (file_name, section, key): value
        for section, values in namelist.items()
        for key, value in values.items()
    }


def diff_namelists(
    source_input: dict[str, dict[str, Any]],
    source_wps: dict[str, dict[str, Any]],
    rendered: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    before = {}
    before.update(_flatten_namelist("namelist.input", source_input))
    before.update(_flatten_namelist("namelist.wps", source_wps))
    after = {}
    after.update(_flatten_namelist("namelist.input", rendered.get("namelist.input", {})))
    after.update(_flatten_namelist("namelist.wps", rendered.get("namelist.wps", {})))
    changes: list[dict[str, Any]] = []
    for file_name, section, key in sorted(set(before) | set(after)):
        before_value = before.get((file_name, section, key))
        after_value = after.get((file_name, section, key))
        if before_value == after_value:
            continue
        if (file_name, section, key) not in before:
            change_type = "added"
        elif (file_name, section, key) not in after:
            change_type = "removed"
        else:
            change_type = "changed"
        changes.append(
            {
                "file": file_name,
                "section": section,
                "key": key,
                "path": f"{file_name}.{section}.{key}",
                "change": change_type,
                "before": before_value,
                "after": after_value,
            }
        )
    return changes


def format_diff(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "No namelist changes."
    lines: list[str] = []
    current_file: str | None = None
    for change in changes:
        if change["file"] != current_file:
            current_file = change["file"]
            lines.append(current_file)
        lines.append(
            f"- {change['section']}.{change['key']}: {change.get('before')!r} -> {change.get('after')!r}"
        )
    return "\n".join(lines)


def _project_paths(runs_dir: Path | str, project_name: str) -> dict[str, Path]:
    project_root = Path(runs_dir) / project_name
    return {
        "project_root": project_root,
        "project_json": project_root / "project.json",
        "spec": project_root / "simulation_spec.json",
        "namelist.input": project_root / "wrf" / "namelist.input",
        "namelist.wps": project_root / "wps" / "namelist.wps",
        "log": project_root / "logs" / "wrf-improve-namelists.log",
    }


def _write_project_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def spec_from_namelists(
    *,
    namelist_input: dict[str, dict[str, Any]] | None = None,
    namelist_wps: dict[str, dict[str, Any]] | None = None,
    project_name: str = "imported",
    data_source: str | None = None,
    run_mode: str = "local",
    include_raw: bool = True,
) -> dict[str, Any]:
    input_config = namelist_input or {}
    wps_config = namelist_wps or {}
    time_control = input_config.get("time_control", {})
    wps_share = wps_config.get("share", {})
    count = _domain_count(input_config, wps_config)

    start_time = (
        _time_from_input(time_control, "start")
        or _parse_wps_time(wps_share.get("start_date"))
        or "2024-07-20_00:00:00"
    )
    end_time = (
        _time_from_input(time_control, "end")
        or _parse_wps_time(wps_share.get("end_date"))
        or _infer_end_from_duration(time_control, start_time)
        or "2024-07-20_06:00:00"
    )
    source = _infer_data_source(wps_config, data_source)
    domains = _build_domains(input_config, wps_config, count)
    physics = _split_physics(input_config.get("physics", {}), domains)

    forcing_interval = int(
        _first(
            time_control.get("interval_seconds", wps_share.get("interval_seconds")),
            default_forcing_interval_seconds(source),
        )
    )
    spec: dict[str, Any] = {
        "schema_version": DEFAULT_SPEC_VERSION,
        "project_name": project_name,
        "data_source": source,
        "timing": {
            "start_time": start_time,
            "end_time": end_time,
            "forcing_interval_seconds": forcing_interval,
            "history_interval_minutes": int(_first(time_control.get("history_interval"), 60)),
            "frames_per_outfile": int(_first(time_control.get("frames_per_outfile"), 1)),
            "restart": bool(_first(time_control.get("restart"), False)),
        },
        "execution": {
            "run_mode": run_mode,
        },
        "domains": domains,
        "physics": physics,
        "wps": _wps_sections(wps_config),
        "model": {
            "namelist_input": _model_namelist_input(input_config),
        },
        "experimental": {
            "raw_namelist_input": {},
            "raw_namelist_wps": {},
        },
    }

    normalized = normalize_spec(spec, project_name_fallback=project_name)
    normalized["experimental"]["raw_namelist_input"] = {}
    normalized["experimental"]["raw_namelist_wps"] = {}
    normalized["experimental"]["import_diagnostics"] = _import_diagnostics(
        input_config,
        wps_config,
        normalized,
        data_source=data_source,
        run_mode=run_mode,
    )
    if include_raw:
        normalized["experimental"]["imported_namelist_input"] = deepcopy(input_config)
        normalized["experimental"]["imported_namelist_wps"] = deepcopy(wps_config)
    return normalized


def load_namelists(
    namelist_input_path: Path | str | None,
    namelist_wps_path: Path | str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if namelist_input_path is None and namelist_wps_path is None:
        raise ValueError("At least one namelist path is required")
    input_config = read_namelist(namelist_input_path) if namelist_input_path else {}
    wps_config = read_namelist(namelist_wps_path) if namelist_wps_path else {}
    return input_config, wps_config


def improve_namelists(
    *,
    namelist_input_path: Path | str | None,
    namelist_wps_path: Path | str | None,
    project_name: str = "imported",
    data_source: str | None = None,
    run_mode: str = "local",
    domains_config: Path | str = "config/domains_presets.json",
    physics_config: Path | str = "config/physics_schemes.json",
    spec_fragment_json: Path | str | None = None,
    domain_presets: list[str] | None = None,
    physics_preset: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    request_text: str | None = None,
    override_entries: list[str] | None = None,
    namelist_override_entries: list[str] | None = None,
    geog_data_path: str | None = None,
    geog_data_res: str | None = None,
    out_dir: Path | str | None = None,
    spec_out: Path | str | None = None,
    runs_dir: Path | str | None = None,
    dry_run: bool = False,
    include_raw: bool = True,
    include_diff_text: bool = False,
) -> dict[str, Any]:
    project_paths: dict[str, Path] | None = None
    project_state: dict[str, Any] | None = None
    if runs_dir is not None:
        project_paths = _project_paths(runs_dir, project_name)
        if not project_paths["project_json"].exists():
            raise FileNotFoundError(f"Missing project.json: {project_paths['project_json']}")
        project_state = load_project(project_paths["project_json"])
        assert_mutation_allowed(project_state, "wrf-improve-namelists")
        artifacts = project_state.get("artifacts", {})
        if namelist_input_path is None:
            namelist_input_path = artifacts.get("namelist_input") or project_paths["namelist.input"]
        if namelist_wps_path is None:
            namelist_wps_path = artifacts.get("namelist_wps") or project_paths["namelist.wps"]
        if spec_out is None:
            spec_out = project_paths["spec"]

    input_config, wps_config = load_namelists(namelist_input_path, namelist_wps_path)
    base_spec = spec_from_namelists(
        namelist_input=input_config,
        namelist_wps=wps_config,
        project_name=project_name,
        data_source=data_source,
        run_mode=run_mode,
        include_raw=include_raw,
    )

    needs_catalogs = bool(domain_presets or physics_preset or request_text)
    domains_catalog = _load_json(domains_config) if needs_catalogs else {}
    physics_catalog = _load_json(physics_config) if needs_catalogs else {}
    inferred = (
        parse_request_text(request_text, domains_catalog, physics_catalog)
        if request_text
        else {
            "start_time": None,
            "end_time": None,
            "data_source": None,
            "run_mode": None,
            "domain_presets": [],
            "physics_preset": None,
        }
    )

    spec, applied = apply_inputs_to_spec(
        base_spec,
        domain_presets=domain_presets or inferred["domain_presets"],
        physics_preset=physics_preset or inferred["physics_preset"],
        domains_catalog=domains_catalog,
        physics_catalog=physics_catalog,
        start_time=start_time or inferred["start_time"],
        end_time=end_time or inferred["end_time"],
        data_source=data_source or inferred["data_source"],
        run_mode=run_mode or inferred["run_mode"],
        spec_fragment=load_spec_fragment(spec_fragment_json),
        overrides=parse_overrides(override_entries),
    )

    spec_errors = validate_spec(spec)
    if spec_errors:
        raise ValueError("; ".join(spec_errors))

    rendered = render_from_spec(
        spec,
        _geog_data_path(wps_config, geog_data_path),
        _geog_data_res(wps_config, geog_data_res),
    )
    namelist_overrides = parse_namelist_overrides(namelist_override_entries)
    if namelist_overrides:
        rendered = apply_namelist_overrides(
            rendered,
            namelist_overrides,
            domain_count=len(spec["domains"]),
        )

    changes = diff_namelists(input_config, wps_config, rendered)
    diff_text = format_diff(changes)

    written: list[str] = []
    project_updated = False
    if not dry_run:
        if project_paths is not None and out_dir is None:
            targets = {
                "namelist.input": project_paths["namelist.input"],
                "namelist.wps": project_paths["namelist.wps"],
            }
            written.extend(write_rendered_targets(rendered, targets))
            if spec_out:
                _write_json(spec_out, spec)
                written.append(str(spec_out))
            assert project_state is not None
            reset_after_reconfigure(project_state)
            project_state["data_source"]["type"] = spec["data_source"]
            project_state["data_source"]["start_time"] = spec["timing"]["start_time"]
            project_state["data_source"]["end_time"] = spec["timing"]["end_time"]
            project_state["execution"]["mode"] = spec["execution"]["run_mode"]
            project_state["execution"]["dry_run"] = False
            register_artifact(project_state, "namelist_input", targets["namelist.input"].as_posix())
            register_artifact(project_state, "namelist_wps", targets["namelist.wps"].as_posix())
            save_project(project_state, project_paths["project_json"])
            _write_project_log(
                project_paths["log"],
                [
                    f"wrf-improve-namelists project={project_name}",
                    f"spec={Path(spec_out).as_posix() if spec_out else '(none)'}",
                    f"changes={len(changes)}",
                    f"written={','.join(written)}",
                ],
            )
            written.append(project_paths["log"].as_posix())
            project_updated = True
        else:
            if not out_dir and not spec_out:
                raise ValueError("Use --out-dir, --spec-out, --runs-dir, or --dry-run for improve mode")
            if out_dir:
                written.extend(write_rendered_files(rendered, out_dir))
            if spec_out:
                _write_json(spec_out, spec)
                written.append(str(spec_out))

    return {
        "dry_run": dry_run,
        "project_name": spec["project_name"],
        "schema_version": spec["schema_version"],
        "simulation_spec": spec,
        "diagnostics": spec.get("experimental", {}).get("import_diagnostics", {}),
        "diff": changes,
        "diff_text": diff_text if include_diff_text else None,
        "rendered": rendered if dry_run else {},
        "project_updated": project_updated,
        "plan": {
            "source_namelist_input": str(namelist_input_path) if namelist_input_path else None,
            "source_namelist_wps": str(namelist_wps_path) if namelist_wps_path else None,
            "out_dir": str(out_dir) if out_dir else None,
            "spec_out": str(spec_out) if spec_out else None,
            "runs_dir": str(runs_dir) if runs_dir else None,
            "applied": applied,
            "inferred": inferred,
            "namelist_overrides": namelist_overrides,
        },
        "written": written,
    }


def build_import_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import existing WRF namelists into a structured simulation spec")
    parser.add_argument("--namelist-input")
    parser.add_argument("--namelist-wps")
    parser.add_argument("--project-name", default="imported")
    parser.add_argument("--data-source", choices=("gfs", "era5", "fnl"))
    parser.add_argument("--run-mode", choices=("local", "hpc"), default="local")
    parser.add_argument("--out")
    parser.add_argument("--no-raw", action="store_true", help="Do not store source namelist snapshots in experimental.imported_namelist_*")
    return parser


def build_improve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Improve existing WRF namelists through the structured spec renderer")
    parser.add_argument("--namelist-input")
    parser.add_argument("--namelist-wps")
    parser.add_argument("--project-name", default="imported")
    parser.add_argument("--data-source", choices=("gfs", "era5", "fnl"))
    parser.add_argument("--run-mode", choices=("local", "hpc"), default="local")
    parser.add_argument("--domains-config", default="config/domains_presets.json")
    parser.add_argument("--physics-config", default="config/physics_schemes.json")
    parser.add_argument("--spec-fragment-json")
    parser.add_argument("--domain-preset", action="append", dest="domain_presets")
    parser.add_argument("--physics-preset")
    parser.add_argument("--start-time")
    parser.add_argument("--end-time")
    parser.add_argument("--request-text")
    parser.add_argument("--override", action="append", dest="override_entries")
    parser.add_argument("--namelist-override", action="append", dest="namelist_override_entries")
    parser.add_argument("--geog-data-path")
    parser.add_argument("--geog-data-res")
    parser.add_argument("--out-dir")
    parser.add_argument("--spec-out")
    parser.add_argument("--runs-dir", help="Use runs/<project> as the source and write back to project state when not using --out-dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--diff", action="store_true", help="Print a human-readable namelist change preview")
    parser.add_argument("--no-raw", action="store_true", help="Do not store source namelist snapshots in experimental.imported_namelist_*")
    return parser


def run_import(args: argparse.Namespace) -> dict[str, Any]:
    input_config, wps_config = load_namelists(args.namelist_input, args.namelist_wps)
    return spec_from_namelists(
        namelist_input=input_config,
        namelist_wps=wps_config,
        project_name=args.project_name,
        data_source=args.data_source,
        run_mode=args.run_mode,
        include_raw=not args.no_raw,
    )


def run_improve(args: argparse.Namespace) -> dict[str, Any]:
    return improve_namelists(
        namelist_input_path=args.namelist_input,
        namelist_wps_path=args.namelist_wps,
        project_name=args.project_name,
        data_source=args.data_source,
        run_mode=args.run_mode,
        domains_config=args.domains_config,
        physics_config=args.physics_config,
        spec_fragment_json=args.spec_fragment_json,
        domain_presets=args.domain_presets,
        physics_preset=args.physics_preset,
        start_time=args.start_time,
        end_time=args.end_time,
        request_text=args.request_text,
        override_entries=args.override_entries,
        namelist_override_entries=args.namelist_override_entries,
        geog_data_path=args.geog_data_path,
        geog_data_res=args.geog_data_res,
        out_dir=args.out_dir,
        spec_out=args.spec_out,
        runs_dir=args.runs_dir,
        dry_run=args.dry_run,
        include_raw=not args.no_raw,
        include_diff_text=args.diff,
    )


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    mode = "import"
    if raw_args and raw_args[0] in {"import", "improve"}:
        mode = raw_args.pop(0)

    if mode == "improve":
        args = build_improve_parser().parse_args(raw_args)
        payload = run_improve(args)
        if args.diff:
            print(payload["diff_text"])
        else:
            print(json.dumps(payload, indent=2, sort_keys=False))
        return 0

    args = build_import_parser().parse_args(raw_args)
    spec = run_import(args)
    rendered = json.dumps(spec, indent=2, sort_keys=False)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"written": str(target)}, indent=2))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
