from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from spec_utils import TIME_FORMAT, deep_merge
except ImportError:  # pragma: no cover
    from .spec_utils import TIME_FORMAT, deep_merge

DEFAULT_POST_SPEC_VERSION = 2
ALLOWED_INPUT_MODES = {"project_artifacts", "explicit_paths", "glob"}
DEFAULT_SECTION_KEYS = ("inputs", "selectors", "render", "output")
SUPPORTED_SOURCE_KINDS = {
    "wrf_native",
    "wrf_native_2d",
    "wrf_native_3d",
    "wrf_native_3d_full",
    "wrf_diag",
}
SUPPORTED_NATIVE_VIEW_AXES = {
    "time",
    "bottom_top",
    "south_north",
    "west_east",
}
SUPPORTED_DERIVED_VIEW_AXES = {
    "height_m",
}
SUPPORTED_PATH_VIEW_AXES = {
    "distance_km",
}
SUPPORTED_VIEW_AXES = (
    SUPPORTED_NATIVE_VIEW_AXES
    | SUPPORTED_DERIVED_VIEW_AXES
    | SUPPORTED_PATH_VIEW_AXES
)
ROOT_RESERVED_KEYS = {
    "schema_version",
    "project_name",
    "defaults",
    "style_defs",
    "view_defs",
    "layer_defs",
    "figures",
}


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path | str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def default_post_defaults() -> dict[str, Any]:
    return {
        "inputs": {
            "mode": "project_artifacts",
            "artifact": "wrfout_files",
        },
        "selectors": {
            "domain": None,
            "time_indices": None,
            "time_range": {
                "start": None,
                "end": None,
            },
            "max_files": None,
        },
        "render": {
            "format": "png",
            "title": None,
            "dpi": 150,
        },
        "output": {
            "subdir": "plots",
            "file_stem": None,
            "sidecar_json": True,
            "overwrite": False,
        },
    }


def default_layer_defs() -> dict[str, dict[str, Any]]:
    return {
        "terrain": {
            "source": {"kind": "wrf_native_2d"},
            "expr": "first(HGT)",
            "units": "m",
            "metadata": {
                "description": "Terrain height from WRF output",
            },
        },
        "landmask": {
            "source": {"kind": "wrf_native_2d"},
            "expr": "first(LANDMASK)",
            "units": None,
            "metadata": {
                "description": "Land-sea mask from WRF output",
            },
        },
        "t2_c": {
            "source": {"kind": "wrf_native_2d"},
            "expr": "T2 - 273.15",
            "units": "C",
            "metadata": {
                "description": "2m temperature in Celsius",
            },
        },
        "wind10m": {
            "source": {"kind": "wrf_diag"},
            "expr": "wind_speed_10m",
            "units": "m s-1",
            "metadata": {
                "description": "10m wind speed magnitude from built-in diagnostics",
            },
        },
        "u10": {
            "source": {"kind": "wrf_native_2d"},
            "expr": "U10",
            "units": "m s-1",
            "metadata": {
                "description": "10m zonal wind component",
            },
        },
        "v10": {
            "source": {"kind": "wrf_native_2d"},
            "expr": "V10",
            "units": "m s-1",
            "metadata": {
                "description": "10m meridional wind component",
            },
        },
        "qvapor_lvl0_gkg": {
            "source": {
                "kind": "wrf_native_3d",
                "level_selector": {"mode": "first"},
            },
            "expr": "QVAPOR * 1000",
            "units": "g kg-1",
            "metadata": {
                "description": "Lowest model-level water vapor mixing ratio in g kg-1",
            },
        },
        "accum_precip": {
            "source": {"kind": "wrf_diag"},
            "expr": "last(total_precip) - first(total_precip)",
            "units": "mm",
            "metadata": {
                "description": "Accumulated precipitation over the selected frame range",
            },
        },
    }


def default_style_defs() -> dict[str, dict[str, Any]]:
    return {
        "temperature_raster": {
            "kind": "raster",
            "alpha": 1.0,
            "zorder": 10,
            "style": {
                "colormap": "coolwarm",
                "show_colorbar": True,
            },
        },
        "terrain_contours": {
            "kind": "contour",
            "alpha": 0.9,
            "zorder": 20,
            "style": {
                "levels": [0, 500, 1000, 1500, 2000],
                "colors": "black",
                "linewidths": 0.5,
            },
        },
        "precip_raster": {
            "kind": "raster",
            "alpha": 0.95,
            "zorder": 10,
            "style": {
                "colormap": "Blues",
                "show_colorbar": True,
            },
        },
        "landmask_fill": {
            "kind": "categorical_fill",
            "alpha": 0.35,
            "zorder": 1,
            "style": {
                "categories": [
                    {"value": 0, "color": "#4c78a8", "label": "water"},
                    {"value": 1, "color": "#e0c36e", "label": "land"},
                ],
            },
        },
        "wind_quiver": {
            "kind": "vector",
            "alpha": 0.9,
            "zorder": 30,
            "style": {
                "mode": "quiver",
                "stride": 4,
                "scale": 80,
                "color": "black",
                "pivot": "mid",
            },
        },
    }


def default_figure_spec() -> dict[str, Any]:
    defaults = default_post_defaults()
    return {
        "figure_id": "surface_temperature",
        "inputs": deepcopy(defaults["inputs"]),
        "selectors": deepcopy(defaults["selectors"]),
        "render": deep_merge(
            defaults["render"],
            {
                "title": "2m Temperature with Terrain",
            },
        ),
        "output": deep_merge(
            defaults["output"],
            {
                "file_stem": "surface-temperature",
            },
        ),
        "layers": [
            {
                "layer_id": "t2_c",
                "style_id": "temperature_raster",
            },
            {
                "layer_id": "terrain",
                "style_id": "terrain_contours",
            },
        ],
    }


