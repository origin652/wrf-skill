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
ROOT_RESERVED_KEYS = {
    "schema_version",
    "project_name",
    "defaults",
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
            "source": {"kind": "wrf_native"},
            "expr": "HGT",
            "units": "m",
            "metadata": {
                "description": "Terrain height from WRF output",
            },
        },
        "landmask": {
            "source": {"kind": "wrf_native"},
            "expr": "LANDMASK",
            "units": None,
            "metadata": {
                "description": "Land-sea mask from WRF output",
            },
        },
        "t2_c": {
            "source": {"kind": "wrf_native"},
            "expr": "T2 - 273.15",
            "units": "C",
            "metadata": {
                "description": "2m temperature in Celsius",
            },
        },
        "wind10m": {
            "source": {"kind": "wrf_native"},
            "expr": "sqrt(U10**2 + V10**2)",
            "units": "m s-1",
            "metadata": {
                "description": "10m wind speed magnitude",
            },
        },
        "accum_precip": {
            "source": {"kind": "wrf_native"},
            "expr": "last(RAINC + RAINNC) - first(RAINC + RAINNC)",
            "units": "mm",
            "metadata": {
                "description": "Accumulated precipitation over the selected frame range",
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
                "draw": {
                    "kind": "raster",
                    "alpha": 1.0,
                    "zorder": 10,
                    "style": {
                        "colormap": "coolwarm",
                        "show_colorbar": True,
                    },
                },
            },
            {
                "layer_id": "terrain",
                "draw": {
                    "kind": "contour",
                    "alpha": 0.9,
                    "zorder": 20,
                    "style": {
                        "levels": [0, 500, 1000, 1500, 2000],
                        "colors": "black",
                        "linewidths": 0.5,
                    },
                },
            },
        ],
    }


def _empty_post_spec(project_name: str = "demo") -> dict[str, Any]:
    return {
        "schema_version": DEFAULT_POST_SPEC_VERSION,
        "project_name": project_name,
        "defaults": default_post_defaults(),
        "layer_defs": {},
        "figures": [],
    }


def default_post_spec(project_name: str = "demo", *, product_name: str | None = None) -> dict[str, Any]:
    del product_name
    return {
        "schema_version": DEFAULT_POST_SPEC_VERSION,
        "project_name": project_name,
        "defaults": default_post_defaults(),
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


def _normalize_draw(raw_draw: Any) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "kind": None,
        "alpha": 1.0,
        "zorder": None,
        "style": {},
    }
    if not isinstance(raw_draw, dict):
        return normalized

    for key, value in raw_draw.items():
        if key not in {"kind", "alpha", "zorder", "style"}:
            normalized[key] = deepcopy(value)
    normalized["kind"] = raw_draw.get("kind")
    normalized["alpha"] = raw_draw.get("alpha", 1.0)
    normalized["zorder"] = raw_draw.get("zorder")
    style = raw_draw.get("style")
    normalized["style"] = deepcopy(style) if isinstance(style, dict) else {}
    return normalized


def _normalize_render_layer(raw_layer: Any) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "layer_id": None,
        "draw": _normalize_draw({}),
    }
    if not isinstance(raw_layer, dict):
        normalized["layer_id"] = str(raw_layer)
        return normalized

    for key, value in raw_layer.items():
        if key not in {"layer_id", "draw"}:
            normalized[key] = deepcopy(value)
    normalized["layer_id"] = raw_layer.get("layer_id")
    normalized["draw"] = _normalize_draw(raw_layer.get("draw"))
    return normalized


def _normalize_figure(raw_figure: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    base = {
        "figure_id": None,
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
        if key not in {"figure_id", *DEFAULT_SECTION_KEYS, "layers"}
    }
    normalized["figure_id"] = raw_figure.get("figure_id")

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
        normalized["layers"] = [_normalize_render_layer(item) for item in raw_layers]
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

    raw_layer_defs = incoming.get("layer_defs")
    if isinstance(raw_layer_defs, dict):
        normalized["layer_defs"] = {
            str(layer_id): _normalize_layer_def(layer_def)
            for layer_id, layer_def in raw_layer_defs.items()
        }

    raw_figures = incoming.get("figures")
    defaults = normalized["defaults"] if isinstance(normalized["defaults"], dict) else {}
    if isinstance(raw_figures, list):
        normalized["figures"] = [
            _normalize_figure(item, defaults)
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

    expr = layer_def.get("expr")
    if not isinstance(expr, str) or not expr.strip():
        errors.append(f"{prefix}.expr must be a non-empty string")

    units = layer_def.get("units")
    if units is not None and (not isinstance(units, str) or not units.strip()):
        errors.append(f"{prefix}.units must be a non-empty string or null")

    metadata = layer_def.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(f"{prefix}.metadata must be an object")


def _validate_draw(draw: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(draw, dict):
        errors.append(f"{prefix} must be an object")
        return

    kind = draw.get("kind")
    allowed_kinds = {"raster", "contour", "categorical_fill"}
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


def validate_post_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    schema_version = spec.get("schema_version")
    if schema_version != DEFAULT_POST_SPEC_VERSION:
        errors.append(f"schema_version must equal {DEFAULT_POST_SPEC_VERSION}")

    project_name = spec.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        errors.append("project_name must be a non-empty string")

    _validate_defaults(spec.get("defaults"), errors)

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

        _validate_inputs(figure.get("inputs"), f"{prefix}.inputs", errors)
        _validate_selectors(figure.get("selectors"), f"{prefix}.selectors", errors)
        _validate_render(figure.get("render"), f"{prefix}.render", errors)
        _validate_output(figure.get("output"), f"{prefix}.output", errors)

        layers = figure.get("layers")
        if not isinstance(layers, list) or not layers:
            errors.append(f"{prefix}.layers must be a non-empty list")
            continue

        for layer_index, layer in enumerate(layers):
            layer_prefix = f"{prefix}.layers[{layer_index}]"
            if not isinstance(layer, dict):
                errors.append(f"{layer_prefix} must be an object")
                continue
            layer_id = layer.get("layer_id")
            if not isinstance(layer_id, str) or not layer_id.strip():
                errors.append(f"{layer_prefix}.layer_id must be a non-empty string")
            elif layer_id not in layer_defs:
                errors.append(f"{layer_prefix}.layer_id references unknown layer_defs key: {layer_id}")
            _validate_draw(layer.get("draw"), f"{layer_prefix}.draw", errors)

    return errors


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

    if args.check:
        return 0

    if args.output:
        dump_json(args.output, normalized)
    else:
        print(json.dumps(normalized, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
