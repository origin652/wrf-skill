from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyparsing as pp

try:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import colors as mcolors
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover
    matplotlib = None
    mcolors = None
    plt = None

try:
    from netCDF4 import Dataset
except ImportError:  # pragma: no cover
    Dataset = None

try:
    from post_spec import default_post_spec, load_json, normalize_post_spec, validate_post_spec
except ImportError:  # pragma: no cover
    from .post_spec import default_post_spec, load_json, normalize_post_spec, validate_post_spec

TIME_FORMAT = "%Y-%m-%d_%H:%M:%S"
DOMAIN_PATTERN = re.compile(r"(d\d{2})")
DRAW_KINDS = {"raster", "contour", "categorical_fill", "vector"}
SOURCE_KIND_ALIASES = {
    "wrf_native": "wrf_native_2d",
}
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
    "pressure_hpa",
}
SUPPORTED_PATH_VIEW_AXES = {
    "distance_km",
}
SUPPORTED_VIEW_AXES = (
    SUPPORTED_NATIVE_VIEW_AXES
    | SUPPORTED_DERIVED_VIEW_AXES
    | SUPPORTED_PATH_VIEW_AXES
)
AXIS_UNIT_DEFAULTS = {
    "distance_km": "km",
    "height_m": "m",
    "pressure_hpa": "hPa",
}
STAGGERED_MASS_DIMS = {
    "bottom_top_stag",
    "south_north_stag",
    "west_east_stag",
}
MAP_VECTOR_PROJECTION_KIND = "map_xy"
PATH_SECTION_VECTOR_PROJECTION_KIND = "path_section"
MAP_VECTOR_COMPONENTS = {"u", "v"}
PATH_SECTION_VECTOR_COMPONENTS = {"path_tangent", "path_normal", "vertical"}
FUNCTION_NAMES = {
    "sqrt",
    "abs",
    "minimum",
    "maximum",
    "clip",
    "where",
    "current",
    "first",
    "last",
}


class PlottingDependencyError(RuntimeError):
    pass


class FormulaParseError(ValueError):
    pass


class LayerResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class FieldCube:
    values: np.ndarray
    dims: tuple[str, ...]
    coords: dict[str, np.ndarray] | None
    units: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ResolvedViewField:
    values: np.ndarray
    dims: tuple[str, str]
    x_axis: dict[str, Any]
    y_axis: dict[str, Any]
    x_coords: np.ndarray | None
    y_coords: np.ndarray | None
    units: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PathSamplingContext:
    frame: dict[str, Any]
    time_index: int
    distances: np.ndarray
    sample_lats: np.ndarray
    sample_lons: np.ndarray
    corner_rows: np.ndarray
    corner_cols: np.ndarray
    sample_weights: np.ndarray
    tangent_east: np.ndarray
    tangent_north: np.ndarray
    normal_east: np.ndarray
    normal_north: np.ndarray


@dataclass(frozen=True)
class PathViewPreparation:
    reduced_cube: FieldCube
    selected_indices: dict[str, int]
    sampling: PathSamplingContext
    x_axis: dict[str, Any]
    y_axis: dict[str, Any]
    x_axis_name: str
    y_axis_name: str
    x_axis_kind: str
    y_axis_kind: str
    other_axis_name: str
    other_axis_kind: str


@dataclass(frozen=True)
class NumberNode:
    value: float


@dataclass(frozen=True)
class NameNode:
    name: str


@dataclass(frozen=True)
class UnaryOpNode:
    op: str
    operand: Any


@dataclass(frozen=True)
class BinaryOpNode:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class CallNode:
    name: str
    args: tuple[Any, ...]


pp.ParserElement.enable_packrat()


def posix_path(path: Path | str) -> str:
    return Path(path).as_posix()


def default_view_spec() -> dict[str, Any]:
    return {
        "x_axis": _default_view_axis("west_east"),
        "y_axis": _default_view_axis("south_north"),
        "selectors": {},
    }


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


def _default_view_axis(axis_name: Any) -> dict[str, Any]:
    name = str(axis_name).strip() if axis_name is not None else ""
    return {
        "kind": _view_axis_kind({"name": name}),
        "name": name,
        "label": name or None,
        "units": AXIS_UNIT_DEFAULTS.get(name),
    }


def _normalize_view_axis(raw_axis: Any, *, fallback_name: str) -> dict[str, Any]:
    normalized = _default_view_axis(fallback_name)
    if isinstance(raw_axis, dict):
        if raw_axis.get("name") is not None:
            normalized = _default_view_axis(raw_axis.get("name"))
        for key, value in raw_axis.items():
            if key not in {"kind", "name", "label", "units"}:
                normalized[key] = deepcopy(value)
        if raw_axis.get("kind") is not None:
            normalized["kind"] = raw_axis.get("kind")
        if raw_axis.get("name") is not None:
            normalized["name"] = raw_axis.get("name")
        if raw_axis.get("label") is not None:
            normalized["label"] = raw_axis.get("label")
        if raw_axis.get("units") is not None:
            normalized["units"] = raw_axis.get("units")
        if not normalized.get("kind"):
            normalized["kind"] = _view_axis_kind(normalized)
        return normalized
    if raw_axis is not None:
        normalized = _default_view_axis(raw_axis)
    return normalized


def _normalize_view_spec(raw_view: Any) -> dict[str, Any]:
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


def _resolve_figure_view(
    figure_spec: dict[str, Any],
    view_defs: dict[str, dict[str, Any]] | None,
) -> tuple[dict[str, Any], str | None]:
    inline_view = figure_spec.get("view")
    if isinstance(inline_view, dict):
        return _normalize_view_spec(inline_view), None

    view_id = figure_spec.get("view_id")
    if isinstance(view_id, str) and isinstance(view_defs, dict):
        view_def = view_defs.get(view_id)
        if isinstance(view_def, dict):
            return _normalize_view_spec(view_def), view_id

    return default_view_spec(), None


def _view_has_axis(view_spec: dict[str, Any], axis_name: str) -> bool:
    return axis_name in {
        _view_axis_name(view_spec.get("x_axis")),
        _view_axis_name(view_spec.get("y_axis")),
    }


def _is_map_view(view_spec: dict[str, Any]) -> bool:
    x_axis = _view_axis_name(view_spec.get("x_axis"))
    y_axis = _view_axis_name(view_spec.get("y_axis"))
    return {x_axis, y_axis} == {"west_east", "south_north"}


def _is_path_view(view_spec: dict[str, Any]) -> bool:
    return "path_coord" in {
        _view_axis_kind(view_spec.get("x_axis")),
        _view_axis_kind(view_spec.get("y_axis")),
    }


def _view_time_selector_mode(view_spec: dict[str, Any]) -> str | None:
    selectors = view_spec.get("selectors")
    if not isinstance(selectors, dict):
        return None
    selector = selectors.get("time")
    if not isinstance(selector, dict):
        return None
    mode = selector.get("mode")
    if not isinstance(mode, str):
        return None
    token = mode.strip().lower()
    return token or None


def _axis_label(view_axis: Any) -> str:
    name = _view_axis_name(view_axis) or "axis"
    if isinstance(view_axis, dict):
        label = view_axis.get("label")
        if isinstance(label, str) and label.strip():
            base_label = label.strip()
        else:
            base_label = name
        units = view_axis.get("units")
        if isinstance(units, str) and units.strip():
            return f"{base_label} ({units.strip()})"
        return base_label
    return name


def ensure_plotting_dependencies() -> None:
    missing: list[str] = []
    if Dataset is None:
        missing.append("netCDF4")
    if plt is None or mcolors is None:
        missing.append("matplotlib")
    if not hasattr(pp, "infix_notation"):
        missing.append("pyparsing")
    if missing:
        raise PlottingDependencyError(
            f"Missing plotting dependencies: {', '.join(missing)}"
        )


def infer_domain_from_path(path: Path | str) -> str | None:
    match = DOMAIN_PATTERN.search(Path(path).name)
    return match.group(1) if match else None


def _infer_time_count(dataset: Dataset) -> int:
    time_dimension = dataset.dimensions.get("Time")
    if time_dimension is not None and len(time_dimension) > 0:
        return len(time_dimension)
    times = dataset.variables.get("Times")
    if times is not None and getattr(times, "shape", None):
        return int(times.shape[0])
    return 1


def _decode_time_row(row: Any) -> str | None:
    values = np.asarray(row).astype("S1").ravel().tolist()
    raw = b"".join(values).decode("ascii", errors="ignore")
    token = raw.replace("\x00", "").strip()
    return token or None


def read_time_labels(dataset: Dataset) -> list[str | None]:
    times = dataset.variables.get("Times")
    if times is None:
        return [None] * _infer_time_count(dataset)

    raw_values = np.asarray(times[:])
    if raw_values.ndim == 1:
        raw_values = raw_values.reshape(1, raw_values.shape[0])
    return [_decode_time_row(row) for row in raw_values]


def enumerate_wrfout_frames(paths: list[Path | str]) -> list[dict[str, Any]]:
    ensure_plotting_dependencies()

    ordered_paths = sorted(
        {Path(path).resolve() for path in paths},
        key=lambda item: item.as_posix(),
    )
    frames: list[dict[str, Any]] = []
    global_index = 0
    for path in ordered_paths:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Missing wrfout file: {path}")
        with Dataset(path) as dataset:
            labels = read_time_labels(dataset)
            count = max(_infer_time_count(dataset), len(labels))
            if len(labels) < count:
                labels.extend([None] * (count - len(labels)))

        domain = infer_domain_from_path(path)
        for time_index in range(count):
            frames.append(
                {
                    "path": path,
                    "path_posix": posix_path(path),
                    "domain": domain,
                    "time_index": time_index,
                    "global_index": global_index,
                    "valid_time": labels[time_index],
                }
            )
            global_index += 1
    return frames


def has_explicit_time_selection(selectors: dict[str, Any] | None) -> bool:
    if not isinstance(selectors, dict):
        return False
    if selectors.get("time_indices") is not None:
        return True
    time_range = selectors.get("time_range")
    if not isinstance(time_range, dict):
        return False
    return any(time_range.get(key) is not None for key in ("start", "end"))


