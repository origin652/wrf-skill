from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

TIME_FORMAT = "%Y-%m-%d_%H:%M:%S"
DEFAULT_SPEC_VERSION = 2
ALLOWED_DATA_SOURCES = {"gfs", "era5", "fnl"}
ALLOWED_RUN_MODES = {"local", "hpc"}
BASE_PHYSICS_KEYS = (
    "mp_physics",
    "cu_physics",
    "ra_lw_physics",
    "ra_sw_physics",
    "bl_pbl_physics",
    "sf_sfclay_physics",
    "sf_surface_physics",
)
DEFAULT_SECTION_KEYS = (
    "time_control",
    "domains",
    "physics",
    "dynamics",
    "bdy_control",
    "namelist_quilt",
)


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT)


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def default_domain(index: int = 1) -> dict[str, Any]:
    return {
        "name": f"d{index:02d}",
        "parent_id": 1 if index == 1 else index - 1,
        "parent_grid_ratio": 1,
        "dx_km": 27,
        "dy_km": 27,
        "e_we": 100,
        "e_sn": 100,
        "i_parent_start": 1 if index == 1 else 30,
        "j_parent_start": 1 if index == 1 else 30,
        "ref_lat": 31.2,
        "ref_lon": 121.5,
        "geog_data_res": None,
        "physics": {},
    }


def default_spec(project_name: str = "demo") -> dict[str, Any]:
    return {
        "schema_version": DEFAULT_SPEC_VERSION,
        "project_name": project_name,
        "data_source": "gfs",
        "timing": {
            "start_time": "2024-07-20_00:00:00",
            "end_time": "2024-07-20_06:00:00",
            "forcing_interval_seconds": 10800,
            "history_interval_minutes": 60,
            "frames_per_outfile": 1,
            "restart": False,
        },
        "execution": {
            "run_mode": "local",
        },
        "domains": [default_domain(1)],
        "physics": {
            "mp_physics": 6,
            "cu_physics": 1,
            "ra_lw_physics": 4,
            "ra_sw_physics": 4,
            "bl_pbl_physics": 1,
            "sf_sfclay_physics": 1,
            "sf_surface_physics": 2,
        },
        "wps": {
            "share": {
                "wrf_core": "ARW",
                "interval_seconds": 10800,
                "io_form_geogrid": 2,
            },
            "geogrid": {
                "map_proj": "lambert",
                "ref_lat": 31.2,
                "ref_lon": 121.5,
                "truelat1": 31.2,
                "truelat2": 46.2,
                "stand_lon": 121.5,
                "ref_x": None,
                "ref_y": None,
                "pole_lat": 90.0,
                "pole_lon": 0.0,
                "geog_data_res": None,
            },
            "ungrib": {
                "out_format": "WPS",
                "prefix": None,
            },
            "metgrid": {
                "fg_name": None,
                "io_form_metgrid": 2,
            },
        },
        "model": {
            "namelist_input": {
                "time_control": {},
                "domains": {
                    "e_vert": 50,
                    "dzstretch_s": 1.1,
                    "p_top_requested": 5000,
                    "num_metgrid_levels": 34,
                    "num_metgrid_soil_levels": 4,
                    "feedback": 1,
                    "smooth_option": 0,
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
                },
                "namelist_quilt": {
                    "nio_tasks_per_group": 0,
                    "nio_groups": 1,
                },
            }
        },
        "experimental": {
            "raw_namelist_input": {},
            "raw_namelist_wps": {},
        },
    }


