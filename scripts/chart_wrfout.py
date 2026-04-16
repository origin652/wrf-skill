from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover
    matplotlib = None
    plt = None

try:
    from plot_wrfout import (
        FigureEvaluator,
        ensure_plotting_dependencies,
        posix_path,
        resolve_layer_dependencies,
        _group_frames_by_domain,
        _sanitize_token,
        _time_token,
        _write_json,
    )
except ImportError:  # pragma: no cover
    from .plot_wrfout import (
        FigureEvaluator,
        ensure_plotting_dependencies,
        posix_path,
        resolve_layer_dependencies,
        _group_frames_by_domain,
        _sanitize_token,
        _time_token,
        _write_json,
    )


def _output_root(base_output_dir: Path, output_cfg: dict[str, Any]) -> Path:
    subdir = str(output_cfg.get("subdir") or "").strip()
    return base_output_dir / subdir if subdir else base_output_dir


def _build_output_path(
    base_output_dir: Path,
    chart_spec: dict[str, Any],
    suffix_tokens: list[str],
    *,
    allow_exact_path: bool = False,
) -> Path:
    output_cfg = chart_spec.get("output", {})
    render_cfg = chart_spec.get("render", {})
    exact_path = output_cfg.get("path")
    if allow_exact_path and exact_path:
        return Path(str(exact_path))

    output_dir = _output_root(base_output_dir, output_cfg)
    stem = str(output_cfg.get("file_stem") or chart_spec["chart_id"])
    pieces = [_sanitize_token(stem)]
    pieces.extend(_sanitize_token(token) for token in suffix_tokens if token)
    suffix = str(render_cfg.get("format") or "png").lower()
    return output_dir / ("__".join(pieces) + f".{suffix}")


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


