from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from constants import MULTI_DOMAIN_NAMELIST_KEYS, TEXT_TO_MODE, TEXT_TO_SOURCE, TIME_FORMAT
    from hpc.admission import evaluate_admission
    from hpc.base import resolve_access_mode
    from project_state import (
        assert_mutation_allowed,
        load_project,
        record_admission,
        register_artifact,
        reset_after_reconfigure,
        save_project,
    )
    from render_config import render_from_spec, validate_spec, write_rendered_targets
    from spec_utils import deep_merge, default_forcing_interval_seconds, normalize_spec
    from utils import coerce_value as coerce_override_value, dump_json, load_json, posix_path
except ImportError:  # pragma: no cover
    from .constants import MULTI_DOMAIN_NAMELIST_KEYS, TEXT_TO_MODE, TEXT_TO_SOURCE, TIME_FORMAT
    from .hpc.admission import evaluate_admission
    from .hpc.base import resolve_access_mode
    from .project_state import (
        assert_mutation_allowed,
        load_project,
        record_admission,
        register_artifact,
        reset_after_reconfigure,
        save_project,
    )
    from .render_config import render_from_spec, validate_spec, write_rendered_targets
    from .spec_utils import deep_merge, default_forcing_interval_seconds, normalize_spec
    from .utils import coerce_value as coerce_override_value, dump_json, load_json, posix_path
def sync_interval_defaults_for_source(spec: dict[str, Any], previous_source: str, next_source: str) -> None:
    if lowered == "null":
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        pass
    for caster in (int, float):
        try:
            return caster(raw_value)
        except ValueError:
            continue
    return raw_value


def parse_time_token(value: str) -> str:
    token = value.strip()
    for fmt in (
        "%Y-%m-%d_%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            dt = datetime.strptime(token, fmt)
            if "H" not in fmt:
                dt = dt.replace(hour=0, minute=0, second=0)
            elif "S" not in fmt:
                dt = dt.replace(second=0)
            return dt.strftime(TIME_FORMAT)
        except ValueError:
            continue
    raise ValueError(f"Unsupported time token: {value}")


def set_nested_value(container: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    current = container
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        is_next_index = next_part.isdigit()
        if isinstance(current, list):
            list_index = int(part)
            while len(current) <= list_index:
                current.append([] if is_next_index else {})
            current = current[list_index]
            continue

        if part not in current or current[part] is None:
            current[part] = [] if is_next_index else {}
        current = current[part]

    leaf = parts[-1]
    if isinstance(current, list):
        list_index = int(leaf)
        while len(current) <= list_index:
            current.append(None)
        current[list_index] = value
    else:
        current[leaf] = value


def is_supported_spec_override_path(path: str) -> bool:
    parts = path.split(".")
    if not parts:
        return False
    root = parts[0]
    if root in {"project_name", "data_source", "start_time", "end_time", "run_mode"}:
        return len(parts) == 1
    if root in {"timing", "execution", "physics"}:
        return len(parts) >= 2
    if root == "wps":
        return len(parts) >= 3
    if root == "model":
        return len(parts) >= 4 and parts[1] == "namelist_input"
    if root == "experimental":
        return len(parts) >= 4 and parts[1] in {"raw_namelist_input", "raw_namelist_wps"}
    if root != "domains" or len(parts) < 3 or not parts[1].isdigit():
        return False
    if len(parts) == 3:
        return True
    return parts[2] == "physics" and len(parts) >= 4


def parse_overrides(entries: list[str] | None) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if not entries:
        return overrides
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"Invalid override, expected key=value: {entry}")
        key, raw_value = entry.split("=", 1)
        if not is_supported_spec_override_path(key):
            raise ValueError(f"Unsupported spec override path: {key}")
        overrides[key] = coerce_override_value(raw_value)
    return overrides


def parse_namelist_overrides(entries: list[str] | None) -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    if not entries:
        return overrides

    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"Invalid namelist override, expected section.key=value: {entry}")
        key_path, raw_value = entry.split("=", 1)
        if "." not in key_path:
            raise ValueError(f"Invalid namelist override path: {key_path}")
        section, key = key_path.split(".", 1)
        overrides.setdefault(section, {})[key] = coerce_override_value(raw_value)
    return overrides