def default_view_spec() -> dict[str, Any]:
    return {
        "x_axis": {"name": "west_east"},
        "y_axis": {"name": "south_north"},
        "selectors": {},
    }


def _empty_post_spec(project_name: str = "demo") -> dict[str, Any]:
    return {
        "schema_version": DEFAULT_POST_SPEC_VERSION,
        "project_name": project_name,
        "defaults": default_post_defaults(),
        "style_defs": {},
        "view_defs": {},
        "layer_defs": {},
        "figures": [],
    }


def default_post_spec(project_name: str = "demo", *, product_name: str | None = None) -> dict[str, Any]:
    del product_name
    return {
        "schema_version": DEFAULT_POST_SPEC_VERSION,
        "project_name": project_name,
        "defaults": default_post_defaults(),
        "style_defs": default_style_defs(),
        "view_defs": {},
        "layer_defs": default_layer_defs(),
        "figures": [default_figure_spec()],
    }


def _seed_project_name(spec: dict[str, Any], project_name_fallback: str | None) -> str:
    candidate = spec.get("project_name")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    if isinstance(project_name_fallback, str) and project_name_fallback.strip():
        return project_name_fallback.strip()
    return "demo"


def _normalize_source(raw_source: Any) -> dict[str, Any]:
    base = {"kind": "wrf_native"}
    if isinstance(raw_source, dict):
        return deep_merge(base, raw_source)
    return base


def _normalize_layer_def(raw_layer: Any) -> dict[str, Any]:
    normalized = {
        "source": {"kind": "wrf_native"},
        "expr": "",
        "units": None,
        "metadata": {},
    }
    if not isinstance(raw_layer, dict):
        normalized["expr"] = str(raw_layer)
        return normalized

    for key, value in raw_layer.items():
        if key not in {"source", "expr", "units", "metadata"}:
            normalized[key] = deepcopy(value)
    normalized["source"] = _normalize_source(raw_layer.get("source"))
    normalized["expr"] = raw_layer.get("expr", "")
    normalized["units"] = raw_layer.get("units")
    metadata = raw_layer.get("metadata")
    normalized["metadata"] = deepcopy(metadata) if isinstance(metadata, dict) else {}
    return normalized


def _normalize_draw(raw_draw: Any, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "kind": None,
        "alpha": 1.0,
        "zorder": None,
        "style": {},
    }
    if isinstance(base, dict):
        normalized = deep_merge(normalized, deepcopy(base))
    if not isinstance(raw_draw, dict):
        return normalized

    for key, value in raw_draw.items():
        if key not in {"kind", "alpha", "zorder", "style"}:
            normalized[key] = deepcopy(value)
    if "kind" in raw_draw:
        normalized["kind"] = raw_draw.get("kind")
    if "alpha" in raw_draw:
        normalized["alpha"] = raw_draw.get("alpha")
    if "zorder" in raw_draw:
        normalized["zorder"] = raw_draw.get("zorder")
    style = raw_draw.get("style")
    if isinstance(style, dict):
        normalized["style"] = deep_merge(normalized["style"], style)
    elif "style" in raw_draw:
        normalized["style"] = {}
    return normalized


def _normalize_style_def(raw_style: Any) -> dict[str, Any]:
    return _normalize_draw(raw_style)


def _normalize_view_axis(raw_axis: Any, *, fallback_name: str) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "name": fallback_name,
        "label": None,
        "units": None,
    }
    if isinstance(raw_axis, dict):
        for key, value in raw_axis.items():
            if key not in {"name", "label", "units"}:
                normalized[key] = deepcopy(value)
        if raw_axis.get("name") is not None:
            normalized["name"] = raw_axis.get("name")
        if raw_axis.get("label") is not None:
            normalized["label"] = raw_axis.get("label")
        if raw_axis.get("units") is not None:
            normalized["units"] = raw_axis.get("units")
        return normalized
    if raw_axis is not None:
        normalized["name"] = raw_axis
    return normalized


def _normalize_view_def(raw_view: Any) -> dict[str, Any]:
    base = default_view_spec()
    if not isinstance(raw_view, dict):
        return base

    normalized: dict[str, Any] = {
        key: deepcopy(value)
        for key, value in raw_view.items()
        if key not in {"x_axis", "y_axis", "selectors"}
    }
    normalized["x_axis"] = _normalize_view_axis(
        raw_view.get("x_axis"),
        fallback_name=str(base["x_axis"]["name"]),
    )
    normalized["y_axis"] = _normalize_view_axis(
        raw_view.get("y_axis"),
        fallback_name=str(base["y_axis"]["name"]),
    )

    selectors = raw_view.get("selectors")
    if isinstance(selectors, dict):
        normalized["selectors"] = {
            str(dim): deepcopy(selector)
            for dim, selector in selectors.items()
        }
    else:
        normalized["selectors"] = {}
    return normalized


def _normalize_render_layer(
    raw_layer: Any,
    style_defs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "layer_id": None,
        "u_layer_id": None,
        "v_layer_id": None,
        "style_id": None,
        "draw": _normalize_draw({}),
    }
    if not isinstance(raw_layer, dict):
        normalized["layer_id"] = str(raw_layer)
        return normalized

    style_id = raw_layer.get("style_id")
    base_style = {}
    if isinstance(style_id, str) and style_id in style_defs:
        base_style = style_defs[style_id]

    for key, value in raw_layer.items():
        if key not in {"layer_id", "u_layer_id", "v_layer_id", "style_id", "draw"}:
            normalized[key] = deepcopy(value)
    normalized["layer_id"] = raw_layer.get("layer_id")
    normalized["u_layer_id"] = raw_layer.get("u_layer_id")
    normalized["v_layer_id"] = raw_layer.get("v_layer_id")
    normalized["style_id"] = style_id
    normalized["draw"] = _normalize_draw(raw_layer.get("draw"), base=base_style)
    return normalized