def _source_files(frames: list[dict[str, Any]]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for frame in frames:
        token = posix_path(frame["path"])
        if token in seen:
            continue
        seen.add(token)
        resolved.append(token)
    return resolved


def _flatten_finite(values: Any) -> np.ndarray:
    array = np.asarray(np.ma.filled(values, np.nan), dtype=float).reshape(-1)
    return array[np.isfinite(array)]


def _reduce_samples(values: Any, mode: str) -> float | None:
    finite = _flatten_finite(values)
    if finite.size == 0:
        return None
    if mode == "mean":
        return float(finite.mean())
    if mode == "min":
        return float(finite.min())
    if mode == "max":
        return float(finite.max())
    if mode == "sum":
        return float(finite.sum())
    raise ValueError(f"Unsupported chart reduce.mode: {mode}")


def _box_stats(values: Any) -> dict[str, float | int | None]:
    finite = _flatten_finite(values)
    if finite.size == 0:
        return {
            "count": 0,
            "min": None,
            "q1": None,
            "median": None,
            "q3": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": int(finite.size),
        "min": float(finite.min()),
        "q1": float(np.percentile(finite, 25.0)),
        "median": float(np.percentile(finite, 50.0)),
        "q3": float(np.percentile(finite, 75.0)),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
    }


def _slice_values_for_region(
    values: np.ndarray,
    dims: tuple[str, ...],
    region_def: dict[str, Any] | None,
    *,
    region_id: str | None,
    layer_id: str,
) -> np.ndarray:
    if not region_def:
        return np.asarray(values, dtype=float)

    sliced = np.asarray(values, dtype=float)
    active_dims = list(dims)
    selectors = {key: value for key, value in region_def.items() if key != "label"}
    for dim, selector in selectors.items():
        if dim not in active_dims:
            raise ValueError(
                f"region_defs.{region_id or 'anonymous'} selects dim={dim} "
                f"but layer_defs.{layer_id} only exposes dims {dims}"
            )
        if not isinstance(selector, dict) or str(selector.get("mode") or "") != "index_range":
            raise ValueError(f"region_defs.{region_id or 'anonymous'}.{dim} must use mode=index_range")
        start = int(selector["start"])
        stop = int(selector["stop"])
        axis = active_dims.index(dim)
        axis_size = int(sliced.shape[axis])
        if start < 0 or stop > axis_size or start >= stop:
            raise ValueError(
                f"region_defs.{region_id or 'anonymous'}.{dim} range [{start}, {stop}) "
                f"is invalid for axis size {axis_size}"
            )
        item = [slice(None)] * sliced.ndim
        item[axis] = slice(start, stop)
        sliced = sliced[tuple(item)]
    return np.asarray(sliced, dtype=float)


def _time_categories(frames: list[dict[str, Any]]) -> list[dict[str, str]]:
    categories: list[dict[str, str]] = []
    for index, frame in enumerate(frames):
        valid_time = str(frame.get("valid_time") or f"time_{index}")
        categories.append({"key": valid_time, "label": valid_time})
    return categories


def _group_categories(x_cfg: dict[str, Any], region_defs: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    categories: list[dict[str, str]] = []
    for region_id in x_cfg.get("group_ids", []):
        region = region_defs[str(region_id)]
        categories.append(
            {
                "key": str(region_id),
                "label": str(region.get("label") or region_id),
            }
        )
    return categories


def _chart_suffix_tokens(chart_spec: dict[str, Any], frames: list[dict[str, Any]]) -> list[str]:
    domain = frames[-1].get("domain") or ""
    first_time = _time_token(frames[0].get("valid_time"))
    last_time = _time_token(frames[-1].get("valid_time"))
    if str(chart_spec.get("chart_kind") or "") == "bar":
        return [str(domain), last_time or first_time or ""]
    if first_time and last_time and first_time != last_time:
        return [str(domain), _sanitize_token(f"{first_time}-to-{last_time}")]
    return [str(domain), last_time or first_time or ""]


def _compose_chart_title(chart_spec: dict[str, Any], frames: list[dict[str, Any]]) -> str:
    base = str(chart_spec.get("render", {}).get("title") or chart_spec["chart_id"])
    details: list[str] = []
    domain = frames[-1].get("domain")
    if domain:
        details.append(str(domain))
    first_time = frames[0].get("valid_time")
    last_time = frames[-1].get("valid_time")
    if str(chart_spec.get("chart_kind") or "") == "bar":
        if last_time:
            details.append(str(last_time))
    elif first_time and last_time:
        details.append(str(last_time) if first_time == last_time else f"{first_time} -> {last_time}")
    suffix = " | ".join(details)
    return f"{base} | {suffix}" if suffix else base


def _series_units(series_payloads: list[dict[str, Any]]) -> str | None:
    units = {item.get("units") for item in series_payloads if item.get("units")}
    if len(units) == 1:
        return str(next(iter(units)))
    return None


def _compute_line_payload(
    chart_spec: dict[str, Any],
    evaluator: FigureEvaluator,
    frames: list[dict[str, Any]],
    region_defs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    categories = _time_categories(frames)
    series_payloads: list[dict[str, Any]] = []
    for series_spec in chart_spec.get("series", []):
        layer_id = str(series_spec["layer_id"])
        region_id_raw = series_spec.get("region_id")
        region_id = str(region_id_raw) if isinstance(region_id_raw, str) and region_id_raw.strip() else None
        region_def = region_defs.get(region_id or "", {})
        reduce_mode = str(series_spec.get("reduce", {}).get("mode") or "mean")
        cube = evaluator.build_time_cube(layer_id)
        values_payload: list[dict[str, Any]] = []
        for index, category in enumerate(categories):
            reduced = _reduce_samples(
                _slice_values_for_region(
                    cube.values[index],
                    cube.dims[1:],
                    region_def,
                    region_id=region_id,
                    layer_id=layer_id,
                ),
                reduce_mode,
            )
            values_payload.append(
                {
                    "category_key": category["key"],
                    "value": reduced,
                }
            )
        series_payloads.append(
            {
                "series_id": str(series_spec["series_id"]),
                "label": str(series_spec["label"]),
                "layer_id": layer_id,
                "region_id": region_id,
                "reduce": deepcopy(series_spec.get("reduce") or {}),
                "units": cube.units,
                "values": values_payload,
            }
        )
    return categories, series_payloads


def _compute_bar_payload(
    chart_spec: dict[str, Any],
    evaluator: FigureEvaluator,
    frames: list[dict[str, Any]],
    region_defs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    categories = _group_categories(chart_spec.get("x", {}), region_defs)
    target_frame = frames[-1]
    series_payloads: list[dict[str, Any]] = []
    for series_spec in chart_spec.get("series", []):
        layer_id = str(series_spec["layer_id"])
        reduce_mode = str(series_spec.get("reduce", {}).get("mode") or "mean")
        cube = evaluator.evaluate_layer_cube(layer_id, target_frame)
        values_payload: list[dict[str, Any]] = []
        for category in categories:
            region_id = category["key"]
            reduced = _reduce_samples(
                _slice_values_for_region(
                    cube.values,
                    cube.dims,
                    region_defs.get(region_id, {}),
                    region_id=region_id,
                    layer_id=layer_id,
                ),
                reduce_mode,
            )
            values_payload.append(
                {
                    "category_key": category["key"],
                    "value": reduced,
                }
            )
        series_payloads.append(
            {
                "series_id": str(series_spec["series_id"]),
                "label": str(series_spec["label"]),
                "layer_id": layer_id,
                "reduce": deepcopy(series_spec.get("reduce") or {}),
                "units": cube.units,
                "values": values_payload,
            }
        )
    return categories, series_payloads


def _compute_boxplot_time_payload(
    chart_spec: dict[str, Any],
    evaluator: FigureEvaluator,
    frames: list[dict[str, Any]],
    region_defs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    categories = _time_categories(frames)
    series_payloads: list[dict[str, Any]] = []
    for series_spec in chart_spec.get("series", []):
        layer_id = str(series_spec["layer_id"])
        region_id_raw = series_spec.get("region_id")
        region_id = str(region_id_raw) if isinstance(region_id_raw, str) and region_id_raw.strip() else None
        region_def = region_defs.get(region_id or "", {})
        cube = evaluator.build_time_cube(layer_id)
        boxes: list[dict[str, Any]] = []
        for index, category in enumerate(categories):
            samples = _slice_values_for_region(
                cube.values[index],
                cube.dims[1:],
                region_def,
                region_id=region_id,
                layer_id=layer_id,
            )
            boxes.append(
                {
                    "category_key": category["key"],
                    **_box_stats(samples),
                }
            )
        series_payloads.append(
            {
                "series_id": str(series_spec["series_id"]),
                "label": str(series_spec["label"]),
                "layer_id": layer_id,
                "region_id": region_id,
                "units": cube.units,
                "boxes": boxes,
            }
        )
    return categories, series_payloads


def _compute_boxplot_group_payload(
    chart_spec: dict[str, Any],
    evaluator: FigureEvaluator,
    frames: list[dict[str, Any]],
    region_defs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    categories = _group_categories(chart_spec.get("x", {}), region_defs)
    series_payloads: list[dict[str, Any]] = []
    for series_spec in chart_spec.get("series", []):
        layer_id = str(series_spec["layer_id"])
        reduce_mode = str(series_spec.get("reduce", {}).get("mode") or "mean")
        cube = evaluator.build_time_cube(layer_id)
        boxes: list[dict[str, Any]] = []
        for category in categories:
            region_id = category["key"]
            reduced_values: list[float] = []
            for index in range(len(frames)):
                reduced = _reduce_samples(
                    _slice_values_for_region(
                        cube.values[index],
                        cube.dims[1:],
                        region_defs.get(region_id, {}),
                        region_id=region_id,
                        layer_id=layer_id,
                    ),
                    reduce_mode,
                )
                if reduced is not None:
                    reduced_values.append(float(reduced))
            boxes.append(
                {
                    "category_key": category["key"],
                    **_box_stats(np.asarray(reduced_values, dtype=float)),
                }
            )
        series_payloads.append(
            {
                "series_id": str(series_spec["series_id"]),
                "label": str(series_spec["label"]),
                "layer_id": layer_id,
                "reduce": deepcopy(series_spec.get("reduce") or {}),
                "units": cube.units,
                "boxes": boxes,
            }
        )
    return categories, series_payloads


def _compute_chart_payload(
    chart_spec: dict[str, Any],
    evaluator: FigureEvaluator,
    frames: list[dict[str, Any]],
    region_defs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    chart_kind = str(chart_spec.get("chart_kind") or "")
    x_mode = str(chart_spec.get("x", {}).get("mode") or "")
    if chart_kind == "line":
        return _compute_line_payload(chart_spec, evaluator, frames, region_defs)
    if chart_kind == "bar":
        return _compute_bar_payload(chart_spec, evaluator, frames, region_defs)
    if chart_kind == "boxplot" and x_mode == "time":
        return _compute_boxplot_time_payload(chart_spec, evaluator, frames, region_defs)
    if chart_kind == "boxplot" and x_mode == "group":
        return _compute_boxplot_group_payload(chart_spec, evaluator, frames, region_defs)
    raise ValueError(f"Unsupported chart request: kind={chart_kind} x.mode={x_mode}")


def _render_line_chart(axis: Any, categories: list[dict[str, str]], series_payloads: list[dict[str, Any]]) -> None:
    positions = np.arange(len(categories), dtype=float)
    for series in series_payloads:
        values = [item.get("value") for item in series.get("values", [])]
        y_values = np.array([np.nan if value is None else float(value) for value in values], dtype=float)
        axis.plot(positions, y_values, marker="o", linewidth=1.8, label=series["label"])
    axis.set_xticks(positions, [item["label"] for item in categories], rotation=30, ha="right")


def _render_bar_chart(axis: Any, categories: list[dict[str, str]], series_payloads: list[dict[str, Any]]) -> None:
    base_positions = np.arange(len(categories), dtype=float)
    width = 0.8 / max(len(series_payloads), 1)
    offset_origin = -0.5 * width * (len(series_payloads) - 1)
    for index, series in enumerate(series_payloads):
        values = [item.get("value") for item in series.get("values", [])]
        y_values = np.array([0.0 if value is None else float(value) for value in values], dtype=float)
        positions = base_positions + offset_origin + index * width
        axis.bar(positions, y_values, width=width, label=series["label"])
    axis.set_xticks(base_positions, [item["label"] for item in categories])


def _render_boxplot_chart(axis: Any, categories: list[dict[str, str]], series_payloads: list[dict[str, Any]]) -> None:
    base_positions = np.arange(1, len(categories) + 1, dtype=float)
    width = 0.7 / max(len(series_payloads), 1)
    offset_origin = -0.5 * width * (len(series_payloads) - 1)
    for index, series in enumerate(series_payloads):
        boxes = series.get("boxes", [])
        stats_list = [
            {
                "label": categories[item_index]["label"],
                "med": box["median"],
                "q1": box["q1"],
                "q3": box["q3"],
                "whislo": box["min"],
                "whishi": box["max"],
                "mean": box["mean"],
                "fliers": [],
            }
            for item_index, box in enumerate(boxes)
            if int(box.get("count") or 0) > 0
        ]
        positions = [
            base_positions[item_index] + offset_origin + index * width
            for item_index, box in enumerate(boxes)
            if int(box.get("count") or 0) > 0
        ]
        if not stats_list:
            continue
        result = axis.bxp(
            stats_list,
            positions=positions,
            widths=width * 0.9,
            patch_artist=True,
            showmeans=True,
        )
        for patch in result["boxes"]:
            patch.set_alpha(0.55)
        result["boxes"][0].set_label(series["label"])
    axis.set_xticks(base_positions, [item["label"] for item in categories], rotation=30, ha="right")


def _render_chart(
    chart_spec: dict[str, Any],
    output_path: Path,
    title: str,
    categories: list[dict[str, str]],
    series_payloads: list[dict[str, Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(7, 4.5), constrained_layout=True)
    try:
        chart_kind = str(chart_spec.get("chart_kind") or "")
        if chart_kind == "line":
            _render_line_chart(axis, categories, series_payloads)
        elif chart_kind == "bar":
            _render_bar_chart(axis, categories, series_payloads)
        elif chart_kind == "boxplot":
            _render_boxplot_chart(axis, categories, series_payloads)
        else:
            raise ValueError(f"Unsupported chart kind: {chart_kind}")

        x_label = chart_spec.get("x", {}).get("label")
        if isinstance(x_label, str) and x_label.strip():
            axis.set_xlabel(x_label)
        units = _series_units(series_payloads)
        if units:
            axis.set_ylabel(units)
        axis.set_title(title)
        if series_payloads:
            axis.legend()
        axis.grid(True, axis="y", alpha=0.25)
        figure.savefig(output_path, dpi=int(chart_spec.get("render", {}).get("dpi") or 150))
    finally:
        plt.close(figure)


def _artifact_payload(
    chart_spec: dict[str, Any],
    *,
    output_path: Path,
    sidecar_path: Path | None,
    selected_frames: list[dict[str, Any]],
    title: str,
    categories: list[dict[str, str]],
    series_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "chart_id": chart_spec["chart_id"],
        "chart_kind": str(chart_spec.get("chart_kind") or ""),
        "path": posix_path(output_path),
        "sidecar_path": None if sidecar_path is None else posix_path(sidecar_path),
        "format": str(chart_spec.get("render", {}).get("format") or "png").lower(),
        "title": title,
        "domain": selected_frames[-1].get("domain"),
        "selected_frames": _serialize_frames(selected_frames),
        "source_files": _source_files(selected_frames),
        "categories": deepcopy(categories),
        "series": deepcopy(series_payloads),
    }


def run_chart_request(
    chart_spec: dict[str, Any],
    layer_defs: dict[str, dict[str, Any]],
    frames: list[dict[str, Any]],
    base_output_dir: Path | str,
    *,
    region_defs: dict[str, dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    ensure_plotting_dependencies()
    if not frames:
        raise ValueError("No frames selected for chart rendering")

    resolved_region_defs = region_defs or {}
    root_layer_ids: list[str] = []
    for series_spec in chart_spec.get("series", []):
        layer_id = str(series_spec.get("layer_id") or "")
        if layer_id and layer_id not in root_layer_ids:
            root_layer_ids.append(layer_id)
    parsed_defs, _ = resolve_layer_dependencies(layer_defs, root_layer_ids)

    artifacts: list[dict[str, Any]] = []
    grouped_frames = _group_frames_by_domain(frames)
    allow_exact_path = len(grouped_frames) == 1
    output_cfg = chart_spec.get("output", {})
    overwrite = bool(output_cfg.get("overwrite", False))
    sidecar_enabled = bool(output_cfg.get("sidecar_json", True))
    for _, group in grouped_frames:
        if not group:
            continue
        evaluator = FigureEvaluator(layer_defs, parsed_defs, group)
        output_path = _build_output_path(
            Path(base_output_dir),
            chart_spec,
            _chart_suffix_tokens(chart_spec, group),
            allow_exact_path=allow_exact_path,
        )
        sidecar_path = output_path.with_suffix(".json") if sidecar_enabled else None
        if output_path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing plot: {output_path}")
        if sidecar_path is not None and sidecar_path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing sidecar: {sidecar_path}")

        title = _compose_chart_title(chart_spec, group)
        categories, series_payloads = _compute_chart_payload(chart_spec, evaluator, group, resolved_region_defs)
        payload = _artifact_payload(
            chart_spec,
            output_path=output_path,
            sidecar_path=sidecar_path,
            selected_frames=group,
            title=title,
            categories=categories,
            series_payloads=series_payloads,
        )
        if not dry_run:
            _render_chart(chart_spec, output_path, title, categories, series_payloads)
            if sidecar_path is not None:
                _write_json(sidecar_path, payload)
        artifacts.append(payload)
    return artifacts
