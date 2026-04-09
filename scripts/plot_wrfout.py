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
DRAW_KINDS = {"raster", "contour", "categorical_fill"}
SOURCE_KIND_ALIASES = {
    "wrf_native": "wrf_native_2d",
}
SUPPORTED_SOURCE_KINDS = {
    "wrf_native",
    "wrf_native_2d",
    "wrf_native_3d",
    "wrf_diag",
}
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

    level_index = _resolve_level_index(level_selector, int(field.shape[0]))
    level_field = np.asarray(field[level_index], dtype=float)
    if level_field.ndim != 2:
        raise ValueError(
            f"Expected 2D slice for {name} after level selection, received ndim={level_field.ndim}"
        )
    return level_field, getattr(variable, "units", None)


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
        "resolved_layers": resolved_layers,
        "layer_summaries": layer_summaries,
    }


def _ensure_2d_field(value: Any, *, label: str) -> np.ndarray:
    field = np.asarray(np.ma.filled(value, np.nan), dtype=float)
    if field.ndim != 2:
        raise ValueError(f"{label} must resolve to a 2D field, received ndim={field.ndim}")
    return field


def _render_raster(
    axis: Any,
    figure: Any,
    field: np.ndarray,
    *,
    draw: dict[str, Any],
    units: str | None,
) -> None:
    style = draw.get("style", {})
    image = axis.imshow(
        field,
        origin="lower",
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


def _render_contour(axis: Any, field: np.ndarray, *, draw: dict[str, Any]) -> None:
    style = draw.get("style", {})
    contour = axis.contour(
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


def _render_categorical_fill(axis: Any, field: np.ndarray, *, draw: dict[str, Any]) -> None:
    style = draw.get("style", {})
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


def _render_layer(
    axis: Any,
    figure: Any,
    field: np.ndarray,
    *,
    draw: dict[str, Any],
    units: str | None,
) -> None:
    kind = str(draw.get("kind") or "")
    if kind == "raster":
        _render_raster(axis, figure, field, draw=draw, units=units)
        return
    if kind == "contour":
        _render_contour(axis, field, draw=draw)
        return
    if kind == "categorical_fill":
        _render_categorical_fill(axis, field, draw=draw)
        return
    raise ValueError(f"Unsupported draw.kind: {kind}")


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
        self.diagnostic_cache: dict[tuple[str, int, str], tuple[np.ndarray, str | None]] = {}
        self.layer_cache: dict[tuple[str, int | None], tuple[np.ndarray, str | None]] = {}

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
        if source_kind == "wrf_diag":
            return self.load_diagnostic(frame, name)
        raise NotImplementedError(
            f"source.kind is recognized but not implemented yet: {source_kind}"
        )

    def evaluate_layer(
        self,
        layer_id: str,
        current_frame: dict[str, Any],
    ) -> tuple[np.ndarray, str | None]:
        cache_key = self._cache_key(layer_id, current_frame)
        cached = self.layer_cache.get(cache_key)
        if cached is not None:
            return cached

        layer_def = self.layer_defs[layer_id]
        source = layer_def.get("source", {})
        source_kind = _normalize_source_kind(str(source.get("kind") or "wrf_native"))
        if source_kind not in {_normalize_source_kind(kind) for kind in SUPPORTED_SOURCE_KINDS}:
            raise NotImplementedError(
                f"source.kind is recognized but not implemented yet: {source_kind}"
            )

        field = _ensure_2d_field(
            self._evaluate_node(self.parsed_defs[layer_id], current_frame, source=source),
            label=f"layer_defs.{layer_id}",
        )
        units = layer_def.get("units")
        units_value = None if units is None else str(units)
        payload = (field, units_value)
        self.layer_cache[cache_key] = payload
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
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    ensure_plotting_dependencies()
    if not frames:
        raise ValueError("No frames selected for figure rendering")

    root_layer_ids = [str(layer["layer_id"]) for layer in figure_spec.get("layers", [])]
    parsed_defs, _ = resolve_layer_dependencies(layer_defs, root_layer_ids)
    usage_memo: dict[str, bool] = {}
    uses_current = any(
        layer_uses_current(layer_id, parsed_defs, memo=usage_memo)
        for layer_id in root_layer_ids
    )

    artifacts: list[dict[str, Any]] = []
    for _, group in _group_frames_by_domain(frames):
        if not group:
            continue

        evaluator = FigureEvaluator(layer_defs, parsed_defs, group)
        output_targets = group if uses_current else [group[-1]]
        allow_exact_path = len(output_targets) == 1 and len(_group_frames_by_domain(frames)) == 1
        output_cfg = figure_spec.get("output", {})
        overwrite = bool(output_cfg.get("overwrite", False))
        sidecar_enabled = bool(output_cfg.get("sidecar_json", True))

        for target in output_targets:
            current_frame = target if uses_current else None
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
                axis.set_xlabel("west_east")
                axis.set_ylabel("south_north")
                axis.set_title(title)

            for render_layer in figure_spec.get("layers", []):
                layer_id = str(render_layer["layer_id"])
                draw = render_layer["draw"]
                field, units = evaluator.evaluate_layer(layer_id, target)
                layer_summaries[layer_id] = _summary(field)
                resolved_layers.append(
                    {
                        "layer_id": layer_id,
                        "style_id": render_layer.get("style_id"),
                        "expr": str(layer_defs[layer_id].get("expr") or ""),
                        "source": deepcopy(layer_defs[layer_id].get("source") or {}),
                        "units": layer_defs[layer_id].get("units"),
                        "draw": deepcopy(draw),
                    }
                )
                if not dry_run and axis is not None and figure is not None:
                    _render_layer(axis, figure, field, draw=draw, units=units)

            payload = _artifact_payload(
                figure_spec,
                output_path=output_path,
                sidecar_path=sidecar_path,
                selected_frames=group,
                current_frame=current_frame,
                title=title,
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
            dry_run=bool(args.dry_run),
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