def select_wrfout_frames(
    frames: list[dict[str, Any]],
    selectors: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(selectors, dict):
        return list(frames)

    selected = list(frames)

    domain = selectors.get("domain")
    if domain:
        selected = [frame for frame in selected if frame.get("domain") == domain]

    time_range = selectors.get("time_range")
    if isinstance(time_range, dict) and any(
        time_range.get(key) is not None for key in ("start", "end")
    ):
        start = time_range.get("start")
        end = time_range.get("end")
        start_dt = datetime.strptime(start, TIME_FORMAT) if start else None
        end_dt = datetime.strptime(end, TIME_FORMAT) if end else None
        filtered: list[dict[str, Any]] = []
        for frame in selected:
            valid_time = frame.get("valid_time")
            if not valid_time:
                raise ValueError(
                    "time_range selection requires WRF outputs with Times metadata"
                )
            valid_dt = datetime.strptime(valid_time, TIME_FORMAT)
            if start_dt is not None and valid_dt < start_dt:
                continue
            if end_dt is not None and valid_dt > end_dt:
                continue
            filtered.append(frame)
        selected = filtered

    time_indices = selectors.get("time_indices")
    if time_indices is not None:
        explicit_indices = {int(value) for value in time_indices}
        selected = [
            frame for frame in selected if int(frame["global_index"]) in explicit_indices
        ]

    return selected


def _sanitize_token(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    safe = safe.strip("-._")
    return safe or "item"


def _time_token(value: str | None) -> str | None:
    if not value:
        return None
    return _sanitize_token(value.replace(":", "-"))


def _output_root(base_output_dir: Path, output_cfg: dict[str, Any]) -> Path:
    subdir = str(output_cfg.get("subdir") or "").strip()
    return base_output_dir / subdir if subdir else base_output_dir


def _build_output_path(
    base_output_dir: Path,
    figure_spec: dict[str, Any],
    suffix_tokens: list[str],
    *,
    allow_exact_path: bool = False,
) -> Path:
    output_cfg = figure_spec.get("output", {})
    render_cfg = figure_spec.get("render", {})
    exact_path = output_cfg.get("path")
    if allow_exact_path and exact_path:
        return Path(str(exact_path))

    output_dir = _output_root(base_output_dir, output_cfg)
    stem = str(output_cfg.get("file_stem") or figure_spec["figure_id"])
    pieces = [_sanitize_token(stem)]
    pieces.extend(_sanitize_token(token) for token in suffix_tokens if token)
    suffix = str(render_cfg.get("format") or "png").lower()
    return output_dir / ("__".join(pieces) + f".{suffix}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _summary(field: np.ndarray) -> dict[str, float | None]:
    finite = np.asarray(field, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return {"min": None, "max": None, "mean": None}
    return {
        "min": float(finite.min()),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
    }


def _load_2d_var(dataset: Dataset, name: str, time_index: int) -> tuple[np.ndarray, str | None]:
    variable = dataset.variables.get(name)
    if variable is None:
        raise KeyError(f"Missing WRF variable: {name}")

    raw = variable[time_index] if variable.ndim >= 3 else variable[:]
    while getattr(raw, "ndim", 0) > 2:
        raw = raw[0]

    field = np.asarray(np.ma.filled(raw, np.nan), dtype=float)
    if field.ndim != 2:
        raise ValueError(f"Expected 2D field for {name}, received ndim={field.ndim}")
    return field, getattr(variable, "units", None)


def _resolve_level_index(level_selector: dict[str, Any] | None, level_count: int) -> int:
    selector = level_selector if isinstance(level_selector, dict) else {}
    mode = str(selector.get("mode") or "index").lower()
    if mode == "first":
        return 0
    if mode == "last":
        return level_count - 1
    if mode != "index":
        raise ValueError(f"Unsupported level_selector.mode: {mode}")
    raw_index = selector.get("index", 0)
    index = int(raw_index)
    if index < 0 or index >= level_count:
        raise ValueError(
            f"level_selector.index={index} is out of range for available levels={level_count}"
        )
    return index


def _load_3d_var(
    dataset: Dataset,
    name: str,
    time_index: int,
    *,
    level_selector: dict[str, Any] | None,
) -> tuple[np.ndarray, str | None]:
    variable = dataset.variables.get(name)
    if variable is None:
        raise KeyError(f"Missing WRF variable: {name}")

    raw = variable[time_index] if variable.ndim >= 4 else variable[:]
    field = np.asarray(np.ma.filled(raw, np.nan), dtype=float)
    if field.ndim != 3:
        raise ValueError(
            f"Expected 3D field for {name} before level selection, received ndim={field.ndim}"
        )
    field = _destagger_mass_grid_field(
        field,
        dims=tuple(variable.dimensions[-field.ndim:]),
        name=name,
    )

    level_index = _resolve_level_index(level_selector, int(field.shape[0]))
    level_field = np.asarray(field[level_index], dtype=float)
    if level_field.ndim != 2:
        raise ValueError(
            f"Expected 2D slice for {name} after level selection, received ndim={level_field.ndim}"
        )
    return level_field, getattr(variable, "units", None)


def _load_3d_var_full(
    dataset: Dataset,
    name: str,
    time_index: int,
) -> tuple[np.ndarray, str | None]:
    variable = dataset.variables.get(name)
    if variable is None:
        raise KeyError(f"Missing WRF variable: {name}")

    raw = variable[time_index] if variable.ndim >= 4 else variable[:]
    field = np.asarray(np.ma.filled(raw, np.nan), dtype=float)
    if field.ndim != 3:
        raise ValueError(
            f"Expected 3D field for {name}, received ndim={field.ndim}"
        )
    field = _destagger_mass_grid_field(
        field,
        dims=tuple(variable.dimensions[-field.ndim:]),
        name=name,
    )
    return field, getattr(variable, "units", None)


def _destagger_mass_grid_field(
    field: np.ndarray,
    *,
    dims: tuple[str, ...],
    name: str,
) -> np.ndarray:
    resolved = np.asarray(field, dtype=float)
    if resolved.ndim != len(dims):
        return resolved

    for axis, dim_name in enumerate(dims):
        if dim_name not in STAGGERED_MASS_DIMS:
            continue
        if resolved.shape[axis] < 2:
            raise ValueError(
                f"Cannot destagger {name} along {dim_name}: size={resolved.shape[axis]}"
            )
        lower = [slice(None)] * resolved.ndim
        upper = [slice(None)] * resolved.ndim
        lower[axis] = slice(0, -1)
        upper[axis] = slice(1, None)
        resolved = 0.5 * (
            resolved[tuple(lower)]
            + resolved[tuple(upper)]
        )
    return resolved


def _normalize_source_kind(kind: str | None) -> str:
    token = str(kind or "wrf_native").strip()
    return SOURCE_KIND_ALIASES.get(token, token)


def _serialize_frame(frame: dict[str, Any] | None) -> dict[str, Any] | None:
    if frame is None:
        return None
    return {
        "path": posix_path(frame["path"]),
        "domain": frame.get("domain"),
        "time_index": int(frame["time_index"]),
        "global_index": int(frame["global_index"]),
        "valid_time": frame.get("valid_time"),
    }


def _serialize_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_serialize_frame(frame) for frame in frames]  # type: ignore[list-item]


def _group_frames_by_domain(
    frames: list[dict[str, Any]],
) -> list[tuple[str | None, list[dict[str, Any]]]]:
    grouped: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        grouped[frame.get("domain")].append(frame)

    items = list(grouped.items())
    items.sort(key=lambda item: item[0] or "")
    for _, group in items:
        group.sort(key=lambda frame: int(frame["global_index"]))
    return items


def _default_zorder(kind: str) -> int:
    return {
        "categorical_fill": 3,
        "raster": 10,
        "contour": 20,
        "vector": 30,
    }.get(kind, 10)


def _figure_suffix_tokens(
    selected_frames: list[dict[str, Any]],
    current_frame: dict[str, Any] | None,
    *,
    uses_current: bool,
) -> list[str]:
    domain = selected_frames[-1].get("domain") or ""
    if uses_current and current_frame is not None:
        return [domain, _time_token(current_frame.get("valid_time")) or ""]

    first_frame = selected_frames[0]
    last_frame = selected_frames[-1]
    first_time = _time_token(first_frame.get("valid_time"))
    last_time = _time_token(last_frame.get("valid_time"))
    if first_time and last_time and first_time != last_time:
        return [domain, _sanitize_token(f"{first_time}-to-{last_time}")]
    return [domain, last_time or first_time or ""]


def _compose_title(
    figure_spec: dict[str, Any],
    selected_frames: list[dict[str, Any]],
    current_frame: dict[str, Any] | None,
    *,
    uses_current: bool,
) -> str:
    render_cfg = figure_spec.get("render", {})
    base = render_cfg.get("title") or figure_spec["figure_id"]
    details: list[str] = []
    if selected_frames[-1].get("domain"):
        details.append(str(selected_frames[-1]["domain"]))

    if uses_current and current_frame is not None and current_frame.get("valid_time"):
        details.append(str(current_frame["valid_time"]))
    else:
        first_time = selected_frames[0].get("valid_time")
        last_time = selected_frames[-1].get("valid_time")
        if first_time and last_time:
            if first_time == last_time:
                details.append(str(last_time))
            else:
                details.append(f"{first_time} -> {last_time}")

    suffix = " | ".join(details)
    return f"{base} | {suffix}" if suffix else str(base)


def _artifact_payload(
    figure_spec: dict[str, Any],
    *,
    output_path: Path,
    sidecar_path: Path | None,
    selected_frames: list[dict[str, Any]],
    current_frame: dict[str, Any] | None,
    title: str,
    view: dict[str, Any],
    view_id: str | None,
    resolved_layers: list[dict[str, Any]],
    layer_summaries: dict[str, dict[str, float | None]],
) -> dict[str, Any]:
    source_files: list[str] = []
    for frame in selected_frames:
        source = posix_path(frame["path"])
        if source not in source_files:
            source_files.append(source)

    return {
        "figure_id": figure_spec["figure_id"],
        "path": posix_path(output_path),
        "sidecar_path": None if sidecar_path is None else posix_path(sidecar_path),
        "format": str(figure_spec.get("render", {}).get("format") or "png").lower(),
        "title": title,
        "domain": selected_frames[-1].get("domain"),
        "current_frame": _serialize_frame(current_frame),
        "selected_frames": _serialize_frames(selected_frames),
        "source_files": source_files,
        "view_id": view_id,
        "view": view,
        "resolved_layers": resolved_layers,
        "layer_summaries": layer_summaries,
    }


def _ensure_2d_field(value: Any, *, label: str) -> np.ndarray:
    field = np.asarray(np.ma.filled(value, np.nan), dtype=float)
    if field.ndim != 2:
        raise ValueError(f"{label} must resolve to a 2D field, received ndim={field.ndim}")
    return field


def _is_default_axis_coords(coords: np.ndarray | None, size: int) -> bool:
    if coords is None:
        return True
    array = np.asarray(coords, dtype=float)
    if array.ndim != 1 or array.shape[0] != size:
        return False
    return np.allclose(array, np.arange(size, dtype=float))


def _coordinate_mesh(
    field: np.ndarray,
    *,
    x_coords: np.ndarray | None,
    y_coords: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray] | None:
    if _is_default_axis_coords(x_coords, int(field.shape[1])) and _is_default_axis_coords(
        y_coords,
        int(field.shape[0]),
    ):
        return None

    x_array = None if x_coords is None else np.asarray(x_coords, dtype=float)
    y_array = None if y_coords is None else np.asarray(y_coords, dtype=float)

    if x_array is None:
        x_array = np.arange(field.shape[1], dtype=float)
    if y_array is None:
        y_array = np.arange(field.shape[0], dtype=float)

    if x_array.ndim == 1 and y_array.ndim == 1:
        return np.meshgrid(x_array, y_array)
    if x_array.ndim == 1 and y_array.ndim == 2 and y_array.shape == field.shape:
        return np.broadcast_to(x_array.reshape(1, -1), field.shape), y_array
    if x_array.ndim == 2 and x_array.shape == field.shape and y_array.ndim == 1:
        return x_array, np.broadcast_to(y_array.reshape(-1, 1), field.shape)
    if (
        x_array.ndim == 2
        and y_array.ndim == 2
        and x_array.shape == field.shape
        and y_array.shape == field.shape
    ):
        return x_array, y_array
    raise ValueError(
        f"Unsupported coordinate shapes for field shape {field.shape}: "
        f"x={None if x_coords is None else np.asarray(x_coords).shape}, "
        f"y={None if y_coords is None else np.asarray(y_coords).shape}"
    )


def _center_mesh_to_edge_mesh(mesh: np.ndarray) -> np.ndarray:
    centers = np.asarray(mesh, dtype=float)
    if centers.ndim != 2:
        raise ValueError(f"Expected a 2D coordinate mesh, received ndim={centers.ndim}")

    rows, cols = centers.shape
    padded = np.empty((rows + 2, cols + 2), dtype=float)
    padded[1:-1, 1:-1] = centers

    if cols > 1:
        padded[1:-1, 0] = 2.0 * centers[:, 0] - centers[:, 1]
        padded[1:-1, -1] = 2.0 * centers[:, -1] - centers[:, -2]
    else:
        padded[1:-1, 0] = centers[:, 0] - 0.5
        padded[1:-1, -1] = centers[:, 0] + 0.5

    if rows > 1:
        padded[0, 1:-1] = 2.0 * centers[0, :] - centers[1, :]
        padded[-1, 1:-1] = 2.0 * centers[-1, :] - centers[-2, :]
    else:
        padded[0, 1:-1] = centers[0, :] - 0.5
        padded[-1, 1:-1] = centers[0, :] + 0.5

    padded[0, 0] = padded[0, 1] + padded[1, 0] - padded[1, 1]
    padded[0, -1] = padded[0, -2] + padded[1, -1] - padded[1, -2]
    padded[-1, 0] = padded[-2, 0] + padded[-1, 1] - padded[-2, 1]
    padded[-1, -1] = padded[-2, -1] + padded[-1, -2] - padded[-2, -2]

    return 0.25 * (
        padded[:-1, :-1]
        + padded[1:, :-1]
        + padded[:-1, 1:]
        + padded[1:, 1:]
    )


def _pcolormesh_coordinate_mesh(
    field: np.ndarray,
    *,
    x_coords: np.ndarray | None,
    y_coords: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray] | None:
    mesh = _coordinate_mesh(field, x_coords=x_coords, y_coords=y_coords)
    if mesh is None:
        return None

    x_mesh, y_mesh = mesh
    # Curvilinear section coordinates need explicit cell edges to avoid
    # Matplotlib inferring broken quads from center coordinates.
    return _center_mesh_to_edge_mesh(x_mesh), _center_mesh_to_edge_mesh(y_mesh)


def _dims_for_field(field: np.ndarray, *, label: str) -> tuple[str, ...]:
    if field.ndim == 2:
        return ("south_north", "west_east")
    if field.ndim == 3:
        return ("bottom_top", "south_north", "west_east")
    raise ValueError(
        f"{label} resolved to unsupported ndim={field.ndim}; expected 2D or 3D fields"
    )


def _default_coords_for_dims(dims: tuple[str, ...], shape: tuple[int, ...]) -> dict[str, np.ndarray]:
    coords: dict[str, np.ndarray] = {}
    for dim, size in zip(dims, shape):
        coords[dim] = np.arange(int(size), dtype=float)
    return coords


def _build_field_cube(
    values: Any,
    *,
    dims: tuple[str, ...],
    units: str | None,
    metadata: dict[str, Any] | None = None,
    coords: dict[str, Any] | None = None,
    label: str,
) -> FieldCube:
    array = np.asarray(np.ma.filled(values, np.nan), dtype=float)
    if array.ndim != len(dims):
        raise ValueError(
            f"{label} expected ndim={len(dims)} for dims={dims}, received ndim={array.ndim}"
        )

    normalized_coords = _default_coords_for_dims(dims, array.shape)
    if isinstance(coords, dict):
        for dim in dims:
            if dim in coords and coords[dim] is not None:
                normalized_coords[dim] = np.asarray(coords[dim], dtype=float)

    return FieldCube(
        values=array,
        dims=dims,
        coords=normalized_coords,
        units=units,
        metadata=deepcopy(metadata or {}),
    )


def _resolve_axis_coords(value: Any, *, size: int) -> np.ndarray:
    if value is None:
        return np.arange(size, dtype=float)
    array = np.asarray(value, dtype=float)
    if array.ndim == 1 and array.shape[0] == size:
        return array
    return np.arange(size, dtype=float)


def _selector_numeric_value(dim: str, selector: Any, field_name: str) -> float:
    if not isinstance(selector, dict):
        raise ValueError(f"selectors.{dim}.mode requires an object selector")
    raw_value = selector.get(field_name)
    if not isinstance(raw_value, (int, float)):
        raise ValueError(f"selectors.{dim}.{field_name} must be numeric")
    return float(raw_value)


def _selector_reduce_values(mode: str, values: np.ndarray, *, axis: int) -> np.ndarray:
    if mode == "mean":
        return np.nanmean(values, axis=axis)
    if mode == "min":
        return np.nanmin(values, axis=axis)
    if mode == "max":
        return np.nanmax(values, axis=axis)
    if mode == "sum":
        return np.nansum(values, axis=axis)
    raise ValueError(f"Unsupported reduction selector mode: {mode}")


def _selector_index(
    dim: str,
    selector: Any,
    coords: np.ndarray,
    size: int,
    *,
    current_time_index: int | None,
) -> int:
    mode = _selector_mode(selector)
    if mode is None:
        mode = "current" if dim == "time" and current_time_index is not None else ("last" if dim == "time" else "first")
        selector = {"mode": mode}

    if mode == "first":
        return 0
    if mode == "last":
        return size - 1
    if mode == "current":
        if dim != "time":
            raise ValueError(f"selectors.{dim}.mode=current is only valid for the time axis")
        if current_time_index is None:
            raise ValueError("selectors.time.mode=current requires a current frame")
        if current_time_index < 0 or current_time_index >= size:
            raise ValueError(
                f"current time index {current_time_index} is out of range for available times={size}"
            )
        return current_time_index
    if mode == "index":
        if not isinstance(selector, dict):
            raise ValueError(f"selectors.{dim}.mode=index requires an object selector")
        index = selector.get("index")
        if not isinstance(index, int):
            raise ValueError(f"selectors.{dim}.index must be an integer for mode=index")
        if index < 0 or index >= size:
            raise ValueError(
                f"selectors.{dim}.index={index} is out of range for available size={size}"
            )
        return index
    if mode == "nearest_index":
        index_value = _selector_numeric_value(dim, selector, "index")
        return int(np.clip(np.rint(index_value), 0, size - 1))
    if mode == "value":
        target = _selector_numeric_value(dim, selector, "value")
        matches = np.flatnonzero(np.isclose(coords, target))
        if matches.size == 0:
            raise ValueError(
                f"selectors.{dim}.value={target} did not match any coordinate in {coords.tolist()}"
            )
        return int(matches[0])
    if mode == "nearest_value":
        target = _selector_numeric_value(dim, selector, "value")
        if coords.ndim != 1 or coords.shape[0] != size:
            raise ValueError(f"selectors.{dim}.mode=nearest_value requires 1D coordinates")
        finite_mask = np.isfinite(coords)
        if not np.any(finite_mask):
            raise ValueError(f"selectors.{dim}.mode=nearest_value found no finite coordinates")
        candidates = np.where(finite_mask, np.abs(coords - target), np.inf)
        return int(np.nanargmin(candidates))
    raise ValueError(
        f"Unsupported selectors.{dim}.mode: {mode}. Expected one of current, first, index, "
        "last, nearest_index, value, nearest_value"
    )


def _reduce_cube_for_view(
    cube: FieldCube,
    *,
    keep_dims: set[str],
    selectors: dict[str, Any],
    current_time_index: int | None,
) -> tuple[FieldCube, dict[str, int]]:
    values = np.asarray(cube.values, dtype=float)
    active_dims = list(cube.dims)
    active_coords = dict(cube.coords or {})
    selected_indices: dict[str, int] = {}

    for dim in list(active_dims):
        if dim in keep_dims:
            continue
        axis = active_dims.index(dim)
        selector = selectors.get(dim)
        mode = _selector_mode(selector)
        if mode is None:
            mode = "current" if dim == "time" and current_time_index is not None else ("last" if dim == "time" else "first")
            selector = {"mode": mode}
        coord_array = _resolve_axis_coords(active_coords.get(dim), size=int(values.shape[axis]))
        if mode in {"mean", "min", "max", "sum"}:
            values = _selector_reduce_values(mode, values, axis=axis)
            active_dims.pop(axis)
            active_coords.pop(dim, None)
            continue
        index = _selector_index(
            dim,
            selector,
            coord_array,
            int(values.shape[axis]),
            current_time_index=current_time_index,
        )
        values = np.take(values, indices=index, axis=axis)
        active_dims.pop(axis)
        active_coords.pop(dim, None)
        selected_indices[dim] = index

    coords: dict[str, np.ndarray] = {}
    for axis, dim in enumerate(active_dims):
        coord_value = active_coords.get(dim)
        if coord_value is None:
            continue
        coord_array = np.asarray(coord_value, dtype=float)
        if coord_array.ndim == 1 and coord_array.shape[0] == int(values.shape[axis]):
            coords[dim] = coord_array

    metadata = deepcopy(cube.metadata)
    if isinstance(metadata, dict):
        metadata["selected_indices"] = {
            **deepcopy(metadata.get("selected_indices") or {}),
            **selected_indices,
        }

    reduced_cube = _build_field_cube(
        values,
        dims=tuple(active_dims),
        units=cube.units,
        metadata=metadata,
        coords=coords,
        label="view_reduction",
    )
    return reduced_cube, selected_indices


def _great_circle_km(
    lat1: Any,
    lon1: Any,
    lat2: Any,
    lon2: Any,
) -> np.ndarray:
    lat1_rad = np.radians(np.asarray(lat1, dtype=float))
    lon1_rad = np.radians(np.asarray(lon1, dtype=float))
    lat2_rad = np.radians(np.asarray(lat2, dtype=float))
    lon2_rad = np.radians(np.asarray(lon2, dtype=float))

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2.0) ** 2
    return 6371.0 * (2.0 * np.arctan2(np.sqrt(a), np.sqrt(np.maximum(1.0 - a, 0.0))))


def _build_polyline_samples(path_config: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points = path_config.get("points") or []
    samples = int(path_config.get("samples") or 0)
    if samples < 2 or len(points) < 2:
        raise ValueError("sampling.path requires at least two points and samples >= 2")

    latitudes = np.asarray([float(point["lat"]) for point in points], dtype=float)
    longitudes = np.asarray([float(point["lon"]) for point in points], dtype=float)
    segment_lengths = _great_circle_km(
        latitudes[:-1],
        longitudes[:-1],
        latitudes[1:],
        longitudes[1:],
    )
    cumulative = np.concatenate([[0.0], np.cumsum(segment_lengths)])
    total = float(cumulative[-1])
    if total <= 0.0:
        raise ValueError("sampling.path points must span a non-zero distance")

    distances = np.linspace(0.0, total, samples)
    sample_lats = np.empty(samples, dtype=float)
    sample_lons = np.empty(samples, dtype=float)
    segment_index = 0
    for index, distance in enumerate(distances):
        while segment_index < len(segment_lengths) - 1 and distance > cumulative[segment_index + 1]:
            segment_index += 1
        segment_start = cumulative[segment_index]
        segment_length = float(segment_lengths[segment_index])
        fraction = 0.0 if segment_length <= 0.0 else (distance - segment_start) / segment_length
        sample_lats[index] = latitudes[segment_index] + fraction * (
            latitudes[segment_index + 1] - latitudes[segment_index]
        )
        sample_lons[index] = longitudes[segment_index] + fraction * (
            longitudes[segment_index + 1] - longitudes[segment_index]
        )

    return distances, sample_lats, sample_lons


def _path_unit_vectors(
    sample_lats: np.ndarray,
    sample_lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    sample_count = int(sample_lats.shape[0])
    if sample_count < 2:
        raise ValueError("Path vectors require at least two sampled points")

    tangent_east = np.empty(sample_count, dtype=float)
    tangent_north = np.empty(sample_count, dtype=float)
    for index in range(sample_count):
        if index == 0:
            start_index, end_index = 0, 1
        elif index == sample_count - 1:
            start_index, end_index = sample_count - 2, sample_count - 1
        else:
            start_index, end_index = index - 1, index + 1

        lat0 = float(sample_lats[start_index])
        lat1 = float(sample_lats[end_index])
        lon0 = float(sample_lons[start_index])
        lon1 = float(sample_lons[end_index])
        mean_lat_rad = np.radians((lat0 + lat1) / 2.0)
        east_km = (lon1 - lon0) * 111.32 * np.cos(mean_lat_rad)
        north_km = (lat1 - lat0) * 111.32
        magnitude = float(np.hypot(east_km, north_km))
        if magnitude <= 0.0:
            if index > 0:
                tangent_east[index] = tangent_east[index - 1]
                tangent_north[index] = tangent_north[index - 1]
            else:
                tangent_east[index] = 1.0
                tangent_north[index] = 0.0
            continue
        tangent_east[index] = east_km / magnitude
        tangent_north[index] = north_km / magnitude

    normal_east = -tangent_north
    normal_north = tangent_east
    return tangent_east, tangent_north, normal_east, normal_north


def _nearest_horizontal_indices(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    sample_lats: np.ndarray,
    sample_lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    if lat_grid.shape != lon_grid.shape:
        raise ValueError("XLAT and XLONG grids must share the same shape")

    rows = np.empty(sample_lats.shape[0], dtype=int)
    cols = np.empty(sample_lats.shape[0], dtype=int)
    flat_lat = np.asarray(lat_grid, dtype=float).reshape(-1)
    flat_lon = np.asarray(lon_grid, dtype=float).reshape(-1)

    for index, (lat_value, lon_value) in enumerate(zip(sample_lats, sample_lons)):
        distances = _great_circle_km(flat_lat, flat_lon, lat_value, lon_value)
        flat_index = int(np.nanargmin(distances))
        row, col = np.unravel_index(flat_index, lat_grid.shape)
        rows[index] = int(row)
        cols[index] = int(col)

    return rows, cols


def _candidate_bilinear_cells(
    row_index: int,
    col_index: int,
    shape: tuple[int, int],
) -> list[tuple[int, int]]:
    row_count, col_count = shape
    candidates: list[tuple[int, int]] = []
    if row_count < 2 or col_count < 2:
        return candidates
    for top_row in (row_index - 1, row_index):
        if top_row < 0 or top_row >= row_count - 1:
            continue
        for left_col in (col_index - 1, col_index):
            if left_col < 0 or left_col >= col_count - 1:
                continue
            candidate = (top_row, left_col)
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _bilinear_weights(row_fraction: float, col_fraction: float) -> np.ndarray:
    return np.asarray(
        [
            (1.0 - row_fraction) * (1.0 - col_fraction),
            row_fraction * (1.0 - col_fraction),
            (1.0 - row_fraction) * col_fraction,
            row_fraction * col_fraction,
        ],
        dtype=float,
    )


def _bilinear_vector(
    p00: np.ndarray,
    p10: np.ndarray,
    p01: np.ndarray,
    p11: np.ndarray,
    row_fraction: float,
    col_fraction: float,
) -> np.ndarray:
    weights = _bilinear_weights(row_fraction, col_fraction)
    return (
        weights[0] * p00
        + weights[1] * p10
        + weights[2] * p01
        + weights[3] * p11
    )


def _solve_bilinear_cell_fractions(
    lat_cell: np.ndarray,
    lon_cell: np.ndarray,
    sample_lat: float,
    sample_lon: float,
) -> tuple[float, float, float] | None:
    p00 = np.asarray([lat_cell[0, 0], lon_cell[0, 0]], dtype=float)
    p10 = np.asarray([lat_cell[1, 0], lon_cell[1, 0]], dtype=float)
    p01 = np.asarray([lat_cell[0, 1], lon_cell[0, 1]], dtype=float)
    p11 = np.asarray([lat_cell[1, 1], lon_cell[1, 1]], dtype=float)
    target = np.asarray([sample_lat, sample_lon], dtype=float)
    if not np.all(np.isfinite([*p00, *p10, *p01, *p11, *target])):
        return None

    row_fraction = 0.5
    col_fraction = 0.5
    center = _bilinear_vector(p00, p10, p01, p11, row_fraction, col_fraction)
    center_jacobian = np.column_stack(
        [
            0.5 * ((p10 - p00) + (p11 - p01)),
            0.5 * ((p01 - p00) + (p11 - p10)),
        ]
    )
    try:
        delta = np.linalg.lstsq(center_jacobian, target - center, rcond=None)[0]
        row_fraction += float(delta[0])
        col_fraction += float(delta[1])
    except np.linalg.LinAlgError:
        pass

    residual = float("inf")
    for _ in range(12):
        current = _bilinear_vector(p00, p10, p01, p11, row_fraction, col_fraction)
        residual_vector = current - target
        residual = float(np.linalg.norm(residual_vector))
        if residual <= 1e-10:
            break
        d_row = (1.0 - col_fraction) * (p10 - p00) + col_fraction * (p11 - p01)
        d_col = (1.0 - row_fraction) * (p01 - p00) + row_fraction * (p11 - p10)
        jacobian = np.column_stack([d_row, d_col])
        try:
            step = np.linalg.lstsq(jacobian, residual_vector, rcond=None)[0]
        except np.linalg.LinAlgError:
            return None
        row_fraction -= float(step[0])
        col_fraction -= float(step[1])

    return row_fraction, col_fraction, residual


def _bilinear_horizontal_weights(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    sample_lats: np.ndarray,
    sample_lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if lat_grid.shape != lon_grid.shape:
        raise ValueError("XLAT and XLONG grids must share the same shape")

    nearest_rows, nearest_cols = _nearest_horizontal_indices(
        lat_grid,
        lon_grid,
        sample_lats,
        sample_lons,
    )
    sample_count = int(sample_lats.shape[0])
    corner_rows = np.empty((sample_count, 4), dtype=int)
    corner_cols = np.empty((sample_count, 4), dtype=int)
    weights = np.zeros((sample_count, 4), dtype=float)

    for index, (row_index, col_index) in enumerate(zip(nearest_rows, nearest_cols)):
        row_value = int(row_index)
        col_value = int(col_index)
        corner_rows[index, :] = row_value
        corner_cols[index, :] = col_value
        weights[index, 0] = 1.0

        best_solution: tuple[float, int, int, float, float] | None = None
        for top_row, left_col in _candidate_bilinear_cells(row_value, col_value, lat_grid.shape):
            lat_cell = np.asarray(lat_grid[top_row : top_row + 2, left_col : left_col + 2], dtype=float)
            lon_cell = np.asarray(lon_grid[top_row : top_row + 2, left_col : left_col + 2], dtype=float)
            solution = _solve_bilinear_cell_fractions(
                lat_cell,
                lon_cell,
                float(sample_lats[index]),
                float(sample_lons[index]),
            )
            if solution is None:
                continue
            row_fraction, col_fraction, residual = solution
            if not (-1e-6 <= row_fraction <= 1.0 + 1e-6 and -1e-6 <= col_fraction <= 1.0 + 1e-6):
                continue
            if best_solution is None or residual < best_solution[0]:
                best_solution = (residual, top_row, left_col, row_fraction, col_fraction)

        if best_solution is None:
            continue

        _, top_row, left_col, row_fraction, col_fraction = best_solution
        row_fraction = min(max(row_fraction, 0.0), 1.0)
        col_fraction = min(max(col_fraction, 0.0), 1.0)
        corner_rows[index, :] = np.asarray([top_row, top_row + 1, top_row, top_row + 1], dtype=int)
        corner_cols[index, :] = np.asarray([left_col, left_col, left_col + 1, left_col + 1], dtype=int)
        weights[index, :] = _bilinear_weights(row_fraction, col_fraction)

    return corner_rows, corner_cols, weights


def _sample_along_path(field: np.ndarray, corner_rows: np.ndarray, corner_cols: np.ndarray, weights: np.ndarray) -> np.ndarray:
    values = np.asarray(field, dtype=float)
    if values.ndim == 2:
        corners = np.stack(
            [values[corner_rows[:, index], corner_cols[:, index]] for index in range(4)],
            axis=0,
        )
        return np.sum(corners * weights.T, axis=0)
    if values.ndim == 3:
        corners = np.stack(
            [values[:, corner_rows[:, index], corner_cols[:, index]] for index in range(4)],
            axis=0,
        )
        return np.sum(corners * weights.T[:, np.newaxis, :], axis=0)
    raise ValueError(f"path sampling requires 2D or 3D fields, received ndim={values.ndim}")


def _build_path_sampling_context(
    path_config: dict[str, Any],
    *,
    frame: dict[str, Any],
    evaluator: "FigureEvaluator",
) -> PathSamplingContext:
    distances, sample_lats, sample_lons = _build_polyline_samples(path_config)
    lat_grid, lon_grid = evaluator.load_horizontal_coords(frame)
    corner_rows, corner_cols, sample_weights = _bilinear_horizontal_weights(
        lat_grid,
        lon_grid,
        sample_lats,
        sample_lons,
    )
    tangent_east, tangent_north, normal_east, normal_north = _path_unit_vectors(
        sample_lats,
        sample_lons,
    )
    return PathSamplingContext(
        frame=frame,
        time_index=int(frame["time_index"]),
        distances=np.asarray(distances, dtype=float),
        sample_lats=np.asarray(sample_lats, dtype=float),
        sample_lons=np.asarray(sample_lons, dtype=float),
        corner_rows=np.asarray(corner_rows, dtype=int),
        corner_cols=np.asarray(corner_cols, dtype=int),
        sample_weights=np.asarray(sample_weights, dtype=float),
        tangent_east=np.asarray(tangent_east, dtype=float),
        tangent_north=np.asarray(tangent_north, dtype=float),
        normal_east=np.asarray(normal_east, dtype=float),
        normal_north=np.asarray(normal_north, dtype=float),
    )


def _sample_with_path_context(
    values: np.ndarray,
    sampling: PathSamplingContext,
) -> np.ndarray:
    return _sample_along_path(
        values,
        sampling.corner_rows,
        sampling.corner_cols,
        sampling.sample_weights,
    )


def _selector_mode(selector: Any) -> str | None:
    if not isinstance(selector, dict):
        return None
    mode = selector.get("mode")
    if not isinstance(mode, str):
        return None
    token = mode.strip().lower()
    return token or None


def _resolve_native_view_field(
    cube: FieldCube,
    view_spec: dict[str, Any],
    *,
    current_time_index: int | None,
) -> ResolvedViewField:
    x_axis = _view_axis_name(view_spec.get("x_axis"))
    y_axis = _view_axis_name(view_spec.get("y_axis"))
    selectors = view_spec.get("selectors") if isinstance(view_spec.get("selectors"), dict) else {}

    if x_axis not in SUPPORTED_NATIVE_VIEW_AXES or y_axis not in SUPPORTED_NATIVE_VIEW_AXES:
        raise ValueError(
            f"Unsupported native view axes x={x_axis!r}, y={y_axis!r}; supported axes: {', '.join(sorted(SUPPORTED_NATIVE_VIEW_AXES))}"
        )
    if x_axis == y_axis:
        raise ValueError("x_axis and y_axis must be different")

    reduced_cube, _ = _reduce_cube_for_view(
        cube,
        keep_dims={x_axis, y_axis},
        selectors=selectors,
        current_time_index=current_time_index,
    )

    active_dims = list(reduced_cube.dims)
    if x_axis not in active_dims or y_axis not in active_dims:
        raise ValueError(
            f"View axes x={x_axis}, y={y_axis} are not available for current layer dimensions {cube.dims}"
        )
    if len(active_dims) != 2:
        raise ValueError(
            f"View extraction expected exactly 2 remaining axes, received {len(active_dims)} ({active_dims})"
        )

    y_index = active_dims.index(y_axis)
    x_index = active_dims.index(x_axis)
    values = np.moveaxis(reduced_cube.values, [y_index, x_index], [0, 1])
    resolved_values = _ensure_2d_field(values, label="view_extraction")
    reduced_coords = reduced_cube.coords or {}
    y_coords = _resolve_axis_coords(reduced_coords.get(y_axis), size=int(resolved_values.shape[0]))
    x_coords = _resolve_axis_coords(reduced_coords.get(x_axis), size=int(resolved_values.shape[1]))
    metadata = deepcopy(reduced_cube.metadata)
    metadata["source_dims"] = list(cube.dims)

    return ResolvedViewField(
        values=resolved_values,
        dims=(y_axis, x_axis),
        x_axis=_normalize_view_axis(view_spec.get("x_axis"), fallback_name=x_axis or "west_east"),
        y_axis=_normalize_view_axis(view_spec.get("y_axis"), fallback_name=y_axis or "south_north"),
        x_coords=x_coords,
        y_coords=y_coords,
        units=cube.units,
        metadata=metadata,
    )


def _resolve_vertical_coord_field(
    evaluator: "FigureEvaluator",
    frame: dict[str, Any],
    coord_name: str,
) -> np.ndarray:
    if coord_name == "height_m":
        return evaluator.load_mass_height(frame)
    if coord_name == "pressure_hpa":
        return evaluator.load_mass_pressure(frame)
    raise ValueError(f"Unsupported derived vertical coordinate: {coord_name}")


def _resolve_time_vertical_view_field(
    cube: FieldCube,
    view_spec: dict[str, Any],
    *,
    current_time_index: int | None,
    evaluator: "FigureEvaluator" | None,
    frames: list[dict[str, Any]] | None,
) -> ResolvedViewField:
    if evaluator is None or frames is None:
        raise ValueError("Derived vertical views require evaluator and frame context")

    x_axis_name = _view_axis_name(view_spec.get("x_axis"))
    y_axis_name = _view_axis_name(view_spec.get("y_axis"))
    x_axis_kind = _view_axis_kind(view_spec.get("x_axis"))
    y_axis_kind = _view_axis_kind(view_spec.get("y_axis"))
    selectors = view_spec.get("selectors") if isinstance(view_spec.get("selectors"), dict) else {}

    if {x_axis_kind, y_axis_kind} != {"native_dim", "derived_coord"}:
        raise ValueError(
            "Derived vertical views currently require exactly one time axis and one "
            "derived vertical axis"
        )
    vertical_axis_name = y_axis_name if y_axis_kind == "derived_coord" else x_axis_name
    if vertical_axis_name not in SUPPORTED_DERIVED_VIEW_AXES:
        raise ValueError(
            "Derived vertical views currently require one derived_coord axis with "
            "name=height_m or pressure_hpa"
        )
    if {x_axis_name, y_axis_name} != {"time", vertical_axis_name}:
        raise ValueError(
            "Derived vertical views currently require one native_dim axis with name=time"
        )

    reduced_cube, selected_indices = _reduce_cube_for_view(
        cube,
        keep_dims={"time", "bottom_top"},
        selectors=selectors,
        current_time_index=current_time_index,
    )
    if tuple(reduced_cube.dims) != ("time", "bottom_top"):
        raise ValueError(
            "Derived vertical views currently require a 3D field with remaining dims "
            "('time', 'bottom_top') after selector reduction"
        )

    row_index = selected_indices.get("south_north")
    col_index = selected_indices.get("west_east")
    if row_index is None or col_index is None:
        raise ValueError(
            "Derived vertical views currently require south_north and west_east "
            "to be resolved by selectors or default reduction"
        )

    values = _ensure_2d_field(reduced_cube.values.T, label="derived_vertical_view")
    vertical_profiles: list[np.ndarray] = []
    for time_index, frame in enumerate(frames):
        coord_field = _resolve_vertical_coord_field(evaluator, frame, vertical_axis_name)
        if coord_field.ndim != 3:
            raise ValueError(
                f"Expected 3D vertical coordinate field for {vertical_axis_name}, received ndim={coord_field.ndim}"
            )
        if coord_field.shape[0] != values.shape[0]:
            raise ValueError(
                f"Vertical coordinate field shape {coord_field.shape} does not match "
                f"reduced cube values {reduced_cube.values.shape}"
            )
        if time_index >= values.shape[1]:
            raise ValueError(
                f"Frame count {len(frames)} does not match reduced time axis size {values.shape[1]}"
            )
        vertical_profiles.append(
            np.asarray(coord_field[:, int(row_index), int(col_index)], dtype=float)
        )

    vertical_coord_mesh = np.stack(vertical_profiles, axis=1)
    reduced_coords = reduced_cube.coords or {}
    time_coords = _resolve_axis_coords(reduced_coords.get("time"), size=int(reduced_cube.values.shape[0]))
    metadata = deepcopy(reduced_cube.metadata)
    metadata["source_dims"] = list(cube.dims)
    metadata["selected_indices"] = {
        "south_north": int(row_index),
        "west_east": int(col_index),
    }
    metadata["vertical_coord"] = vertical_axis_name

    if x_axis_name == "time":
        resolved_values = values
        resolved_x_coords = time_coords
        resolved_y_coords = vertical_coord_mesh
    else:
        resolved_values = reduced_cube.values
        resolved_x_coords = vertical_coord_mesh.T
        resolved_y_coords = time_coords

    return ResolvedViewField(
        values=resolved_values,
        dims=(y_axis_name, x_axis_name),
        x_axis=_normalize_view_axis(view_spec.get("x_axis"), fallback_name=x_axis_name),
        y_axis=_normalize_view_axis(view_spec.get("y_axis"), fallback_name=y_axis_name),
        x_coords=np.asarray(resolved_x_coords, dtype=float),
        y_coords=np.asarray(resolved_y_coords, dtype=float),
        units=cube.units,
        metadata=metadata,
    )


def _prepare_path_view_field(
    cube: FieldCube,
    view_spec: dict[str, Any],
    *,
    current_time_index: int | None,
    evaluator: "FigureEvaluator",
    frames: list[dict[str, Any]],
) -> PathViewPreparation:
    x_axis_name = _view_axis_name(view_spec.get("x_axis"))
    y_axis_name = _view_axis_name(view_spec.get("y_axis"))
    x_axis_kind = _view_axis_kind(view_spec.get("x_axis"))
    y_axis_kind = _view_axis_kind(view_spec.get("y_axis"))
    selectors = view_spec.get("selectors") if isinstance(view_spec.get("selectors"), dict) else {}

    if {x_axis_kind, y_axis_kind} != {"path_coord", "native_dim"} and not (
        "path_coord" in {x_axis_kind, y_axis_kind} and "derived_coord" in {x_axis_kind, y_axis_kind}
    ):
        raise ValueError(
            "First-pass path sections currently require exactly one path_coord axis and one "
            "vertical axis (bottom_top, height_m, or pressure_hpa)"
        )
    if x_axis_kind == "path_coord" and x_axis_name != "distance_km":
        raise ValueError("Path-coordinate axes currently require name=distance_km")
    if y_axis_kind == "path_coord" and y_axis_name != "distance_km":
        raise ValueError("Path-coordinate axes currently require name=distance_km")

    other_axis_name = y_axis_name if x_axis_kind == "path_coord" else x_axis_name
    other_axis_kind = y_axis_kind if x_axis_kind == "path_coord" else x_axis_kind
    if other_axis_name not in {"bottom_top", *SUPPORTED_DERIVED_VIEW_AXES}:
        raise ValueError(
            "First-pass path sections currently support the non-path axis as "
            "bottom_top, height_m, or pressure_hpa"
        )
    if other_axis_kind not in {"native_dim", "derived_coord"}:
        raise ValueError(
            "First-pass path sections currently require the non-path axis to be "
            "native_dim or derived_coord"
        )
    if _view_axis_name(view_spec.get("x_axis")) == "time" or _view_axis_name(view_spec.get("y_axis")) == "time":
        raise ValueError("Path-coordinate sections do not currently support time as a plotted axis")

    reduced_cube, selected_indices = _reduce_cube_for_view(
        cube,
        keep_dims={"bottom_top", "south_north", "west_east"},
        selectors=selectors,
        current_time_index=current_time_index,
    )
    if tuple(reduced_cube.dims) != ("bottom_top", "south_north", "west_east"):
        raise ValueError(
            "distance_km sections currently require a 3D field with remaining dims "
            "('bottom_top', 'south_north', 'west_east') after selector reduction"
        )

    time_index = selected_indices.get("time")
    if time_index is None:
        raise ValueError("distance_km sections require time to be resolved by selectors or current frame")
    if time_index < 0 or time_index >= len(frames):
        raise ValueError(f"Resolved time index {time_index} is out of range for available frames={len(frames)}")
    frame = frames[time_index]

    sampling = view_spec.get("sampling")
    if not isinstance(sampling, dict):
        raise ValueError("distance_km sections require view.sampling.path")
    path_config = sampling.get("path")
    if not isinstance(path_config, dict):
        raise ValueError("distance_km sections require view.sampling.path")

    sampling_context = _build_path_sampling_context(
        path_config,
        frame=frame,
        evaluator=evaluator,
    )
    return PathViewPreparation(
        reduced_cube=reduced_cube,
        selected_indices=selected_indices,
        sampling=sampling_context,
        x_axis=_normalize_view_axis(view_spec.get("x_axis"), fallback_name=x_axis_name),
        y_axis=_normalize_view_axis(view_spec.get("y_axis"), fallback_name=y_axis_name),
        x_axis_name=x_axis_name,
        y_axis_name=y_axis_name,
        x_axis_kind=x_axis_kind,
        y_axis_kind=y_axis_kind,
        other_axis_name=other_axis_name,
        other_axis_kind=other_axis_kind,
    )


def _resolve_path_vertical_coords(
    prepared: PathViewPreparation,
    *,
    evaluator: "FigureEvaluator",
) -> np.ndarray | None:
    if prepared.other_axis_kind != "derived_coord":
        return None
    vertical_coord = _resolve_vertical_coord_field(
        evaluator,
        prepared.sampling.frame,
        prepared.other_axis_name,
    )
    if vertical_coord.shape != tuple(prepared.reduced_cube.values.shape):
        raise ValueError(
            f"Vertical coordinate field shape {vertical_coord.shape} does not match "
            f"sampled cube shape {prepared.reduced_cube.values.shape}"
        )
    return _sample_with_path_context(vertical_coord, prepared.sampling)


def _build_path_resolved_view_field(
    sampled_values: np.ndarray,
    prepared: PathViewPreparation,
    *,
    vertical_coords: np.ndarray | None,
    units: str | None,
    metadata: dict[str, Any],
) -> ResolvedViewField:
    if sampled_values.ndim != 2:
        raise ValueError("distance_km sections expected a 2D sampled field after path extraction")

    if prepared.x_axis_kind == "path_coord":
        resolved_values = sampled_values
        resolved_x_coords = np.asarray(prepared.sampling.distances, dtype=float)
        if prepared.other_axis_kind == "derived_coord":
            resolved_y_coords = np.asarray(vertical_coords, dtype=float)
        else:
            resolved_y_coords = np.arange(sampled_values.shape[0], dtype=float)
    else:
        resolved_values = sampled_values.T
        resolved_y_coords = np.asarray(prepared.sampling.distances, dtype=float)
        if prepared.other_axis_kind == "derived_coord":
            if vertical_coords is None:
                raise ValueError("Derived path views require vertical coordinates")
            resolved_x_coords = np.asarray(vertical_coords.T, dtype=float)
        else:
            resolved_x_coords = np.arange(sampled_values.shape[0], dtype=float)

    return ResolvedViewField(
        values=np.asarray(resolved_values, dtype=float),
        dims=(prepared.y_axis_name, prepared.x_axis_name),
        x_axis=deepcopy(prepared.x_axis),
        y_axis=deepcopy(prepared.y_axis),
        x_coords=np.asarray(resolved_x_coords, dtype=float),
        y_coords=np.asarray(resolved_y_coords, dtype=float),
        units=units,
        metadata=metadata,
    )


def _resolve_vector_axis_projection(
    draw: dict[str, Any],
    *,
    view_spec: dict[str, Any],
) -> dict[str, str]:
    style = draw.get("style")
    if not isinstance(style, dict):
        raise ValueError("Vector draw.style must be an object")
    raw_projection = style.get("axis_projection")
    if raw_projection is None:
        if _is_map_view(view_spec):
            return {
                "kind": MAP_VECTOR_PROJECTION_KIND,
                "x_component": "u",
                "y_component": "v",
            }
        raise ValueError("Vector layers in non-map views require draw.style.axis_projection")
    if not isinstance(raw_projection, dict):
        raise ValueError("draw.style.axis_projection must be an object when provided")

    projection_kind = str(raw_projection.get("kind") or "").strip()
    x_component = str(raw_projection.get("x_component") or "").strip()
    y_component = str(raw_projection.get("y_component") or "").strip()
    if projection_kind == MAP_VECTOR_PROJECTION_KIND:
        if not _is_map_view(view_spec):
            raise ValueError("draw.style.axis_projection.kind=map_xy is only valid for map views")
        allowed_components = MAP_VECTOR_COMPONENTS
    elif projection_kind == PATH_SECTION_VECTOR_PROJECTION_KIND:
        if not _is_path_view(view_spec):
            raise ValueError("draw.style.axis_projection.kind=path_section is only valid for path views")
        allowed_components = PATH_SECTION_VECTOR_COMPONENTS
    else:
        raise ValueError(
            f"Unsupported draw.style.axis_projection.kind: {projection_kind}. "
            f"Expected {MAP_VECTOR_PROJECTION_KIND} or {PATH_SECTION_VECTOR_PROJECTION_KIND}"
        )

    for component_key, component_value in (("x_component", x_component), ("y_component", y_component)):
        if component_value not in allowed_components:
            raise ValueError(
                f"draw.style.axis_projection.{component_key} must be one of "
                f"{', '.join(sorted(allowed_components))}"
            )
    if x_component == y_component:
        raise ValueError("draw.style.axis_projection.x_component and y_component must differ")
    return {
        "kind": projection_kind,
        "x_component": x_component,
        "y_component": y_component,
    }


def _coerce_same_shape_view(
    field: ResolvedViewField,
    *,
    expected: ResolvedViewField,
    label: str,
) -> ResolvedViewField:
    if field.values.shape != expected.values.shape:
        raise ValueError(
            f"{label} resolved to shape {field.values.shape}, expected {expected.values.shape} "
            "to match the section vector geometry"
        )
    return field


def _resolve_path_projected_horizontal_component(
    u_cube: FieldCube,
    v_cube: FieldCube,
    view_spec: dict[str, Any],
    *,
    component: str,
    current_time_index: int | None,
    evaluator: "FigureEvaluator",
    frames: list[dict[str, Any]],
) -> ResolvedViewField:
    prepared = _prepare_path_view_field(
        u_cube,
        view_spec,
        current_time_index=current_time_index,
        evaluator=evaluator,
        frames=frames,
    )
    selectors = view_spec.get("selectors") if isinstance(view_spec.get("selectors"), dict) else {}
    reduced_v_cube, selected_v_indices = _reduce_cube_for_view(
        v_cube,
        keep_dims={"bottom_top", "south_north", "west_east"},
        selectors=selectors,
        current_time_index=current_time_index,
    )
    if tuple(reduced_v_cube.dims) != tuple(prepared.reduced_cube.dims):
        raise ValueError(
            f"Path vector component v reduced dims {reduced_v_cube.dims} do not match "
            f"u reduced dims {prepared.reduced_cube.dims}"
        )
    if reduced_v_cube.values.shape != prepared.reduced_cube.values.shape:
        raise ValueError(
            f"Path vector component v shape {reduced_v_cube.values.shape} does not match "
            f"u shape {prepared.reduced_cube.values.shape}"
        )
    if selected_v_indices.get("time") != prepared.selected_indices.get("time"):
        raise ValueError("Path vector components must resolve to the same time index")

    sampled_u = _sample_with_path_context(prepared.reduced_cube.values, prepared.sampling)
    sampled_v = _sample_with_path_context(reduced_v_cube.values, prepared.sampling)
    if component == "path_tangent":
        projected_values = (
            sampled_u * prepared.sampling.tangent_east.reshape(1, -1)
            + sampled_v * prepared.sampling.tangent_north.reshape(1, -1)
        )
    elif component == "path_normal":
        projected_values = (
            sampled_u * prepared.sampling.normal_east.reshape(1, -1)
            + sampled_v * prepared.sampling.normal_north.reshape(1, -1)
        )
    else:
        raise ValueError(f"Unsupported path horizontal projection component: {component}")

    vertical_coords = _resolve_path_vertical_coords(prepared, evaluator=evaluator)
    metadata = deepcopy(prepared.reduced_cube.metadata)
    metadata["source_dims"] = list(u_cube.dims)
    metadata["sampling"] = {
        "path_kind": "polyline",
        "interpolation": "bilinear",
        "sample_count": int(prepared.sampling.distances.shape[0]),
        "selected_time_index": int(prepared.sampling.time_index),
    }
    metadata["axis_projection_component"] = component
    metadata["path_basis"] = {
        "tangent_frame": "east_north",
        "normal_orientation": "left_of_path",
    }
    units = u_cube.units if u_cube.units == v_cube.units else None
    return _build_path_resolved_view_field(
        np.asarray(projected_values, dtype=float),
        prepared,
        vertical_coords=vertical_coords,
        units=units,
        metadata=metadata,
    )

def _resolve_path_view_field(
    cube: FieldCube,
    view_spec: dict[str, Any],
    *,
    current_time_index: int | None,
    evaluator: "FigureEvaluator" | None,
    frames: list[dict[str, Any]] | None,
) -> ResolvedViewField:
    if evaluator is None or frames is None:
        raise ValueError("Path-coordinate views require evaluator and frame context")
    prepared = _prepare_path_view_field(
        cube,
        view_spec,
        current_time_index=current_time_index,
        evaluator=evaluator,
        frames=frames,
    )
    sampled_values = _sample_with_path_context(prepared.reduced_cube.values, prepared.sampling)
    vertical_coords = _resolve_path_vertical_coords(prepared, evaluator=evaluator)
    metadata = deepcopy(prepared.reduced_cube.metadata)
    metadata["source_dims"] = list(cube.dims)
    metadata["sampling"] = {
        "path_kind": "polyline",
        "interpolation": "bilinear",
        "sample_count": int(prepared.sampling.distances.shape[0]),
        "selected_time_index": int(prepared.sampling.time_index),
    }
    return _build_path_resolved_view_field(
        sampled_values,
        prepared,
        vertical_coords=vertical_coords,
        units=prepared.reduced_cube.units,
        metadata=metadata,
    )


def _resolve_view_field(
    cube: FieldCube,
    view_spec: dict[str, Any],
    *,
    current_time_index: int | None,
    evaluator: "FigureEvaluator" | None = None,
    frames: list[dict[str, Any]] | None = None,
) -> ResolvedViewField:
    x_axis_kind = _view_axis_kind(view_spec.get("x_axis"))
    y_axis_kind = _view_axis_kind(view_spec.get("y_axis"))
    if "path_coord" in {x_axis_kind, y_axis_kind}:
        return _resolve_path_view_field(
            cube,
            view_spec,
            current_time_index=current_time_index,
            evaluator=evaluator,
            frames=frames,
        )
    if "derived_coord" in {x_axis_kind, y_axis_kind}:
        return _resolve_time_vertical_view_field(
            cube,
            view_spec,
            current_time_index=current_time_index,
            evaluator=evaluator,
            frames=frames,
        )
    return _resolve_native_view_field(
        cube,
        view_spec,
        current_time_index=current_time_index,
    )


def _render_raster(
    axis: Any,
    figure: Any,
    field: np.ndarray,
    *,
    draw: dict[str, Any],
    units: str | None,
    x_coords: np.ndarray | None = None,
    y_coords: np.ndarray | None = None,
) -> None:
    style = draw.get("style", {})
    mesh = _pcolormesh_coordinate_mesh(field, x_coords=x_coords, y_coords=y_coords)
    if mesh is None:
        image = axis.imshow(
            field,
            origin="lower",
            cmap=str(style.get("colormap") or "viridis"),
            alpha=float(draw.get("alpha", 1.0)),
            zorder=float(draw.get("zorder") or _default_zorder("raster")),
            vmin=style.get("vmin"),
            vmax=style.get("vmax"),
        )
    else:
        x_mesh, y_mesh = mesh
        image = axis.pcolormesh(
            x_mesh,
            y_mesh,
            field,
            shading="auto",
            cmap=str(style.get("colormap") or "viridis"),
            alpha=float(draw.get("alpha", 1.0)),
            zorder=float(draw.get("zorder") or _default_zorder("raster")),
            vmin=style.get("vmin"),
            vmax=style.get("vmax"),
        )
    if bool(style.get("show_colorbar", True)):
        colorbar = figure.colorbar(image, ax=axis, shrink=0.9)
        if units:
            colorbar.set_label(units)


def _render_contour(
    axis: Any,
    field: np.ndarray,
    *,
    draw: dict[str, Any],
    x_coords: np.ndarray | None = None,
    y_coords: np.ndarray | None = None,
) -> None:
    style = draw.get("style", {})
    mesh = _coordinate_mesh(field, x_coords=x_coords, y_coords=y_coords)
    if mesh is None:
        contour = axis.contour(
            field,
            levels=style.get("levels"),
            colors=style.get("colors"),
            linewidths=style.get("linewidths"),
            linestyles=style.get("linestyles"),
            alpha=float(draw.get("alpha", 1.0)),
            zorder=float(draw.get("zorder") or _default_zorder("contour")),
        )
    else:
        x_mesh, y_mesh = mesh
        contour = axis.contour(
            x_mesh,
            y_mesh,
            field,
            levels=style.get("levels"),
            colors=style.get("colors"),
            linewidths=style.get("linewidths"),
            linestyles=style.get("linestyles"),
            alpha=float(draw.get("alpha", 1.0)),
            zorder=float(draw.get("zorder") or _default_zorder("contour")),
        )
    if bool(style.get("label_contours", False)):
        axis.clabel(contour, fmt=str(style.get("label_format") or "%1.0f"))


def _render_categorical_fill(
    axis: Any,
    field: np.ndarray,
    *,
    draw: dict[str, Any],
    x_coords: np.ndarray | None = None,
    y_coords: np.ndarray | None = None,
) -> None:
    style = draw.get("style", {})
    mesh = _pcolormesh_coordinate_mesh(field, x_coords=x_coords, y_coords=y_coords)
    if mesh is not None:
        categories = list(style.get("categories", []))
        colors = [category["color"] for category in categories]
        values = [float(category["value"]) for category in categories]
        if not values:
            raise ValueError("categorical_fill requires at least one category")
        ordered = sorted(zip(values, colors), key=lambda item: item[0])
        ordered_values = [item[0] for item in ordered]
        ordered_colors = [item[1] for item in ordered]
        boundaries: list[float] = []
        for index, value in enumerate(ordered_values):
            if index == 0:
                boundaries.append(value - 0.5)
            else:
                boundaries.append((ordered_values[index - 1] + value) / 2.0)
        boundaries.append(ordered_values[-1] + 0.5)
        cmap = mcolors.ListedColormap(ordered_colors)
        norm = mcolors.BoundaryNorm(boundaries, cmap.N)
        x_mesh, y_mesh = mesh
        axis.pcolormesh(
            x_mesh,
            y_mesh,
            field,
            shading="auto",
            cmap=cmap,
            norm=norm,
            alpha=float(draw.get("alpha", 1.0)),
            zorder=float(draw.get("zorder") or _default_zorder("categorical_fill")),
        )
        return

    rgba = np.zeros(field.shape + (4,), dtype=float)
    draw_alpha = float(draw.get("alpha", 1.0))
    for category in style.get("categories", []):
        category_rgba = list(mcolors.to_rgba(category["color"]))
        category_rgba[3] *= draw_alpha
        rgba[np.asarray(field == category["value"])] = category_rgba
    axis.imshow(
        rgba,
        origin="lower",
        zorder=float(draw.get("zorder") or _default_zorder("categorical_fill")),
    )


def _render_vector(
    axis: Any,
    u_field: np.ndarray,
    v_field: np.ndarray,
    *,
    draw: dict[str, Any],
    x_coords: np.ndarray | None = None,
    y_coords: np.ndarray | None = None,
) -> None:
    if u_field.shape != v_field.shape:
        raise ValueError(
            "Vector render layers require u and v fields with matching shapes"
        )

    style = draw.get("style", {})
    stride = int(style.get("stride") or 1)
    sample = (slice(None, None, stride), slice(None, None, stride))
    mesh = _coordinate_mesh(u_field, x_coords=x_coords, y_coords=y_coords)
    if mesh is None:
        y_mesh, x_mesh = np.mgrid[0 : u_field.shape[0], 0 : u_field.shape[1]]
    else:
        x_mesh, y_mesh = mesh
    quiver_kwargs: dict[str, Any] = {
        "angles": "xy",
        "alpha": float(draw.get("alpha", 1.0)),
        "color": style.get("color", "black"),
        "pivot": style.get("pivot", "mid"),
        "zorder": float(draw.get("zorder") or _default_zorder("vector")),
    }
    if style.get("scale") is not None:
        quiver_kwargs["scale"] = float(style["scale"])
    axis.quiver(
        x_mesh[sample],
        y_mesh[sample],
        u_field[sample],
        v_field[sample],
        **quiver_kwargs,
    )


def _render_layer(
    axis: Any,
    figure: Any,
    field: np.ndarray,
    *,
    draw: dict[str, Any],
    units: str | None,
    x_coords: np.ndarray | None = None,
    y_coords: np.ndarray | None = None,
) -> None:
    kind = str(draw.get("kind") or "")
    if kind == "raster":
        _render_raster(axis, figure, field, draw=draw, units=units, x_coords=x_coords, y_coords=y_coords)
        return
    if kind == "contour":
        _render_contour(axis, field, draw=draw, x_coords=x_coords, y_coords=y_coords)
        return
    if kind == "categorical_fill":
        _render_categorical_fill(axis, field, draw=draw, x_coords=x_coords, y_coords=y_coords)
        return
    raise ValueError(f"Unsupported draw.kind: {kind}")


def _render_layer_target_ids(render_layer: dict[str, Any]) -> list[str]:
    draw = render_layer.get("draw")
    kind = str(draw.get("kind") or "") if isinstance(draw, dict) else ""
    if kind == "vector":
        target_ids: list[str] = []
        for key in ("u_layer_id", "v_layer_id", "vertical_layer_id"):
            value = render_layer.get(key)
            if isinstance(value, str) and value.strip() and value not in target_ids:
                target_ids.append(value)
        return target_ids
    value = render_layer.get("layer_id")
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _make_number(tokens: pp.ParseResults) -> NumberNode:
    return NumberNode(float(tokens[0]))


def _make_name(tokens: pp.ParseResults) -> NameNode:
    return NameNode(str(tokens[0]))


def _make_call(tokens: pp.ParseResults) -> CallNode:
    token = tokens[0]
    args = tuple(token.get("args", []))
    return CallNode(str(token["func"]), args)


def _make_unary(tokens: pp.ParseResults) -> UnaryOpNode:
    token = tokens[0]
    return UnaryOpNode(str(token[0]), token[1])


def _make_binary(tokens: pp.ParseResults) -> Any:
    token = tokens[0]
    node = token[0]
    for index in range(1, len(token), 2):
        node = BinaryOpNode(str(token[index]), node, token[index + 1])
    return node


def _expression_parser() -> pp.ParserElement:
    expr = pp.Forward()
    identifier = pp.Word(pp.alphas + "_", pp.alphanums + "_")
    number = pp.pyparsing_common.fnumber().set_parse_action(_make_number)
    name = identifier.copy().set_parse_action(_make_name)
    function_call = (
        pp.Group(
            identifier("func")
            + pp.Suppress("(")
            + pp.Optional(pp.delimited_list(expr), default=[])("args")
            + pp.Suppress(")")
        )
        .set_parse_action(_make_call)
    )
    atom = function_call | number | name | (pp.Suppress("(") + expr + pp.Suppress(")"))
    expr <<= pp.infix_notation(
        atom,
        [
            (pp.one_of("+ -"), 1, pp.opAssoc.RIGHT, _make_unary),
            (pp.Literal("**"), 2, pp.opAssoc.RIGHT, _make_binary),
            (pp.one_of("* /"), 2, pp.opAssoc.LEFT, _make_binary),
            (pp.one_of("+ -"), 2, pp.opAssoc.LEFT, _make_binary),
        ],
    )
    return expr


FORMULA_PARSER = _expression_parser()


def parse_formula(expr: str) -> Any:
    try:
        parsed = FORMULA_PARSER.parse_string(expr, parse_all=True)[0]
    except pp.ParseBaseException as exc:
        raise FormulaParseError(f"Invalid layer expression: {expr}") from exc
    return parsed


def _collect_layer_refs(node: Any, known_layers: set[str]) -> set[str]:
    if isinstance(node, NumberNode):
        return set()
    if isinstance(node, NameNode):
        return {node.name} if node.name in known_layers else set()
    if isinstance(node, UnaryOpNode):
        return _collect_layer_refs(node.operand, known_layers)
    if isinstance(node, BinaryOpNode):
        return _collect_layer_refs(node.left, known_layers) | _collect_layer_refs(node.right, known_layers)
    if isinstance(node, CallNode):
        refs: set[str] = set()
        for arg in node.args:
            refs |= _collect_layer_refs(arg, known_layers)
        return refs
    raise TypeError(f"Unsupported AST node: {type(node)!r}")


def resolve_layer_dependencies(
    layer_defs: dict[str, dict[str, Any]],
    root_layer_ids: list[str],
) -> tuple[dict[str, Any], list[str]]:
    known_layers = set(layer_defs)
    parsed_defs = {
        layer_id: parse_formula(str(layer_def.get("expr") or ""))
        for layer_id, layer_def in layer_defs.items()
    }
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(layer_id: str) -> None:
        if layer_id in visited:
            return
        if layer_id in visiting:
            raise LayerResolutionError(f"Cyclic layer dependency detected at: {layer_id}")
        if layer_id not in layer_defs:
            raise LayerResolutionError(f"Unknown layer_id referenced by figure: {layer_id}")

        visiting.add(layer_id)
        for dependency in sorted(_collect_layer_refs(parsed_defs[layer_id], known_layers)):
            visit(dependency)
        visiting.remove(layer_id)
        visited.add(layer_id)
        order.append(layer_id)

    for layer_id in root_layer_ids:
        visit(layer_id)
    return parsed_defs, order


def _node_uses_current(
    node: Any,
    layer_uses_current: Any,
    *,
    fixed_scope: bool = False,
) -> bool:
    if isinstance(node, NumberNode):
        return False
    if isinstance(node, NameNode):
        if fixed_scope:
            return False
        return bool(layer_uses_current(node.name))
    if isinstance(node, UnaryOpNode):
        return _node_uses_current(node.operand, layer_uses_current, fixed_scope=fixed_scope)
    if isinstance(node, BinaryOpNode):
        return _node_uses_current(node.left, layer_uses_current, fixed_scope=fixed_scope) or _node_uses_current(
            node.right,
            layer_uses_current,
            fixed_scope=fixed_scope,
        )
    if isinstance(node, CallNode):
        func = node.name.lower()
        if func in {"first", "last"}:
            return False
        if func == "current":
            return any(
                _node_uses_current(arg, layer_uses_current, fixed_scope=False)
                for arg in node.args
            )
        return any(
            _node_uses_current(arg, layer_uses_current, fixed_scope=fixed_scope)
            for arg in node.args
        )
    raise TypeError(f"Unsupported AST node: {type(node)!r}")


def layer_uses_current(
    layer_id: str,
    parsed_defs: dict[str, Any],
    *,
    memo: dict[str, bool] | None = None,
) -> bool:
    cache = memo if memo is not None else {}
    if layer_id in cache:
        return cache[layer_id]

    def _delegate(name: str) -> bool:
        if name in parsed_defs:
            return layer_uses_current(name, parsed_defs, memo=cache)
        return True

    cache[layer_id] = _node_uses_current(parsed_defs[layer_id], _delegate)
    return cache[layer_id]


class FigureEvaluator:
    def __init__(
        self,
        layer_defs: dict[str, dict[str, Any]],
        parsed_defs: dict[str, Any],
        frames: list[dict[str, Any]],
    ) -> None:
        self.layer_defs = layer_defs
        self.parsed_defs = parsed_defs
        self.frames = frames
        self.variable_cache: dict[tuple[str, int, str], tuple[np.ndarray, str | None]] = {}
        self.variable_3d_cache: dict[
            tuple[str, int, str, str],
            tuple[np.ndarray, str | None],
        ] = {}
        self.variable_3d_full_cache: dict[
            tuple[str, int, str],
            tuple[np.ndarray, str | None],
        ] = {}
        self.diagnostic_cache: dict[tuple[str, int, str], tuple[np.ndarray, str | None]] = {}
        self.horizontal_coord_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
        self.mass_height_cache: dict[tuple[str, int], np.ndarray] = {}
        self.mass_pressure_cache: dict[tuple[str, int], np.ndarray] = {}
        self.layer_cube_cache: dict[tuple[str, int | None], FieldCube] = {}
        self.time_cube_cache: dict[str, FieldCube] = {}

    def _cache_key(self, layer_id: str, current_frame: dict[str, Any] | None) -> tuple[str, int | None]:
        if current_frame is None:
            return (layer_id, None)
        return (layer_id, int(current_frame["global_index"]))

    def load_variable(
        self,
        frame: dict[str, Any],
        name: str,
    ) -> tuple[np.ndarray, str | None]:
        key = (posix_path(frame["path"]), int(frame["time_index"]), name)
        cached = self.variable_cache.get(key)
        if cached is not None:
            return cached

        with Dataset(frame["path"]) as dataset:
            field, units = _load_2d_var(dataset, name, int(frame["time_index"]))
        self.variable_cache[key] = (field, units)
        return field, units

    def load_variable_3d(
        self,
        frame: dict[str, Any],
        name: str,
        *,
        level_selector: dict[str, Any] | None,
    ) -> tuple[np.ndarray, str | None]:
        selector_key = json.dumps(level_selector or {"mode": "index", "index": 0}, sort_keys=True)
        key = (posix_path(frame["path"]), int(frame["time_index"]), name, selector_key)
        cached = self.variable_3d_cache.get(key)
        if cached is not None:
            return cached

        with Dataset(frame["path"]) as dataset:
            field, units = _load_3d_var(
                dataset,
                name,
                int(frame["time_index"]),
                level_selector=level_selector,
            )
        self.variable_3d_cache[key] = (field, units)
        return field, units

    def load_variable_3d_full(
        self,
        frame: dict[str, Any],
        name: str,
    ) -> tuple[np.ndarray, str | None]:
        key = (posix_path(frame["path"]), int(frame["time_index"]), name)
        cached = self.variable_3d_full_cache.get(key)
        if cached is not None:
            return cached

        with Dataset(frame["path"]) as dataset:
            field, units = _load_3d_var_full(dataset, name, int(frame["time_index"]))
        self.variable_3d_full_cache[key] = (field, units)
        return field, units

    def load_horizontal_coords(
        self,
        frame: dict[str, Any],
    ) -> tuple[np.ndarray, np.ndarray]:
        key = (posix_path(frame["path"]), int(frame["time_index"]))
        cached = self.horizontal_coord_cache.get(key)
        if cached is not None:
            return cached

        with Dataset(frame["path"]) as dataset:
            xlat = dataset.variables.get("XLAT")
            xlong = dataset.variables.get("XLONG")
            if xlat is None or xlong is None:
                raise KeyError("Path-coordinate views require XLAT and XLONG in wrfout files")

            def _read_coord(variable: Any) -> np.ndarray:
                raw = variable[int(frame["time_index"])] if variable.ndim >= 3 else variable[:]
                field = np.asarray(np.ma.filled(raw, np.nan), dtype=float)
                if field.ndim != 2:
                    raise ValueError(
                        f"Expected 2D coordinate field for {getattr(variable, 'name', 'coord')}, received ndim={field.ndim}"
                    )
                return field

            payload = (_read_coord(xlat), _read_coord(xlong))
        self.horizontal_coord_cache[key] = payload
        return payload

    def load_mass_height(
        self,
        frame: dict[str, Any],
    ) -> np.ndarray:
        key = (posix_path(frame["path"]), int(frame["time_index"]))
        cached = self.mass_height_cache.get(key)
        if cached is not None:
            return cached

        with Dataset(frame["path"]) as dataset:
            ph = dataset.variables.get("PH")
            phb = dataset.variables.get("PHB")
            if ph is None or phb is None:
                raise KeyError("height_m views require PH and PHB in wrfout files")
            ph_raw = ph[int(frame["time_index"])] if ph.ndim >= 4 else ph[:]
            phb_raw = phb[int(frame["time_index"])] if phb.ndim >= 4 else phb[:]
            geopotential = np.asarray(np.ma.filled(ph_raw, np.nan), dtype=float) + np.asarray(
                np.ma.filled(phb_raw, np.nan),
                dtype=float,
            )
            if geopotential.ndim != 3:
                raise ValueError(f"Expected 3D geopotential field, received ndim={geopotential.ndim}")
            payload = 0.5 * (geopotential[:-1] + geopotential[1:]) / 9.81

        self.mass_height_cache[key] = payload
        return payload

    def load_mass_pressure(
        self,
        frame: dict[str, Any],
    ) -> np.ndarray:
        key = (posix_path(frame["path"]), int(frame["time_index"]))
        cached = self.mass_pressure_cache.get(key)
        if cached is not None:
            return cached

        with Dataset(frame["path"]) as dataset:
            p = dataset.variables.get("P")
            pb = dataset.variables.get("PB")
            if p is None or pb is None:
                raise KeyError("pressure_hpa views require P and PB in wrfout files")
            p_raw = p[int(frame["time_index"])] if p.ndim >= 4 else p[:]
            pb_raw = pb[int(frame["time_index"])] if pb.ndim >= 4 else pb[:]
            pressure = np.asarray(np.ma.filled(p_raw, np.nan), dtype=float) + np.asarray(
                np.ma.filled(pb_raw, np.nan),
                dtype=float,
            )
            if pressure.ndim != 3:
                raise ValueError(f"Expected 3D pressure field, received ndim={pressure.ndim}")
            payload = pressure / 100.0

        self.mass_pressure_cache[key] = payload
        return payload

    def load_diagnostic(
        self,
        frame: dict[str, Any],
        name: str,
    ) -> tuple[np.ndarray, str | None]:
        key = (posix_path(frame["path"]), int(frame["time_index"]), name.lower())
        cached = self.diagnostic_cache.get(key)
        if cached is not None:
            return cached

        diag_name = name.lower()
        payload: tuple[np.ndarray, str | None]
        if diag_name in {"wind_speed_10m", "wind10m", "wind_speed10m"}:
            u10, _ = self.load_variable(frame, "U10")
            v10, _ = self.load_variable(frame, "V10")
            payload = (np.sqrt(u10**2 + v10**2), "m s-1")
        elif diag_name in {"wind_dir_10m", "winddir10m", "wind_direction_10m"}:
            u10, _ = self.load_variable(frame, "U10")
            v10, _ = self.load_variable(frame, "V10")
            payload = ((270.0 - np.degrees(np.arctan2(v10, u10))) % 360.0, "deg")
        elif diag_name in {"total_precip", "precip_total", "total_precipitation"}:
            rainc, units = self.load_variable(frame, "RAINC")
            rainnc, _ = self.load_variable(frame, "RAINNC")
            payload = (rainc + rainnc, units or "mm")
        elif diag_name in {"temp_c_2m", "t2_c", "temperature_2m_c"}:
            t2, _ = self.load_variable(frame, "T2")
            payload = (t2 - 273.15, "C")
        elif diag_name in {"rh2", "relative_humidity_2m"}:
            q2, _ = self.load_variable(frame, "Q2")
            psfc, _ = self.load_variable(frame, "PSFC")
            t2, _ = self.load_variable(frame, "T2")
            epsilon = 0.622
            vapor_pressure = q2 * psfc / np.maximum(epsilon + q2, 1.0e-12)
            temp_c = t2 - 273.15
            saturation = 611.2 * np.exp((17.67 * temp_c) / np.maximum(temp_c + 243.5, 1.0e-12))
            rh = 100.0 * vapor_pressure / np.maximum(saturation, 1.0e-12)
            payload = (np.clip(rh, 0.0, 100.0), "%")
        else:
            supported = [
                "rh2",
                "temp_c_2m",
                "total_precip",
                "wind_dir_10m",
                "wind_speed_10m",
            ]
            raise ValueError(
                f"Unsupported wrf_diag name: {name}. Supported diagnostics: {', '.join(sorted(supported))}"
            )

        self.diagnostic_cache[key] = payload
        return payload

    def resolve_external_name(
        self,
        frame: dict[str, Any],
        name: str,
        source: dict[str, Any],
    ) -> tuple[np.ndarray, str | None]:
        source_kind = _normalize_source_kind(str(source.get("kind") or "wrf_native"))
        if source_kind == "wrf_native_2d":
            return self.load_variable(frame, name)
        if source_kind == "wrf_native_3d":
            return self.load_variable_3d(
                frame,
                name,
                level_selector=source.get("level_selector"),
            )
        if source_kind == "wrf_native_3d_full":
            return self.load_variable_3d_full(frame, name)
        if source_kind == "wrf_diag":
            return self.load_diagnostic(frame, name)
        raise NotImplementedError(
            f"source.kind is recognized but not implemented yet: {source_kind}"
        )

    def evaluate_layer_cube(
        self,
        layer_id: str,
        current_frame: dict[str, Any],
    ) -> FieldCube:
        cache_key = self._cache_key(layer_id, current_frame)
        cached = self.layer_cube_cache.get(cache_key)
        if cached is not None:
            return cached

        layer_def = self.layer_defs[layer_id]
        source = layer_def.get("source", {})
        source_kind = _normalize_source_kind(str(source.get("kind") or "wrf_native"))
        if source_kind not in {_normalize_source_kind(kind) for kind in SUPPORTED_SOURCE_KINDS}:
            raise NotImplementedError(
                f"source.kind is recognized but not implemented yet: {source_kind}"
            )

        field = np.asarray(
            np.ma.filled(self._evaluate_node(self.parsed_defs[layer_id], current_frame, source=source), np.nan),
            dtype=float,
        )
        units = layer_def.get("units")
        units_value = None if units is None else str(units)
        payload = _build_field_cube(
            field,
            dims=_dims_for_field(field, label=f"layer_defs.{layer_id}"),
            units=units_value,
            metadata={
                "layer_id": layer_id,
                "source_kind": source_kind,
            },
            label=f"layer_defs.{layer_id}",
        )
        self.layer_cube_cache[cache_key] = payload
        return payload

    def evaluate_layer(
        self,
        layer_id: str,
        current_frame: dict[str, Any],
    ) -> tuple[np.ndarray, str | None]:
        cube = self.evaluate_layer_cube(layer_id, current_frame)
        return cube.values, cube.units

    def build_time_cube(self, layer_id: str) -> FieldCube:
        cached = self.time_cube_cache.get(layer_id)
        if cached is not None:
            return cached

        cubes: list[FieldCube] = []
        first_cube: FieldCube | None = None
        for index, frame in enumerate(self.frames):
            cube = self.evaluate_layer_cube(layer_id, frame)
            cubes.append(cube)
            if first_cube is None:
                first_cube = cube
                continue
            if cube.dims != first_cube.dims:
                raise ValueError(
                    f"layer_defs.{layer_id} frame {index} changed dims from {first_cube.dims} to {cube.dims}"
                )
            if cube.values.shape != first_cube.values.shape:
                raise ValueError(
                    f"layer_defs.{layer_id} frame {index} changed shape from {first_cube.values.shape} to {cube.values.shape}"
                )

        if first_cube is None:
            raise ValueError("Cannot build section field from an empty frame list")

        coords = {"time": np.arange(len(cubes), dtype=float)}
        for dim, values in (first_cube.coords or {}).items():
            coords[dim] = np.asarray(values, dtype=float)

        metadata = deepcopy(first_cube.metadata)
        metadata["frame_count"] = len(cubes)
        payload = _build_field_cube(
            np.stack([cube.values for cube in cubes], axis=0),
            dims=("time",) + first_cube.dims,
            units=first_cube.units,
            metadata=metadata,
            coords=coords,
            label=f"layer_defs.{layer_id}",
        )
        self.time_cube_cache[layer_id] = payload
        return payload

    def _evaluate_node(
        self,
        node: Any,
        current_frame: dict[str, Any],
        *,
        source: dict[str, Any],
    ) -> Any:
        if isinstance(node, NumberNode):
            return float(node.value)
        if isinstance(node, NameNode):
            if node.name in self.layer_defs:
                return self.evaluate_layer(node.name, current_frame)[0]
            return self.resolve_external_name(current_frame, node.name, source)[0]
        if isinstance(node, UnaryOpNode):
            value = self._evaluate_node(node.operand, current_frame, source=source)
            if node.op == "+":
                return value
            if node.op == "-":
                return -value
            raise ValueError(f"Unsupported unary operator: {node.op}")
        if isinstance(node, BinaryOpNode):
            left = self._evaluate_node(node.left, current_frame, source=source)
            right = self._evaluate_node(node.right, current_frame, source=source)
            if node.op == "+":
                return left + right
            if node.op == "-":
                return left - right
            if node.op == "*":
                return left * right
            if node.op == "/":
                return left / right
            if node.op == "**":
                return left ** right
            raise ValueError(f"Unsupported binary operator: {node.op}")
        if isinstance(node, CallNode):
            func = node.name.lower()
            if func not in FUNCTION_NAMES:
                raise ValueError(f"Unsupported expression function: {node.name}")
            if func == "current":
                if len(node.args) != 1:
                    raise ValueError("current() expects exactly one argument")
                return self._evaluate_node(node.args[0], current_frame, source=source)
            if func == "first":
                if len(node.args) != 1:
                    raise ValueError("first() expects exactly one argument")
                return self._evaluate_node(node.args[0], self.frames[0], source=source)
            if func == "last":
                if len(node.args) != 1:
                    raise ValueError("last() expects exactly one argument")
                return self._evaluate_node(node.args[0], self.frames[-1], source=source)

            args = [self._evaluate_node(arg, current_frame, source=source) for arg in node.args]
            if func == "sqrt":
                return np.sqrt(args[0])
            if func == "abs":
                return np.abs(args[0])
            if func == "minimum":
                if len(args) < 2:
                    raise ValueError("minimum() expects at least two arguments")
                result = args[0]
                for arg in args[1:]:
                    result = np.minimum(result, arg)
                return result
            if func == "maximum":
                if len(args) < 2:
                    raise ValueError("maximum() expects at least two arguments")
                result = args[0]
                for arg in args[1:]:
                    result = np.maximum(result, arg)
                return result
            if func == "clip":
                if len(args) != 3:
                    raise ValueError("clip() expects exactly three arguments")
                return np.clip(args[0], args[1], args[2])
            if func == "where":
                if len(args) != 3:
                    raise ValueError("where() expects exactly three arguments")
                return np.where(args[0], args[1], args[2])
        raise TypeError(f"Unsupported AST node: {type(node)!r}")


def run_figure_request(
    figure_spec: dict[str, Any],
    layer_defs: dict[str, dict[str, Any]],
    frames: list[dict[str, Any]],
    base_output_dir: Path | str,
    *,
    view_defs: dict[str, dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    ensure_plotting_dependencies()
    if not frames:
        raise ValueError("No frames selected for figure rendering")

    view_spec, view_id = _resolve_figure_view(figure_spec, view_defs)
    map_view = _is_map_view(view_spec)

    root_layer_ids: list[str] = []
    for render_layer in figure_spec.get("layers", []):
        for layer_id in _render_layer_target_ids(render_layer):
            if layer_id not in root_layer_ids:
                root_layer_ids.append(layer_id)
    parsed_defs, _ = resolve_layer_dependencies(layer_defs, root_layer_ids)
    usage_memo: dict[str, bool] = {}
    expression_uses_current = any(
        layer_uses_current(layer_id, parsed_defs, memo=usage_memo)
        for layer_id in root_layer_ids
    )
    if _view_has_axis(view_spec, "time"):
        uses_current = False
    else:
        uses_current = expression_uses_current or _view_time_selector_mode(view_spec) == "current"

    artifacts: list[dict[str, Any]] = []
    grouped_frames = _group_frames_by_domain(frames)
    for _, group in grouped_frames:
        if not group:
            continue

        evaluator = FigureEvaluator(layer_defs, parsed_defs, group)
        output_targets = group if uses_current else [group[-1]]
        if _view_has_axis(view_spec, "time"):
            output_targets = [group[-1]]
        allow_exact_path = len(output_targets) == 1 and len(grouped_frames) == 1
        output_cfg = figure_spec.get("output", {})
        overwrite = bool(output_cfg.get("overwrite", False))
        sidecar_enabled = bool(output_cfg.get("sidecar_json", True))

        for target in output_targets:
            current_frame = target if uses_current else None
            if _view_has_axis(view_spec, "time"):
                current_frame = None
            title = _compose_title(
                figure_spec,
                group,
                current_frame,
                uses_current=uses_current,
            )
            output_path = _build_output_path(
                Path(base_output_dir),
                figure_spec,
                _figure_suffix_tokens(group, target, uses_current=uses_current),
                allow_exact_path=allow_exact_path,
            )
            sidecar_path = output_path.with_suffix(".json") if sidecar_enabled else None

            resolved_view = deepcopy(view_spec)
            resolved_layers: list[dict[str, Any]] = []
            layer_summaries: dict[str, dict[str, float | None]] = {}

            if output_path.exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing plot: {output_path}")
            if sidecar_path is not None and sidecar_path.exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing sidecar: {sidecar_path}")

            figure = axis = None
            if not dry_run:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                figure, axis = plt.subplots(figsize=(6, 5), constrained_layout=True)
                axis.set_xlabel(_axis_label(view_spec.get("x_axis")))
                axis.set_ylabel(_axis_label(view_spec.get("y_axis")))
                axis.set_title(title)

            current_time_index: int | None = None
            if current_frame is not None:
                for index, candidate in enumerate(group):
                    if int(candidate["global_index"]) == int(current_frame["global_index"]):
                        current_time_index = index
                        break
                if current_time_index is None:
                    raise ValueError("Current frame is not part of the selected frame group")

            for render_layer in figure_spec.get("layers", []):
                draw = render_layer["draw"]
                if str(draw.get("kind") or "") == "vector":
                    axis_projection = _resolve_vector_axis_projection(draw, view_spec=view_spec)
                    u_layer_id = str(render_layer["u_layer_id"])
                    v_layer_id = str(render_layer["v_layer_id"])
                    if map_view:
                        u_cube = evaluator.evaluate_layer_cube(u_layer_id, target)
                        v_cube = evaluator.evaluate_layer_cube(v_layer_id, target)
                    else:
                        u_cube = evaluator.build_time_cube(u_layer_id)
                        v_cube = evaluator.build_time_cube(v_layer_id)
                    u_view = _resolve_view_field(
                        u_cube,
                        view_spec,
                        current_time_index=current_time_index,
                        evaluator=evaluator,
                        frames=group,
                    )
                    v_view = _resolve_view_field(
                        v_cube,
                        view_spec,
                        current_time_index=current_time_index,
                        evaluator=evaluator,
                        frames=group,
                    )
                    u_view = _coerce_same_shape_view(u_view, expected=u_view, label="u_layer")
                    v_view = _coerce_same_shape_view(v_view, expected=u_view, label="v_layer")
                    component_views: dict[str, ResolvedViewField] = {}
                    if axis_projection["kind"] == MAP_VECTOR_PROJECTION_KIND:
                        if render_layer.get("vertical_layer_id") is not None:
                            raise ValueError("Map-view vector layers do not use vertical_layer_id")
                        component_views["u"] = u_view
                        component_views["v"] = v_view
                    elif axis_projection["kind"] == PATH_SECTION_VECTOR_PROJECTION_KIND:
                        if not _is_path_view(view_spec):
                            raise ValueError(
                                f"draw.style.axis_projection.kind={PATH_SECTION_VECTOR_PROJECTION_KIND} "
                                f"is only valid for path views: figure={figure_spec['figure_id']}"
                            )
                        for component_name in {axis_projection["x_component"], axis_projection["y_component"]}:
                            if component_name in {"path_tangent", "path_normal"}:
                                component_views[component_name] = _resolve_path_projected_horizontal_component(
                                    u_cube,
                                    v_cube,
                                    view_spec,
                                    component=component_name,
                                    current_time_index=current_time_index,
                                    evaluator=evaluator,
                                    frames=group,
                                )
                            elif component_name == "vertical":
                                vertical_layer_id = render_layer.get("vertical_layer_id")
                                if not isinstance(vertical_layer_id, str) or not vertical_layer_id.strip():
                                    raise ValueError(
                                        "Path-section vector layers using vertical axis_projection "
                                        "require vertical_layer_id"
                                    )
                                vertical_cube = evaluator.build_time_cube(str(vertical_layer_id))
                                vertical_view = _resolve_view_field(
                                    vertical_cube,
                                    view_spec,
                                    current_time_index=current_time_index,
                                    evaluator=evaluator,
                                    frames=group,
                                )
                                component_views["vertical"] = _coerce_same_shape_view(
                                    vertical_view,
                                    expected=u_view,
                                    label="vertical_layer",
                                )
                            else:
                                raise ValueError(
                                    f"Unsupported path-section axis_projection component: {component_name}"
                                )
                    else:
                        raise ValueError(
                            f"Unsupported vector axis_projection kind: {axis_projection['kind']}"
                        )

                    x_view = component_views[axis_projection["x_component"]]
                    y_view = _coerce_same_shape_view(
                        component_views[axis_projection["y_component"]],
                        expected=x_view,
                        label="vector_component",
                    )
                    x_field = x_view.values
                    y_field = y_view.values
                    resolved_view["x_axis"] = deepcopy(x_view.x_axis)
                    resolved_view["y_axis"] = deepcopy(x_view.y_axis)
                    magnitude = np.sqrt(x_field**2 + y_field**2)
                    u_summary = _summary(u_view.values)
                    v_summary = _summary(v_view.values)
                    x_summary = _summary(x_field)
                    y_summary = _summary(y_field)
                    magnitude_summary = _summary(magnitude)
                    layer_summaries[u_layer_id] = u_summary
                    layer_summaries[v_layer_id] = v_summary
                    resolved_layer = {
                        "u_layer_id": u_layer_id,
                        "v_layer_id": v_layer_id,
                        "style_id": render_layer.get("style_id"),
                        "u_expr": str(layer_defs[u_layer_id].get("expr") or ""),
                        "v_expr": str(layer_defs[v_layer_id].get("expr") or ""),
                        "u_source_kind": str(
                            layer_defs[u_layer_id].get("source", {}).get("kind") or "wrf_native"
                        ),
                        "v_source_kind": str(
                            layer_defs[v_layer_id].get("source", {}).get("kind") or "wrf_native"
                        ),
                        "u_source": deepcopy(layer_defs[u_layer_id].get("source") or {}),
                        "v_source": deepcopy(layer_defs[v_layer_id].get("source") or {}),
                        "u_units": layer_defs[u_layer_id].get("units"),
                        "v_units": layer_defs[v_layer_id].get("units"),
                        "u_summary": u_summary,
                        "v_summary": v_summary,
                        "axis_projection": deepcopy(axis_projection),
                        "x_component_units": x_view.units,
                        "y_component_units": y_view.units,
                        "x_component_summary": x_summary,
                        "y_component_summary": y_summary,
                        "magnitude_units": x_view.units if x_view.units == y_view.units else None,
                        "magnitude_summary": magnitude_summary,
                        "draw": deepcopy(draw),
                    }
                    vertical_layer_id = render_layer.get("vertical_layer_id")
                    if (
                        isinstance(vertical_layer_id, str)
                        and vertical_layer_id.strip()
                        and "vertical" in {axis_projection["x_component"], axis_projection["y_component"]}
                    ):
                        vertical_layer_id = str(vertical_layer_id)
                        layer_summaries[vertical_layer_id] = _summary(component_views["vertical"].values)
                        resolved_layer.update(
                            {
                                "vertical_layer_id": vertical_layer_id,
                                "vertical_expr": str(layer_defs[vertical_layer_id].get("expr") or ""),
                                "vertical_source_kind": str(
                                    layer_defs[vertical_layer_id].get("source", {}).get("kind") or "wrf_native"
                                ),
                                "vertical_source": deepcopy(layer_defs[vertical_layer_id].get("source") or {}),
                                "vertical_units": layer_defs[vertical_layer_id].get("units"),
                                "vertical_summary": layer_summaries[vertical_layer_id],
                            }
                        )
                    resolved_layers.append(resolved_layer)
                    if not dry_run and axis is not None:
                        _render_vector(
                            axis,
                            x_field,
                            y_field,
                            draw=draw,
                            x_coords=x_view.x_coords,
                            y_coords=x_view.y_coords,
                        )
                    continue

                layer_id = str(render_layer["layer_id"])
                if map_view:
                    cube = evaluator.evaluate_layer_cube(layer_id, target)
                else:
                    cube = evaluator.build_time_cube(layer_id)
                resolved_field = _resolve_view_field(
                    cube,
                    view_spec,
                    current_time_index=current_time_index,
                    evaluator=evaluator,
                    frames=group,
                )
                field = resolved_field.values
                units = resolved_field.units
                resolved_view["x_axis"] = deepcopy(resolved_field.x_axis)
                resolved_view["y_axis"] = deepcopy(resolved_field.y_axis)
                layer_summaries[layer_id] = _summary(field)
                resolved_layers.append(
                    {
                        "layer_id": layer_id,
                        "style_id": render_layer.get("style_id"),
                        "expr": str(layer_defs[layer_id].get("expr") or ""),
                        "source_kind": str(
                            layer_defs[layer_id].get("source", {}).get("kind") or "wrf_native"
                        ),
                        "source": deepcopy(layer_defs[layer_id].get("source") or {}),
                        "units": layer_defs[layer_id].get("units"),
                        "view_axes": {
                            "x": str(resolved_field.x_axis.get("name") or ""),
                            "y": str(resolved_field.y_axis.get("name") or ""),
                        },
                        "draw": deepcopy(draw),
                    }
                )
                if not dry_run and axis is not None and figure is not None:
                    _render_layer(
                        axis,
                        figure,
                        field,
                        draw=draw,
                        units=units,
                        x_coords=resolved_field.x_coords,
                        y_coords=resolved_field.y_coords,
                    )

            payload = _artifact_payload(
                figure_spec,
                output_path=output_path,
                sidecar_path=sidecar_path,
                selected_frames=group,
                current_frame=current_frame,
                title=title,
                view=resolved_view,
                view_id=view_id,
                resolved_layers=resolved_layers,
                layer_summaries=layer_summaries,
            )

            if not dry_run and figure is not None:
                figure.savefig(output_path, dpi=int(figure_spec.get("render", {}).get("dpi") or 150))
                plt.close(figure)
                if sidecar_path is not None:
                    _write_json(sidecar_path, payload)
            artifacts.append(payload)

    return artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a single figure from a v2 WRF post-processing spec."
    )
    parser.add_argument("--wrfout", nargs="+", required=True)
    parser.add_argument("--figure-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--post-spec")
    parser.add_argument("--project-name", default="demo")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.post_spec:
        payload = load_json(args.post_spec)
    else:
        payload = default_post_spec(args.project_name)

    normalized = normalize_post_spec(payload, project_name_fallback=args.project_name)
    errors = validate_post_spec(normalized)
    if errors:
        raise SystemExit("\n".join(errors))

    figure_spec = None
    for candidate in normalized["figures"]:
        if candidate.get("figure_id") == args.figure_id:
            figure_spec = deepcopy(candidate)
            break
    if figure_spec is None:
        raise SystemExit(f"Unknown figure_id: {args.figure_id}")

    wrfout_paths = [Path(item) for item in args.wrfout]
    for wrfout_path in wrfout_paths:
        if not wrfout_path.exists():
            raise SystemExit(f"Missing wrfout file: {wrfout_path}")

    output_path = Path(args.out)
    figure_spec["inputs"] = {
        "mode": "explicit_paths",
        "paths": [posix_path(path) for path in wrfout_paths],
    }
    figure_spec.setdefault("render", {})
    figure_spec["render"]["format"] = (output_path.suffix.lstrip(".") or "png").lower()
    figure_spec.setdefault("output", {})
    figure_spec["output"].update(
        {
            "subdir": "",
            "file_stem": output_path.stem,
            "sidecar_json": True,
            "overwrite": True,
            "path": posix_path(output_path),
        }
    )

    frames = enumerate_wrfout_frames(wrfout_paths)
    selected_frames = select_wrfout_frames(frames, figure_spec.get("selectors"))
    if not selected_frames:
        raise SystemExit("No matching frames after applying selectors")

    result = {
        "dry_run": bool(args.dry_run),
        "artifacts": run_figure_request(
            figure_spec,
            normalized["layer_defs"],
            selected_frames,
            output_path.parent,
            view_defs=normalized.get("view_defs"),
            dry_run=bool(args.dry_run),
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