def _normalize_figure(
    raw_figure: Any,
    defaults: dict[str, Any],
    style_defs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    base = {
        "figure_id": None,
        "view_id": None,
        "inputs": deep_merge(default_post_defaults()["inputs"], defaults.get("inputs", {})),
        "selectors": deep_merge(
            default_post_defaults()["selectors"],
            defaults.get("selectors", {}),
        ),
        "render": deep_merge(default_post_defaults()["render"], defaults.get("render", {})),
        "output": deep_merge(default_post_defaults()["output"], defaults.get("output", {})),
        "layers": [],
    }
    if not isinstance(raw_figure, dict):
        base["figure_id"] = str(raw_figure)
        return base

    normalized: dict[str, Any] = {
        key: deepcopy(value)
        for key, value in raw_figure.items()
        if key not in {"figure_id", "view_id", "view", *DEFAULT_SECTION_KEYS, "layers"}
    }
    normalized["figure_id"] = raw_figure.get("figure_id")
    normalized["view_id"] = raw_figure.get("view_id")
    normalized["view"] = _normalize_view_def(raw_figure.get("view")) if "view" in raw_figure else None

    for section in DEFAULT_SECTION_KEYS:
        merged = deepcopy(base[section])
        raw_value = raw_figure.get(section)
        if isinstance(raw_value, dict):
            normalized[section] = deep_merge(merged, raw_value)
        elif raw_value is None:
            normalized[section] = merged
        else:
            normalized[section] = deepcopy(raw_value)

    raw_layers = raw_figure.get("layers")
    if isinstance(raw_layers, list):
        normalized["layers"] = [_normalize_render_layer(item, style_defs) for item in raw_layers]
    else:
        normalized["layers"] = []
    return normalized


def normalize_post_spec(
    spec: dict[str, Any],
    *,
    project_name_fallback: str | None = None,
) -> dict[str, Any]:
    incoming = deepcopy(spec)
    seed_name = _seed_project_name(incoming, project_name_fallback)
    normalized = _empty_post_spec(seed_name)

    for key, value in incoming.items():
        if key not in ROOT_RESERVED_KEYS:
            normalized[key] = deepcopy(value)

    if "schema_version" in incoming:
        normalized["schema_version"] = deepcopy(incoming["schema_version"])
    if "project_name" in incoming:
        normalized["project_name"] = deepcopy(incoming["project_name"])

    raw_defaults = incoming.get("defaults")
    if isinstance(raw_defaults, dict):
        normalized["defaults"] = deep_merge(normalized["defaults"], raw_defaults)
    elif raw_defaults is not None:
        normalized["defaults"] = deepcopy(raw_defaults)

    raw_style_defs = incoming.get("style_defs")
    if isinstance(raw_style_defs, dict):
        normalized["style_defs"] = {
            str(style_id): _normalize_style_def(style_def)
            for style_id, style_def in raw_style_defs.items()
        }

    raw_view_defs = incoming.get("view_defs")
    if isinstance(raw_view_defs, dict):
        normalized["view_defs"] = {
            str(view_id): _normalize_view_def(view_def)
            for view_id, view_def in raw_view_defs.items()
        }

    raw_layer_defs = incoming.get("layer_defs")
    if isinstance(raw_layer_defs, dict):
        normalized["layer_defs"] = {
            str(layer_id): _normalize_layer_def(layer_def)
            for layer_id, layer_def in raw_layer_defs.items()
        }

    raw_figures = incoming.get("figures")
    defaults = normalized["defaults"] if isinstance(normalized["defaults"], dict) else {}
    style_defs = normalized["style_defs"] if isinstance(normalized["style_defs"], dict) else {}
    if isinstance(raw_figures, list):
        normalized["figures"] = [
            _normalize_figure(item, defaults, style_defs)
            for item in raw_figures
        ]

    if (
        "project_name" not in incoming
        or not isinstance(normalized["project_name"], str)
        or not normalized["project_name"].strip()
    ):
        normalized["project_name"] = seed_name
    if "schema_version" not in incoming:
        normalized["schema_version"] = DEFAULT_POST_SPEC_VERSION

    return normalized


def _is_valid_time_token(value: str) -> bool:
    try:
        datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        return False
    return True


def _validate_defaults(defaults: Any, errors: list[str]) -> None:
    if defaults is None:
        return
    if not isinstance(defaults, dict):
        errors.append("defaults must be an object when provided")
        return
    for section in DEFAULT_SECTION_KEYS:
        value = defaults.get(section)
        if value is not None and not isinstance(value, dict):
            errors.append(f"defaults.{section} must be an object when provided")


def _validate_inputs(inputs: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(inputs, dict):
        errors.append(f"{prefix} must be an object")
        return

    mode = inputs.get("mode")
    if not isinstance(mode, str) or mode not in ALLOWED_INPUT_MODES:
        errors.append(
            f"{prefix}.mode must be one of {', '.join(sorted(ALLOWED_INPUT_MODES))}"
        )
        return

    if mode == "project_artifacts":
        artifact = inputs.get("artifact")
        if not isinstance(artifact, str) or not artifact.strip():
            errors.append(f"{prefix}.artifact must be a non-empty string in project_artifacts mode")
        return

    if mode == "explicit_paths":
        paths = inputs.get("paths")
        if not isinstance(paths, list) or not paths:
            errors.append(f"{prefix}.paths must be a non-empty list in explicit_paths mode")
            return
        for path_index, value in enumerate(paths):
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.paths[{path_index}] must be a non-empty string")
        return

    pattern = inputs.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        errors.append(f"{prefix}.pattern must be a non-empty string in glob mode")


def _validate_selectors(selectors: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(selectors, dict):
        errors.append(f"{prefix} must be an object")
        return

    domain = selectors.get("domain")
    if domain is not None and (not isinstance(domain, str) or not domain.strip()):
        errors.append(f"{prefix}.domain must be a non-empty string or null")

    time_indices = selectors.get("time_indices")
    if time_indices is not None:
        if not isinstance(time_indices, list):
            errors.append(f"{prefix}.time_indices must be a list or null")
        else:
            for item_index, value in enumerate(time_indices):
                if not isinstance(value, int) or value < 0:
                    errors.append(
                        f"{prefix}.time_indices[{item_index}] must be a non-negative integer"
                    )

    time_range = selectors.get("time_range")
    if time_range is not None:
        if not isinstance(time_range, dict):
            errors.append(f"{prefix}.time_range must be an object or null")
        else:
            for key in ("start", "end"):
                value = time_range.get(key)
                if value is not None and (
                    not isinstance(value, str) or not _is_valid_time_token(value)
                ):
                    errors.append(
                        f"{prefix}.time_range.{key} must match {TIME_FORMAT} or be null"
                    )

    max_files = selectors.get("max_files")
    if max_files is not None and (not isinstance(max_files, int) or max_files < 1):
        errors.append(f"{prefix}.max_files must be a positive integer or null")


def _validate_render(render: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(render, dict):
        errors.append(f"{prefix} must be an object")
        return

    value = render.get("format")
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{prefix}.format must be a non-empty string")

    dpi = render.get("dpi")
    if not isinstance(dpi, int) or dpi < 1:
        errors.append(f"{prefix}.dpi must be a positive integer")

    title = render.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        errors.append(f"{prefix}.title must be a non-empty string or null")


def _validate_output(output: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(output, dict):
        errors.append(f"{prefix} must be an object")
        return

    subdir = output.get("subdir")
    if not isinstance(subdir, str) or not subdir.strip():
        errors.append(f"{prefix}.subdir must be a non-empty string")

    file_stem = output.get("file_stem")
    if file_stem is not None and (not isinstance(file_stem, str) or not file_stem.strip()):
        errors.append(f"{prefix}.file_stem must be a non-empty string or null")

    for key in ("sidecar_json", "overwrite"):
        value = output.get(key)
        if not isinstance(value, bool):
            errors.append(f"{prefix}.{key} must be a boolean")


def _validate_layer_def(layer_id: str, layer_def: Any, errors: list[str]) -> None:
    prefix = f"layer_defs.{layer_id}"
    if not layer_id.strip():
        errors.append("layer_defs keys must be non-empty strings")
        return
    if not isinstance(layer_def, dict):
        errors.append(f"{prefix} must be an object")
        return

    source = layer_def.get("source")
    if not isinstance(source, dict):
        errors.append(f"{prefix}.source must be an object")
    else:
        kind = source.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            errors.append(f"{prefix}.source.kind must be a non-empty string")
        elif kind == "wrf_native_3d_full":
            level_selector = source.get("level_selector")
            if level_selector is not None and not isinstance(level_selector, dict):
                errors.append(
                    f"{prefix}.source.level_selector must be an object or null for wrf_native_3d_full"
                )
        elif kind == "wrf_native_3d":
            level_selector = source.get("level_selector")
            if not isinstance(level_selector, dict):
                errors.append(f"{prefix}.source.level_selector must be an object for wrf_native_3d")
            else:
                mode = level_selector.get("mode")
                if mode not in {"index", "first", "last"}:
                    errors.append(
                        f"{prefix}.source.level_selector.mode must be one of first, index, last"
                    )
                if mode == "index":
                    index = level_selector.get("index")
                    if not isinstance(index, int) or index < 0:
                        errors.append(
                            f"{prefix}.source.level_selector.index must be a non-negative integer for mode=index"
                        )

    expr = layer_def.get("expr")
    if not isinstance(expr, str) or not expr.strip():
        errors.append(f"{prefix}.expr must be a non-empty string")

    units = layer_def.get("units")
    if units is not None and (not isinstance(units, str) or not units.strip()):
        errors.append(f"{prefix}.units must be a non-empty string or null")

    metadata = layer_def.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(f"{prefix}.metadata must be an object")


def _validate_style_def(style_id: str, style_def: Any, errors: list[str]) -> None:
    prefix = f"style_defs.{style_id}"
    if not style_id.strip():
        errors.append("style_defs keys must be non-empty strings")
        return
    _validate_draw(style_def, prefix, errors)


def _view_axis_name(axis: Any) -> str:
    if isinstance(axis, dict):
        token = axis.get("name")
        return str(token).strip() if token is not None else ""
    if axis is None:
        return ""
    return str(axis).strip()


def _view_axis_kind(axis: Any) -> str:
    if isinstance(axis, dict):
        raw_kind = axis.get("kind")
        if isinstance(raw_kind, str) and raw_kind.strip():
            return raw_kind.strip()
    name = _view_axis_name(axis)
    if name in SUPPORTED_NATIVE_VIEW_AXES:
        return "native_dim"
    if name in SUPPORTED_DERIVED_VIEW_AXES:
        return "derived_coord"
    if name in SUPPORTED_PATH_VIEW_AXES:
        return "path_coord"
    return ""


def _is_map_view(view: dict[str, Any]) -> bool:
    x_axis = _view_axis_name(view.get("x_axis"))
    y_axis = _view_axis_name(view.get("y_axis"))
    return {x_axis, y_axis} == {"west_east", "south_north"}


def _validate_view_def(view_id: str, view_def: Any, errors: list[str], *, prefix: str | None = None) -> None:
    item_prefix = prefix or f"view_defs.{view_id}"
    if not isinstance(view_def, dict):
        errors.append(f"{item_prefix} must be an object")
        return

    if view_id and not view_id.strip() and prefix is None:
        errors.append("view_defs keys must be non-empty strings")

    x_axis = view_def.get("x_axis")
    y_axis = view_def.get("y_axis")
    for axis_name, axis_value in (("x_axis", x_axis), ("y_axis", y_axis)):
        axis_prefix = f"{item_prefix}.{axis_name}"
        if not isinstance(axis_value, dict):
            errors.append(f"{axis_prefix} must be an object")
            continue
        axis_kind = _view_axis_kind(axis_value)
        if axis_kind not in {"native_dim", "derived_coord", "path_coord"}:
            errors.append(
                f"{axis_prefix}.kind must be one of derived_coord, native_dim, path_coord"
            )
        token = _view_axis_name(axis_value)
        if not token:
            errors.append(f"{axis_prefix}.name must be a non-empty string")
        elif token not in SUPPORTED_VIEW_AXES:
            errors.append(
                f"{axis_prefix}.name must be one of {', '.join(sorted(SUPPORTED_VIEW_AXES))}"
            )
        elif axis_kind == "native_dim" and token not in SUPPORTED_NATIVE_VIEW_AXES:
            errors.append(
                f"{axis_prefix}.name={token} is not valid for kind=native_dim"
            )
        elif axis_kind == "derived_coord" and token not in SUPPORTED_DERIVED_VIEW_AXES:
            errors.append(
                f"{axis_prefix}.name={token} is not valid for kind=derived_coord"
            )
        elif axis_kind == "path_coord" and token not in SUPPORTED_PATH_VIEW_AXES:
            errors.append(
                f"{axis_prefix}.name={token} is not valid for kind=path_coord"
            )
        for optional_key in ("label", "units"):
            optional_value = axis_value.get(optional_key)
            if optional_value is not None and (
                not isinstance(optional_value, str) or not optional_value.strip()
            ):
                errors.append(f"{axis_prefix}.{optional_key} must be a non-empty string or null")

    x_name = _view_axis_name(x_axis)
    y_name = _view_axis_name(y_axis)
    if x_name and y_name and x_name == y_name:
        errors.append(f"{item_prefix}.x_axis and {item_prefix}.y_axis must target different axes")

    selectors = view_def.get("selectors")
    if selectors is None:
        selectors = {}
    if not isinstance(selectors, dict):
        errors.append(f"{item_prefix}.selectors must be an object")
        selectors = {}

    for dim, selector in selectors.items():
        selector_prefix = f"{item_prefix}.selectors.{dim}"
        if dim not in SUPPORTED_NATIVE_VIEW_AXES:
            errors.append(
                f"{selector_prefix} uses unsupported dimension, expected one of {', '.join(sorted(SUPPORTED_NATIVE_VIEW_AXES))}"
            )
            continue
        if not isinstance(selector, dict):
            errors.append(f"{selector_prefix} must be an object")
            continue
        mode = selector.get("mode")
        allowed_modes = {"index", "first", "last", "current"}
        if mode not in allowed_modes:
            errors.append(
                f"{selector_prefix}.mode must be one of {', '.join(sorted(allowed_modes))}"
            )
            continue
        if dim != "time" and mode == "current":
            errors.append(f"{selector_prefix}.mode=current is only valid for time")
        if mode == "index":
            index = selector.get("index")
            if not isinstance(index, int) or index < 0:
                errors.append(f"{selector_prefix}.index must be a non-negative integer for mode=index")

    x_kind = _view_axis_kind(x_axis)
    y_kind = _view_axis_kind(y_axis)
    if "path_coord" in {x_kind, y_kind}:
        if x_kind != "path_coord":
            errors.append(f"{item_prefix} currently requires path_coord axes to be assigned to x_axis")
        sampling = view_def.get("sampling")
        sampling_prefix = f"{item_prefix}.sampling"
        if not isinstance(sampling, dict):
            errors.append(f"{sampling_prefix} must be an object when using path_coord axes")
            return
        path_cfg = sampling.get("path")
        path_prefix = f"{sampling_prefix}.path"
        if not isinstance(path_cfg, dict):
            errors.append(f"{path_prefix} must be an object when using path_coord axes")
            return
        kind = path_cfg.get("kind")
        if kind != "polyline":
            errors.append(f"{path_prefix}.kind must equal polyline")
        points = path_cfg.get("points")
        if not isinstance(points, list) or len(points) < 2:
            errors.append(f"{path_prefix}.points must be a list with at least two points")
        else:
            for index, point in enumerate(points):
                point_prefix = f"{path_prefix}.points[{index}]"
                if not isinstance(point, dict):
                    errors.append(f"{point_prefix} must be an object")
                    continue
                for key in ("lat", "lon"):
                    value = point.get(key)
                    if not isinstance(value, (int, float)):
                        errors.append(f"{point_prefix}.{key} must be numeric")
        samples = path_cfg.get("samples")
        if not isinstance(samples, int) or samples < 2:
            errors.append(f"{path_prefix}.samples must be an integer >= 2")
        if x_name != "distance_km":
            errors.append(f"{item_prefix}.x_axis.name must be distance_km for first-pass path sections")
        if y_name not in {"bottom_top", "height_m"}:
            errors.append(
                f"{item_prefix}.y_axis.name must be bottom_top or height_m for first-pass path sections"
            )


def _resolve_figure_view(
    figure: dict[str, Any],
    view_defs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    inline_view = figure.get("view")
    if isinstance(inline_view, dict):
        return inline_view, None
    view_id = figure.get("view_id")
    if isinstance(view_id, str) and view_id in view_defs:
        return view_defs[view_id], view_id
    return default_view_spec(), None


def _view_has_axis(view: dict[str, Any], axis_name: str) -> bool:
    return axis_name in {
        _view_axis_name(view.get("x_axis")),
        _view_axis_name(view.get("y_axis")),
    }


def _view_time_selector_mode(view: dict[str, Any]) -> str | None:
    selectors = view.get("selectors")
    if not isinstance(selectors, dict):
        return None
    selector = selectors.get("time")
    if not isinstance(selector, dict):
        return None
    mode = selector.get("mode")
    if not isinstance(mode, str):
        return None
    return mode.strip().lower() or None


def _validate_draw(draw: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(draw, dict):
        errors.append(f"{prefix} must be an object")
        return

    kind = draw.get("kind")
    allowed_kinds = {"raster", "contour", "categorical_fill", "vector"}
    if not isinstance(kind, str) or kind not in allowed_kinds:
        errors.append(f"{prefix}.kind must be one of {', '.join(sorted(allowed_kinds))}")

    alpha = draw.get("alpha")
    if not isinstance(alpha, (int, float)) or alpha < 0 or alpha > 1:
        errors.append(f"{prefix}.alpha must be a number between 0 and 1")

    zorder = draw.get("zorder")
    if zorder is not None and not isinstance(zorder, (int, float)):
        errors.append(f"{prefix}.zorder must be a number or null")

    style = draw.get("style")
    if not isinstance(style, dict):
        errors.append(f"{prefix}.style must be an object")
        return

    if kind == "contour":
        levels = style.get("levels")
        if not isinstance(levels, list) or not levels:
            errors.append(f"{prefix}.style.levels must be a non-empty list for contour")
        else:
            for index, value in enumerate(levels):
                if not isinstance(value, (int, float)):
                    errors.append(f"{prefix}.style.levels[{index}] must be a number")
    elif kind == "categorical_fill":
        categories = style.get("categories")
        if not isinstance(categories, list) or not categories:
            errors.append(f"{prefix}.style.categories must be a non-empty list for categorical_fill")
        else:
            for index, category in enumerate(categories):
                item_prefix = f"{prefix}.style.categories[{index}]"
                if not isinstance(category, dict):
                    errors.append(f"{item_prefix} must be an object")
                    continue
                if "value" not in category:
                    errors.append(f"{item_prefix}.value is required")
                color = category.get("color")
                if not isinstance(color, str) or not color.strip():
                    errors.append(f"{item_prefix}.color must be a non-empty string")
                label = category.get("label")
                if label is not None and (not isinstance(label, str) or not label.strip()):
                    errors.append(f"{item_prefix}.label must be a non-empty string or null")
    elif kind == "vector":
        mode = style.get("mode")
        if mode not in {"quiver"}:
            errors.append(f"{prefix}.style.mode must currently be quiver for vector")
        stride = style.get("stride")
        if stride is not None and (not isinstance(stride, int) or stride < 1):
            errors.append(f"{prefix}.style.stride must be a positive integer when provided")
        scale = style.get("scale")
        if scale is not None and not isinstance(scale, (int, float)):
            errors.append(f"{prefix}.style.scale must be a number when provided")
        color = style.get("color")
        if color is not None and (not isinstance(color, str) or not color.strip()):
            errors.append(f"{prefix}.style.color must be a non-empty string when provided")
        pivot = style.get("pivot")
        if pivot is not None and pivot not in {"tail", "mid", "middle", "tip"}:
            errors.append(f"{prefix}.style.pivot must be one of tail, mid, middle, tip when provided")


def _render_layer_target_ids(render_layer: dict[str, Any]) -> list[str]:
    draw = render_layer.get("draw")
    kind = str(draw.get("kind") or "") if isinstance(draw, dict) else ""
    if kind == "vector":
        target_ids: list[str] = []
        for key in ("u_layer_id", "v_layer_id"):
            value = render_layer.get(key)
            if isinstance(value, str) and value.strip():
                target_ids.append(value)
        return target_ids
    value = render_layer.get("layer_id")
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def validate_post_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    schema_version = spec.get("schema_version")
    if schema_version != DEFAULT_POST_SPEC_VERSION:
        errors.append(f"schema_version must equal {DEFAULT_POST_SPEC_VERSION}")

    project_name = spec.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        errors.append("project_name must be a non-empty string")

    _validate_defaults(spec.get("defaults"), errors)

    style_defs = spec.get("style_defs")
    if style_defs is None:
        style_defs = {}
    elif not isinstance(style_defs, dict):
        errors.append("style_defs must be an object when provided")
        style_defs = {}
    else:
        for style_id, style_def in style_defs.items():
            _validate_style_def(str(style_id), style_def, errors)

    view_defs = spec.get("view_defs")
    if view_defs is None:
        view_defs = {}
    elif not isinstance(view_defs, dict):
        errors.append("view_defs must be an object when provided")
        view_defs = {}
    else:
        for view_id, view_def in view_defs.items():
            _validate_view_def(str(view_id), view_def, errors)

    layer_defs = spec.get("layer_defs")
    if not isinstance(layer_defs, dict) or not layer_defs:
        errors.append("layer_defs must be a non-empty object")
        layer_defs = {}
    else:
        for layer_id, layer_def in layer_defs.items():
            _validate_layer_def(str(layer_id), layer_def, errors)

    figures = spec.get("figures")
    if not isinstance(figures, list) or not figures:
        errors.append("figures must be a non-empty list")
        return errors

    for figure_index, figure in enumerate(figures):
        prefix = f"figures[{figure_index}]"
        if not isinstance(figure, dict):
            errors.append(f"{prefix} must be an object")
            continue

        figure_id = figure.get("figure_id")
        if not isinstance(figure_id, str) or not figure_id.strip():
            errors.append(f"{prefix}.figure_id must be a non-empty string")

        view_id = figure.get("view_id")
        if view_id is not None:
            if not isinstance(view_id, str) or not view_id.strip():
                errors.append(f"{prefix}.view_id must be a non-empty string or null")
            elif view_id not in view_defs:
                errors.append(f"{prefix}.view_id references unknown view_defs key: {view_id}")

        inline_view = figure.get("view")
        if inline_view is not None and not isinstance(inline_view, dict):
            errors.append(f"{prefix}.view must be an object or null")
            inline_view = None
        if inline_view is not None and view_id is not None:
            errors.append(f"{prefix}.view and {prefix}.view_id are mutually exclusive")

        _validate_inputs(figure.get("inputs"), f"{prefix}.inputs", errors)
        _validate_selectors(figure.get("selectors"), f"{prefix}.selectors", errors)
        _validate_render(figure.get("render"), f"{prefix}.render", errors)
        _validate_output(figure.get("output"), f"{prefix}.output", errors)

        if isinstance(inline_view, dict):
            _validate_view_def("", inline_view, errors, prefix=f"{prefix}.view")
            resolved_view = inline_view
        else:
            resolved_view, _ = _resolve_figure_view(figure, view_defs)
        is_map_view = _is_map_view(resolved_view)

        layers = figure.get("layers")
        if not isinstance(layers, list) or not layers:
            errors.append(f"{prefix}.layers must be a non-empty list")
            continue

        for layer_index, layer in enumerate(layers):
            layer_prefix = f"{prefix}.layers[{layer_index}]"
            if not isinstance(layer, dict):
                errors.append(f"{layer_prefix} must be an object")
                continue
            style_id = layer.get("style_id")
            if style_id is not None:
                if not isinstance(style_id, str) or not style_id.strip():
                    errors.append(f"{layer_prefix}.style_id must be a non-empty string or null")
                elif style_id not in style_defs:
                    errors.append(f"{layer_prefix}.style_id references unknown style_defs key: {style_id}")
            draw = layer.get("draw")
            _validate_draw(draw, f"{layer_prefix}.draw", errors)
            draw_kind = str(draw.get("kind") or "") if isinstance(draw, dict) else ""
            if draw_kind == "vector":
                if not is_map_view:
                    errors.append(
                        f"{layer_prefix} draw.kind=vector is currently only supported for map views"
                    )
                for component_key in ("u_layer_id", "v_layer_id"):
                    component_id = layer.get(component_key)
                    if not isinstance(component_id, str) or not component_id.strip():
                        errors.append(f"{layer_prefix}.{component_key} must be a non-empty string for vector layers")
                    elif component_id not in layer_defs:
                        errors.append(
                            f"{layer_prefix}.{component_key} references unknown layer_defs key: {component_id}"
                        )
                if layer.get("layer_id") is not None:
                    errors.append(f"{layer_prefix}.layer_id is not used when draw.kind=vector")
            else:
                layer_id = layer.get("layer_id")
                if not isinstance(layer_id, str) or not layer_id.strip():
                    errors.append(f"{layer_prefix}.layer_id must be a non-empty string")
                elif layer_id not in layer_defs:
                    errors.append(f"{layer_prefix}.layer_id references unknown layer_defs key: {layer_id}")
                for component_key in ("u_layer_id", "v_layer_id"):
                    if layer.get(component_key) is not None:
                        errors.append(f"{layer_prefix}.{component_key} is only valid when draw.kind=vector")

    return errors


def _runtime_symbols() -> dict[str, Any]:
    try:
        from plot_wrfout import (
            BinaryOpNode,
            CallNode,
            NameNode,
            NumberNode,
            UnaryOpNode,
            layer_uses_current,
            parse_formula,
            resolve_layer_dependencies,
        )
    except ImportError:  # pragma: no cover
        from .plot_wrfout import (
            BinaryOpNode,
            CallNode,
            NameNode,
            NumberNode,
            UnaryOpNode,
            layer_uses_current,
            parse_formula,
            resolve_layer_dependencies,
        )

    return {
        "BinaryOpNode": BinaryOpNode,
        "CallNode": CallNode,
        "NameNode": NameNode,
        "NumberNode": NumberNode,
        "UnaryOpNode": UnaryOpNode,
        "layer_uses_current": layer_uses_current,
        "parse_formula": parse_formula,
        "resolve_layer_dependencies": resolve_layer_dependencies,
    }


def _collect_interpret_refs(node: Any, known_layers: set[str], runtime: dict[str, Any]) -> set[str]:
    if isinstance(node, runtime["NumberNode"]):
        return set()
    if isinstance(node, runtime["NameNode"]):
        return {node.name} if node.name in known_layers else set()
    if isinstance(node, runtime["UnaryOpNode"]):
        return _collect_interpret_refs(node.operand, known_layers, runtime)
    if isinstance(node, runtime["BinaryOpNode"]):
        return _collect_interpret_refs(node.left, known_layers, runtime) | _collect_interpret_refs(
            node.right,
            known_layers,
            runtime,
        )
    if isinstance(node, runtime["CallNode"]):
        refs: set[str] = set()
        for arg in node.args:
            refs |= _collect_interpret_refs(arg, known_layers, runtime)
        return refs
    raise TypeError(f"Unsupported AST node: {type(node)!r}")


def interpret_post_spec(
    spec: dict[str, Any],
    *,
    project_name_fallback: str | None = None,
    figure_id: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_post_spec(spec, project_name_fallback=project_name_fallback)
    errors = validate_post_spec(normalized)
    if errors:
        raise ValueError("Invalid post spec: " + "; ".join(errors))

    figures = list(normalized["figures"])
    if figure_id is not None:
        figures = [figure for figure in figures if figure.get("figure_id") == figure_id]
        if not figures:
            raise ValueError(f"Unknown figure_id: {figure_id}")

    runtime = _runtime_symbols()
    view_defs = normalized.get("view_defs") if isinstance(normalized.get("view_defs"), dict) else {}
    layer_defs = normalized["layer_defs"]
    known_layers = set(layer_defs)
    interpreted_figures: list[dict[str, Any]] = []
    for figure in figures:
        resolved_view, resolved_view_id = _resolve_figure_view(figure, view_defs)
        root_layer_ids: list[str] = []
        for render_layer in figure.get("layers", []):
            for layer_id in _render_layer_target_ids(render_layer):
                if layer_id not in root_layer_ids:
                    root_layer_ids.append(layer_id)
        parsed_defs, dependency_order = runtime["resolve_layer_dependencies"](layer_defs, root_layer_ids)
        usage_memo: dict[str, bool] = {}

        resolved_layers: list[dict[str, Any]] = []
        for render_layer in figure.get("layers", []):
            draw = deepcopy(render_layer.get("draw") or {})
            if str(draw.get("kind") or "") == "vector":
                u_layer_id = str(render_layer["u_layer_id"])
                v_layer_id = str(render_layer["v_layer_id"])
                u_parsed = parsed_defs[u_layer_id]
                v_parsed = parsed_defs[v_layer_id]
                resolved_layers.append(
                    {
                        "u_layer_id": u_layer_id,
                        "v_layer_id": v_layer_id,
                        "style_id": render_layer.get("style_id"),
                        "u_expr": str(layer_defs[u_layer_id].get("expr") or ""),
                        "v_expr": str(layer_defs[v_layer_id].get("expr") or ""),
                        "u_units": layer_defs[u_layer_id].get("units"),
                        "v_units": layer_defs[v_layer_id].get("units"),
                        "u_source_kind": str(layer_defs[u_layer_id].get("source", {}).get("kind") or "wrf_native"),
                        "v_source_kind": str(layer_defs[v_layer_id].get("source", {}).get("kind") or "wrf_native"),
                        "u_source": deepcopy(layer_defs[u_layer_id].get("source") or {}),
                        "v_source": deepcopy(layer_defs[v_layer_id].get("source") or {}),
                        "depends_on": sorted(
                            _collect_interpret_refs(u_parsed, known_layers, runtime)
                            | _collect_interpret_refs(v_parsed, known_layers, runtime)
                        ),
                        "uses_current": bool(
                            runtime["layer_uses_current"](u_layer_id, parsed_defs, memo=usage_memo)
                            or runtime["layer_uses_current"](v_layer_id, parsed_defs, memo=usage_memo)
                        ),
                        "draw": draw,
                    }
                )
            else:
                layer_id = str(render_layer["layer_id"])
                parsed = parsed_defs[layer_id]
                resolved_layers.append(
                    {
                        "layer_id": layer_id,
                        "style_id": render_layer.get("style_id"),
                        "expr": str(layer_defs[layer_id].get("expr") or ""),
                        "units": layer_defs[layer_id].get("units"),
                        "source_kind": str(layer_defs[layer_id].get("source", {}).get("kind") or "wrf_native"),
                        "source": deepcopy(layer_defs[layer_id].get("source") or {}),
                        "depends_on": sorted(_collect_interpret_refs(parsed, known_layers, runtime)),
                        "uses_current": bool(
                            runtime["layer_uses_current"](layer_id, parsed_defs, memo=usage_memo)
                        ),
                        "draw": draw,
                    }
                )

        figure_uses_current = any(item["uses_current"] for item in resolved_layers)
        if _view_has_axis(resolved_view, "time"):
            output_mode = "frame_range"
        elif _view_time_selector_mode(resolved_view) == "current":
            output_mode = "per_frame"
        else:
            output_mode = "per_frame" if figure_uses_current else "frame_range"
        interpreted_figures.append(
            {
                "figure_id": figure["figure_id"],
                "view_id": resolved_view_id,
                "view": deepcopy(resolved_view),
                "output_mode": output_mode,
                "render_layer_order": root_layer_ids,
                "dependency_order": dependency_order,
                "resolved_layers": resolved_layers,
                "inputs": deepcopy(figure.get("inputs") or {}),
                "selectors": deepcopy(figure.get("selectors") or {}),
                "render": deepcopy(figure.get("render") or {}),
                "output": deepcopy(figure.get("output") or {}),
            }
        )

    return {
        "schema_version": normalized["schema_version"],
        "project_name": normalized["project_name"],
        "style_defs": deepcopy(normalized.get("style_defs") or {}),
        "view_defs": deepcopy(normalized.get("view_defs") or {}),
        "layer_defs": {
            layer_id: {
                "expr": layer_def.get("expr"),
                "units": layer_def.get("units"),
                "source_kind": str(layer_def.get("source", {}).get("kind") or "wrf_native"),
                "source": deepcopy(layer_def.get("source") or {}),
            }
            for layer_id, layer_def in layer_defs.items()
        },
        "figures": interpreted_figures,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize and validate a WRF post-processing spec."
    )
    parser.add_argument(
        "--input",
        help="Path to an existing post-processing spec. Defaults to a generated starter spec.",
    )
    parser.add_argument(
        "--output",
        help="Write the normalized spec to this path. Defaults to stdout.",
    )
    parser.add_argument(
        "--project-name",
        default="demo",
        help="Project name fallback or starter-spec value.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate only and do not emit normalized JSON.",
    )
    parser.add_argument(
        "--interpret",
        action="store_true",
        help="Emit an interpreted execution plan instead of normalized JSON.",
    )
    parser.add_argument(
        "--figure-id",
        help="Optional figure filter used with --interpret.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input:
        payload = load_json(args.input)
    else:
        payload = default_post_spec(args.project_name)

    normalized = normalize_post_spec(payload, project_name_fallback=args.project_name)
    errors = validate_post_spec(normalized)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    if args.check and not args.interpret:
        return 0

    emitted: dict[str, Any] = normalized
    if args.interpret:
        emitted = interpret_post_spec(
            payload,
            project_name_fallback=args.project_name,
            figure_id=args.figure_id,
        )

    if args.output:
        dump_json(args.output, emitted)
    else:
        print(json.dumps(emitted, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