def _normalize_domains(domains: list[dict[str, Any]] | None, *, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_domains = domains or fallback
    normalized: list[dict[str, Any]] = []
    for index, raw_domain in enumerate(source_domains, start=1):
        normalized.append(deep_merge(default_domain(index), raw_domain))
    return normalized


def normalize_spec(spec: dict[str, Any], *, project_name_fallback: str | None = None) -> dict[str, Any]:
    seed_name = (
        str(spec.get("project_name") or "").strip()
        or str(project_name_fallback or "").strip()
        or "demo"
    )
    normalized = default_spec(seed_name)
    incoming = deepcopy(spec)

    schema_version = incoming.get("schema_version")
    if schema_version == DEFAULT_SPEC_VERSION:
        normalized = deep_merge(normalized, incoming)
    else:
        normalized["project_name"] = incoming.get("project_name") or normalized["project_name"]
        normalized["data_source"] = incoming.get("data_source") or normalized["data_source"]
        normalized["timing"]["start_time"] = incoming.get("start_time") or normalized["timing"]["start_time"]
        normalized["timing"]["end_time"] = incoming.get("end_time") or normalized["timing"]["end_time"]
        normalized["execution"]["run_mode"] = incoming.get("run_mode") or normalized["execution"]["run_mode"]
        if isinstance(incoming.get("physics"), dict):
            normalized["physics"] = deep_merge(normalized["physics"], incoming["physics"])
        if isinstance(incoming.get("domains"), list):
            normalized["domains"] = _normalize_domains(incoming["domains"], fallback=normalized["domains"])
        if isinstance(incoming.get("model"), dict):
            normalized["model"] = deep_merge(normalized["model"], incoming["model"])
        if isinstance(incoming.get("wps"), dict):
            normalized["wps"] = deep_merge(normalized["wps"], incoming["wps"])
        if isinstance(incoming.get("experimental"), dict):
            normalized["experimental"] = deep_merge(normalized["experimental"], incoming["experimental"])

    if "start_time" in incoming:
        normalized["timing"]["start_time"] = incoming["start_time"]
    if "end_time" in incoming:
        normalized["timing"]["end_time"] = incoming["end_time"]
    if "run_mode" in incoming:
        normalized["execution"]["run_mode"] = incoming["run_mode"]
    if "data_source" in incoming:
        normalized["data_source"] = incoming["data_source"]
    if "physics" in incoming and isinstance(incoming["physics"], dict):
        normalized["physics"] = deep_merge(normalized["physics"], incoming["physics"])
    if "domains" in incoming and isinstance(incoming["domains"], list):
        normalized["domains"] = _normalize_domains(incoming["domains"], fallback=normalized["domains"])

    outer_domain = normalized["domains"][0]
    geogrid = normalized["wps"]["geogrid"]
    geogrid.setdefault("map_proj", "lambert")
    geogrid["ref_lat"] = geogrid.get("ref_lat") if geogrid.get("ref_lat") is not None else outer_domain.get("ref_lat")
    geogrid["ref_lon"] = geogrid.get("ref_lon") if geogrid.get("ref_lon") is not None else outer_domain.get("ref_lon")
    geogrid["truelat1"] = geogrid.get("truelat1") if geogrid.get("truelat1") is not None else geogrid["ref_lat"]
    geogrid["truelat2"] = geogrid.get("truelat2") if geogrid.get("truelat2") is not None else float(geogrid["ref_lat"]) + 15.0
    geogrid["stand_lon"] = geogrid.get("stand_lon") if geogrid.get("stand_lon") is not None else geogrid["ref_lon"]

    for domain in normalized["domains"]:
        if domain.get("geog_data_res") is None:
            domain["geog_data_res"] = geogrid.get("geog_data_res", "default")
        domain.setdefault("physics", {})

    share = normalized["wps"]["share"]
    share["interval_seconds"] = int(
        share.get("interval_seconds")
        or normalized["timing"]["forcing_interval_seconds"]
    )
    normalized["timing"]["forcing_interval_seconds"] = int(share["interval_seconds"])
    normalized["wps"]["ungrib"]["prefix"] = str(
        normalized["wps"]["ungrib"].get("prefix") or normalized["data_source"]
    ).upper()
    normalized["wps"]["metgrid"]["fg_name"] = str(
        normalized["wps"]["metgrid"].get("fg_name") or normalized["data_source"]
    ).upper()

    for section in DEFAULT_SECTION_KEYS:
        normalized["model"]["namelist_input"].setdefault(section, {})

    normalized["schema_version"] = DEFAULT_SPEC_VERSION
    return normalized