def load_spec_fragment(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return load_json(path)


def integer_ratio(parent_value: float, child_value: float) -> int:
    ratio = parent_value / child_value
    rounded = round(ratio)
    if rounded < 1 or abs(ratio - rounded) > 1e-6:
        raise ValueError(
            f"Child spacing must divide parent spacing exactly: parent={parent_value}, child={child_value}"
        )
    return int(rounded)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def resolve_catalog_matches(
    request_text: str,
    catalog: dict[str, Any],
    *,
    description_fields: tuple[str, ...],
) -> list[str]:
    normalized = normalize_text(request_text)
    hits: list[tuple[int, str]] = []
    for key, payload in catalog.items():
        terms = [key.replace("_", " ")]
        aliases = payload.get("aliases", [])
        if isinstance(aliases, list):
            terms.extend(str(alias) for alias in aliases)
        for field in description_fields:
            value = payload.get(field)
            if isinstance(value, list):
                terms.extend(str(item) for item in value)
            elif isinstance(value, str):
                terms.append(value)

        positions = [normalized.find(term.lower()) for term in terms if term]
        positions = [position for position in positions if position >= 0]
        if positions:
            hits.append((min(positions), key))

    hits.sort()
    ordered: list[str] = []
    for _, key in hits:
        if key not in ordered:
            ordered.append(key)
    return ordered


def parse_duration_hours(request_text: str) -> int | None:
    match = re.search(r"(\d+)\s*(小时|小時|hours?|hrs?|h)\b", request_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def parse_times_from_text(request_text: str) -> tuple[str | None, str | None]:
    time_pattern = re.compile(
        r"\d{4}[-/]\d{2}[-/]\d{2}(?:[ _]\d{2}:\d{2}(?::\d{2})?)?"
    )
    matches = [parse_time_token(match.group(0)) for match in time_pattern.finditer(request_text)]
    if len(matches) >= 2:
        return matches[0], matches[1]
    if len(matches) == 1:
        duration_hours = parse_duration_hours(request_text)
        if duration_hours is None:
            return matches[0], None
        start_dt = datetime.strptime(matches[0], TIME_FORMAT)
        end_dt = start_dt + timedelta(hours=duration_hours)
        return matches[0], end_dt.strftime(TIME_FORMAT)
    return None, None


def parse_request_text(
    request_text: str,
    domains_catalog: dict[str, Any],
    physics_catalog: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_text(request_text)
    start_time, end_time = parse_times_from_text(request_text)

    data_source = None
    for token, value in TEXT_TO_SOURCE.items():
        if token in normalized:
            data_source = value
            break

    run_mode = None
    for token, value in TEXT_TO_MODE.items():
        if token in normalized:
            run_mode = value
            break

    domain_presets = resolve_catalog_matches(
        request_text,
        domains_catalog,
        description_fields=("description", "aliases"),
    )
    physics_matches = resolve_catalog_matches(
        request_text,
        physics_catalog,
        description_fields=("description", "aliases", "recommended_for"),
    )

    return {
        "start_time": start_time,
        "end_time": end_time,
        "data_source": data_source,
        "run_mode": run_mode,
        "domain_presets": domain_presets,
        "physics_preset": physics_matches[0] if physics_matches else None,
    }


def build_domains_from_presets(
    preset_names: list[str],
    presets: dict[str, Any],
) -> list[dict[str, Any]]:
    if not preset_names:
        raise ValueError("At least one domain preset is required")

    domains: list[dict[str, Any]] = []
    for index, preset_name in enumerate(preset_names, start=1):
        if preset_name not in presets:
            raise KeyError(f"Unknown domain preset: {preset_name}")
        preset = presets[preset_name]
        if index == 1:
            parent_id = 1
            parent_grid_ratio = 1
            i_parent_start = 1
            j_parent_start = 1
        else:
            parent_domain = domains[-1]
            parent_id = index - 1
            parent_grid_ratio = integer_ratio(
                float(parent_domain["dx_km"]),
                float(preset["default_dx_km"]),
            )
            i_parent_start = 30
            j_parent_start = 30

        domains.append(
            {
                "name": f"d{index:02d}",
                "preset_name": preset_name,
                "parent_id": parent_id,
                "parent_grid_ratio": parent_grid_ratio,
                "dx_km": preset["default_dx_km"],
                "dy_km": preset["default_dy_km"],
                "e_we": preset["default_e_we"],
                "e_sn": preset["default_e_sn"],
                "i_parent_start": i_parent_start,
                "j_parent_start": j_parent_start,
                "ref_lat": preset["ref_lat"],
                "ref_lon": preset["ref_lon"],
                "geog_data_res": None,
                "physics": {},
            }
        )
    return domains


def apply_inputs_to_spec(
    base_spec: dict[str, Any],
    *,
    domain_presets: list[str] | None,
    physics_preset: str | None,
    domains_catalog: dict[str, Any],
    physics_catalog: dict[str, Any],
    start_time: str | None,
    end_time: str | None,
    data_source: str | None,
    run_mode: str | None,
    spec_fragment: dict[str, Any] | None,
    overrides: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = normalize_spec(base_spec)
    applied = {
        "domain_presets": domain_presets or [],
        "physics_preset": physics_preset,
        "spec_fragment": spec_fragment or {},
        "overrides": overrides,
    }

    if domain_presets:
        spec["domains"] = build_domains_from_presets(domain_presets, domains_catalog)
        outer_domain = spec["domains"][0]
        spec["wps"]["geogrid"].update(
            {
                "ref_lat": outer_domain["ref_lat"],
                "ref_lon": outer_domain["ref_lon"],
                "truelat1": outer_domain["ref_lat"],
                "truelat2": float(outer_domain["ref_lat"]) + 15.0,
                "stand_lon": outer_domain["ref_lon"],
            }
        )
    if physics_preset:
        if physics_preset not in physics_catalog:
            raise KeyError(f"Unknown physics preset: {physics_preset}")
        spec["physics"] = deep_merge(spec["physics"], physics_catalog[physics_preset]["physics"])
    if start_time:
        spec["timing"]["start_time"] = start_time
    if end_time:
        spec["timing"]["end_time"] = end_time
    if data_source:
        previous_source = str(spec["data_source"]).lower()
        next_source = str(data_source).lower()
        previous_source_label = previous_source.upper()
        next_source_label = next_source.upper()
        if str(spec["wps"]["ungrib"].get("prefix") or "").upper() == previous_source_label:
            spec["wps"]["ungrib"]["prefix"] = next_source_label
        if str(spec["wps"]["metgrid"].get("fg_name") or "").upper() == previous_source_label:
            spec["wps"]["metgrid"]["fg_name"] = next_source_label
        spec["data_source"] = next_source
        sync_interval_defaults_for_source(spec, previous_source, next_source)
    if run_mode:
        spec["execution"]["run_mode"] = run_mode
    if spec_fragment:
        spec = deep_merge(spec, spec_fragment)

    for key_path, value in overrides.items():
        set_nested_value(spec, key_path, value)

    return normalize_spec(spec), applied


def apply_namelist_overrides(
    rendered: dict[str, dict[str, dict[str, Any]]],
    namelist_overrides: dict[str, dict[str, Any]],
    *,
    domain_count: int,
) -> dict[str, dict[str, dict[str, Any]]]:
    updated = deepcopy(rendered)
    input_config = updated["namelist.input"]
    for section, values in namelist_overrides.items():
        target_section = input_config.setdefault(section, {})
        for key, value in values.items():
            base_value = target_section.get(key)
            if key in MULTI_DOMAIN_NAMELIST_KEYS.get(section, set()) and not isinstance(value, list):
                target_section[key] = [value for _ in range(domain_count)]
            elif isinstance(base_value, list) and not isinstance(value, list):
                target_section[key] = [value for _ in range(domain_count)]
            else:
                target_section[key] = value
    return updated


def sync_project_state(state: dict[str, Any], spec: dict[str, Any], config: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    reset_after_reconfigure(state)
    state["data_source"]["type"] = spec["data_source"]
    state["data_source"]["start_time"] = spec["timing"]["start_time"]
    state["data_source"]["end_time"] = spec["timing"]["end_time"]
    state["execution"]["mode"] = spec["execution"]["run_mode"]
    state["execution"]["access_mode"] = resolve_access_mode(config) if spec["execution"]["run_mode"] == "hpc" else None
    state["execution"]["dry_run"] = dry_run
    return state


def build_targets(project_root: Path) -> dict[str, Path]:
    return {
        "namelist.wps": project_root / "wps" / "namelist.wps",
        "namelist.input": project_root / "wrf" / "namelist.input",
    }


def write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def configure_project(
    project_name: str,
    *,
    runs_dir: Path | str = "runs",
    config_path: Path | str = "config/wrf_env.json",
    domains_config: Path | str = "config/domains_presets.json",
    physics_config: Path | str = "config/physics_schemes.json",
    spec_json: Path | str | None = None,
    spec_fragment_json: Path | str | None = None,
    domain_presets: list[str] | None = None,
    physics_preset: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    data_source: str | None = None,
    run_mode: str | None = None,
    request_text: str | None = None,
    override_entries: list[str] | None = None,
    namelist_override_entries: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    runs_dir = Path(runs_dir)
    config_path = Path(config_path)
    project_root = runs_dir / project_name
    project_json_path = project_root / "project.json"
    spec_path = Path(spec_json) if spec_json else project_root / "simulation_spec.json"
    log_path = project_root / "logs" / "wrf-config.log"

    if not project_json_path.exists():
        raise FileNotFoundError(f"Missing project.json: {project_json_path}")
    if not spec_path.exists():
        raise FileNotFoundError(f"Missing simulation spec: {spec_path}")

    config = load_json(config_path)
    domains_catalog = load_json(domains_config)
    physics_catalog = load_json(physics_config)
    base_state = load_project(project_json_path)
    assert_mutation_allowed(base_state, "wrf-config")
    base_spec = load_json(spec_path)
    spec_fragment = load_spec_fragment(spec_fragment_json)
    overrides = parse_overrides(override_entries)
    namelist_overrides = parse_namelist_overrides(namelist_override_entries)
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
        spec_fragment=spec_fragment,
        overrides=overrides,
    )

    spec_errors = validate_spec(spec)
    if spec_errors:
        raise ValueError("; ".join(spec_errors))

    rendered = render_from_spec(
        spec,
        config["geog_data_path"],
        str(config.get("geog_data_res") or "default"),
    )
    if namelist_overrides:
        rendered = apply_namelist_overrides(
            rendered,
            namelist_overrides,
            domain_count=len(spec["domains"]),
        )

    targets = build_targets(project_root)
    state = deepcopy(base_state)
    admission = None
    if spec["execution"]["run_mode"] == "hpc":
        admission = evaluate_admission(spec, config)
        record_admission(state, admission)

    accepted = admission is None or admission["decision"] in {"admissible_now", "admissible_with_queue"}
    if accepted:
        sync_project_state(state, spec, config, dry_run=dry_run)

    plan = {
        "project_root": posix_path(project_root),
        "targets": {name: posix_path(path) for name, path in targets.items()},
        "applied": applied,
        "request_text": request_text,
        "inferred": inferred,
        "namelist_overrides": namelist_overrides,
        "admission": admission,
        "accepted": accepted,
        "schema_version": spec["schema_version"],
    }

    if dry_run:
        return {
            "dry_run": True,
            "accepted": accepted,
            "project": state,
            "simulation_spec": spec,
            "plan": plan,
            "admission": admission,
        }

    log_lines = [
        f"wrf-config project={project_name}",
        f"spec={posix_path(spec_path)}",
        f"domains={len(spec['domains'])}",
        f"run_mode={spec['execution']['run_mode']}",
        f"schema_version={spec['schema_version']}",
        f"physics_preset={physics_preset or '(unchanged)'}",
        f"domain_presets={','.join(domain_presets or []) or '(unchanged)'}",
        f"request_text={request_text or '(none)'}",
        f"spec_fragment={posix_path(Path(spec_fragment_json)) if spec_fragment_json else '(none)'}",
        f"namelist_overrides={json.dumps(namelist_overrides, ensure_ascii=True, sort_keys=True)}",
    ]
    if admission is not None:
        log_lines.extend(
            [
                f"admission_decision={admission['decision']}",
                f"admission_reason_codes={','.join(admission['reason_codes']) or '(none)'}",
            ]
        )

    if not accepted:
        save_project(state, project_json_path)
        write_log(log_path, log_lines + ["written=(none)", "config_blocked=true"])
        return {
            "dry_run": False,
            "accepted": False,
            "project": state,
            "simulation_spec_path": posix_path(spec_path),
            "written": [],
            "log_path": posix_path(log_path),
            "plan": plan,
            "admission": admission,
        }

    written = write_rendered_targets(rendered, targets)
    dump_json(spec_path, spec)
    register_artifact(state, "namelist_wps", posix_path(targets["namelist.wps"]))
    register_artifact(state, "namelist_input", posix_path(targets["namelist.input"]))
    save_project(state, project_json_path)
    write_log(
        log_path,
        log_lines + [f"written={','.join(posix_path(Path(path)) for path in written)}", "config_blocked=false"],
    )

    return {
        "dry_run": False,
        "accepted": True,
        "project": state,
        "simulation_spec_path": posix_path(spec_path),
        "written": [posix_path(Path(path)) for path in written],
        "log_path": posix_path(log_path),
        "plan": plan,
        "admission": admission,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configure a WRF project and render namelists")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--config", default="config/wrf_env.json")
    parser.add_argument("--domains-config", default="config/domains_presets.json")
    parser.add_argument("--physics-config", default="config/physics_schemes.json")
    parser.add_argument("--spec-json")
    parser.add_argument("--spec-fragment-json")
    parser.add_argument("--domain-preset", action="append", dest="domain_presets")
    parser.add_argument("--physics-preset")
    parser.add_argument("--start-time")
    parser.add_argument("--end-time")
    parser.add_argument("--data-source", choices=("gfs", "era5", "fnl"))
    parser.add_argument("--run-mode", choices=("local", "hpc"))
    parser.add_argument("--request-text")
    parser.add_argument("--override", action="append", dest="override_entries")
    parser.add_argument("--namelist-override", action="append", dest="namelist_override_entries")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = configure_project(
        args.project_name,
        runs_dir=args.runs_dir,
        config_path=args.config,
        domains_config=args.domains_config,
        physics_config=args.physics_config,
        spec_json=args.spec_json,
        spec_fragment_json=args.spec_fragment_json,
        domain_presets=args.domain_presets,
        physics_preset=args.physics_preset,
        start_time=args.start_time,
        end_time=args.end_time,
        data_source=args.data_source,
        run_mode=args.run_mode,
        request_text=args.request_text,
        override_entries=args.override_entries,
        namelist_override_entries=args.namelist_override_entries,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
