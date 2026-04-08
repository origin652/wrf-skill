from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
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
    from netCDF4 import Dataset
except ImportError:  # pragma: no cover
    Dataset = None

TIME_FORMAT = "%Y-%m-%d_%H:%M:%S"
SUPPORTED_PRODUCTS = {
    "accumulated_precipitation",
    "t2",
    "wind10m",
    "h500",
    "storm_track",
}
IMPLEMENTED_PRODUCTS = {
    "accumulated_precipitation",
    "t2",
    "wind10m",
}
DEFAULT_COLORMAPS = {
    "accumulated_precipitation": "Blues",
    "t2": "coolwarm",
    "wind10m": "viridis",
}
DOMAIN_PATTERN = re.compile(r"(d\d{2})")


class PlottingDependencyError(RuntimeError):
    pass


class ProductNotImplementedError(NotImplementedError):
    pass


def posix_path(path: Path | str) -> str:
    return Path(path).as_posix()


def ensure_plotting_dependencies() -> None:
    missing: list[str] = []
    if Dataset is None:
        missing.append("netCDF4")
    if plt is None:
        missing.append("matplotlib")
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
    product_spec: dict[str, Any],
    suffix_tokens: list[str],
    *,
    allow_exact_path: bool = False,
) -> Path:
    output_cfg = product_spec.get("output", {})
    render_cfg = product_spec.get("render", {})
    exact_path = output_cfg.get("path")
    if allow_exact_path and exact_path:
        return Path(str(exact_path))

    output_dir = _output_root(base_output_dir, output_cfg)
    stem = str(output_cfg.get("file_stem") or product_spec["product"])
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


def _render_field_png(
    field: np.ndarray,
    *,
    output_path: Path,
    title: str,
    colormap: str,
    dpi: int,
    units: str | None,
) -> None:
    ensure_plotting_dependencies()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(6, 5), constrained_layout=True)
    image = axis.imshow(field, origin="lower", cmap=colormap)
    axis.set_title(title)
    axis.set_xlabel("west_east")
    axis.set_ylabel("south_north")
    colorbar = figure.colorbar(image, ax=axis, shrink=0.9)
    if units:
        colorbar.set_label(units)
    figure.savefig(output_path, dpi=dpi)
    plt.close(figure)


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


def _serialize_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for frame in frames:
        payload.append(
            {
                "path": posix_path(frame["path"]),
                "domain": frame.get("domain"),
                "time_index": int(frame["time_index"]),
                "global_index": int(frame["global_index"]),
                "valid_time": frame.get("valid_time"),
            }
        )
    return payload


def _compose_title(product_spec: dict[str, Any], frames: list[dict[str, Any]]) -> str:
    render_cfg = product_spec.get("render", {})
    base = render_cfg.get("title") or product_spec.get("label") or product_spec["product"]
    first_frame = frames[0]
    last_frame = frames[-1]
    details: list[str] = []
    if last_frame.get("domain"):
        details.append(str(last_frame["domain"]))

    first_time = first_frame.get("valid_time")
    last_time = last_frame.get("valid_time")
    if first_time and last_time:
        if first_time == last_time:
            details.append(str(last_time))
        else:
            details.append(f"{first_time} -> {last_time}")
    elif last_time:
        details.append(str(last_time))

    suffix = " | ".join(details)
    return f"{base} | {suffix}" if suffix else str(base)


def _artifact_payload(
    product_spec: dict[str, Any],
    *,
    output_path: Path,
    sidecar_path: Path | None,
    frames: list[dict[str, Any]],
    field: np.ndarray | None,
    units: str | None,
    title: str,
) -> dict[str, Any]:
    source_files: list[str] = []
    for frame in frames:
        source = posix_path(frame["path"])
        if source not in source_files:
            source_files.append(source)

    return {
        "product": product_spec["product"],
        "path": posix_path(output_path),
        "sidecar_path": None if sidecar_path is None else posix_path(sidecar_path),
        "format": str(product_spec.get("render", {}).get("format") or "png").lower(),
        "title": title,
        "domain": frames[-1].get("domain"),
        "units": units,
        "source_files": source_files,
        "selected_frames": _serialize_frames(frames),
        "summary": None if field is None else _summary(field),
    }


def _write_artifact(
    product_spec: dict[str, Any],
    *,
    output_path: Path,
    frames: list[dict[str, Any]],
    field: np.ndarray,
    units: str | None,
    title: str,
    colormap: str,
    dpi: int,
    dry_run: bool,
) -> dict[str, Any]:
    output_cfg = product_spec.get("output", {})
    overwrite = bool(output_cfg.get("overwrite", False))
    sidecar_enabled = bool(output_cfg.get("sidecar_json", True))
    sidecar_path = output_path.with_suffix(".json") if sidecar_enabled else None

    payload = _artifact_payload(
        product_spec,
        output_path=output_path,
        sidecar_path=sidecar_path,
        frames=frames,
        field=field,
        units=units,
        title=title,
    )
    if dry_run:
        return payload

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing plot: {output_path}")
    if sidecar_path is not None and sidecar_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing sidecar: {sidecar_path}")

    _render_field_png(
        field,
        output_path=output_path,
        title=title,
        colormap=colormap,
        dpi=dpi,
        units=units,
    )
    if sidecar_path is not None:
        _write_json(sidecar_path, payload)
    return payload


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


def _instantaneous_requests(
    frames: list[dict[str, Any]],
    selectors: dict[str, Any] | None,
) -> list[list[dict[str, Any]]]:
    if has_explicit_time_selection(selectors):
        return [[frame] for frame in frames]
    return [[group[-1]] for _, group in _group_frames_by_domain(frames) if group]


def _prepare_t2(
    field: np.ndarray,
    units: str | None,
    options: dict[str, Any],
) -> tuple[np.ndarray, str | None]:
    target_units = str(options.get("units") or "celsius").lower()
    if target_units in {"k", "kelvin"}:
        return field, "K" if units and units.lower().startswith("k") else units
    if target_units in {"c", "celsius", "degc", "degrees_celsius"}:
        if units and units.lower().startswith("k"):
            return field - 273.15, "C"
        return field, "C"
    return field, units


def _run_t2(
    product_spec: dict[str, Any],
    frames: list[dict[str, Any]],
    *,
    base_output_dir: Path,
    dry_run: bool,
) -> list[dict[str, Any]]:
    if not frames:
        raise ValueError("No frames selected for t2")

    render_cfg = product_spec.get("render", {})
    requests = _instantaneous_requests(frames, product_spec.get("selectors"))

    artifacts: list[dict[str, Any]] = []
    for request in requests:
        frame = request[-1]
        with Dataset(frame["path"]) as dataset:
            field, units = _load_2d_var(dataset, "T2", int(frame["time_index"]))
        field, units = _prepare_t2(field, units, product_spec.get("options", {}))
        title = _compose_title(product_spec, request)
        output_path = _build_output_path(
            base_output_dir,
            product_spec,
            [frame.get("domain") or "", _time_token(frame.get("valid_time")) or ""],
            allow_exact_path=len(requests) == 1,
        )
        artifacts.append(
            _write_artifact(
                product_spec,
                output_path=output_path,
                frames=request,
                field=field,
                units=units,
                title=title,
                colormap=str(render_cfg.get("colormap") or DEFAULT_COLORMAPS["t2"]),
                dpi=int(render_cfg.get("dpi") or 150),
                dry_run=dry_run,
            )
        )
    return artifacts


def _run_wind10m(
    product_spec: dict[str, Any],
    frames: list[dict[str, Any]],
    *,
    base_output_dir: Path,
    dry_run: bool,
) -> list[dict[str, Any]]:
    if not frames:
        raise ValueError("No frames selected for wind10m")

    render_cfg = product_spec.get("render", {})
    requests = _instantaneous_requests(frames, product_spec.get("selectors"))

    artifacts: list[dict[str, Any]] = []
    for request in requests:
        frame = request[-1]
        with Dataset(frame["path"]) as dataset:
            u10, units = _load_2d_var(dataset, "U10", int(frame["time_index"]))
            v10, _ = _load_2d_var(dataset, "V10", int(frame["time_index"]))
        field = np.sqrt(np.square(u10) + np.square(v10))
        title = _compose_title(product_spec, request)
        output_path = _build_output_path(
            base_output_dir,
            product_spec,
            [frame.get("domain") or "", _time_token(frame.get("valid_time")) or ""],
            allow_exact_path=len(requests) == 1,
        )
        artifacts.append(
            _write_artifact(
                product_spec,
                output_path=output_path,
                frames=request,
                field=field,
                units=units,
                title=title,
                colormap=str(
                    render_cfg.get("colormap") or DEFAULT_COLORMAPS["wind10m"]
                ),
                dpi=int(render_cfg.get("dpi") or 150),
                dry_run=dry_run,
            )
        )
    return artifacts


def _precip_total(frame: dict[str, Any]) -> np.ndarray:
    with Dataset(frame["path"]) as dataset:
        rainc, _ = _load_2d_var(dataset, "RAINC", int(frame["time_index"]))
        rainnc, _ = _load_2d_var(dataset, "RAINNC", int(frame["time_index"]))
    return rainc + rainnc


def _run_accumulated_precipitation(
    product_spec: dict[str, Any],
    frames: list[dict[str, Any]],
    *,
    base_output_dir: Path,
    dry_run: bool,
) -> list[dict[str, Any]]:
    if not frames:
        raise ValueError("No frames selected for accumulated_precipitation")

    render_cfg = product_spec.get("render", {})
    artifacts: list[dict[str, Any]] = []
    for _, group in _group_frames_by_domain(frames):
        if not group:
            continue
        first_frame = group[0]
        last_frame = group[-1]
        if len(group) == 1:
            field = _precip_total(last_frame)
        else:
            field = _precip_total(last_frame) - _precip_total(first_frame)

        first_time = _time_token(first_frame.get("valid_time"))
        last_time = _time_token(last_frame.get("valid_time"))
        if first_time and last_time and first_time != last_time:
            time_token = _sanitize_token(f"{first_time}-to-{last_time}")
        else:
            time_token = last_time or first_time or ""

        output_path = _build_output_path(
            base_output_dir,
            product_spec,
            [last_frame.get("domain") or "", time_token],
            allow_exact_path=len(frames) == len(group) and len(group) == 1,
        )
        artifacts.append(
            _write_artifact(
                product_spec,
                output_path=output_path,
                frames=group,
                field=field,
                units="mm",
                title=_compose_title(product_spec, group),
                colormap=str(
                    render_cfg.get("colormap")
                    or DEFAULT_COLORMAPS["accumulated_precipitation"]
                ),
                dpi=int(render_cfg.get("dpi") or 150),
                dry_run=dry_run,
            )
        )
    return artifacts


PRODUCT_HANDLERS = {
    "accumulated_precipitation": _run_accumulated_precipitation,
    "t2": _run_t2,
    "wind10m": _run_wind10m,
}


def run_product_request(
    product_spec: dict[str, Any],
    frames: list[dict[str, Any]],
    base_output_dir: Path | str,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    product_name = str(product_spec.get("product") or "").strip()
    if product_name not in SUPPORTED_PRODUCTS:
        raise ValueError(f"Unsupported product: {product_name}")

    handler = PRODUCT_HANDLERS.get(product_name)
    if handler is None:
        raise ProductNotImplementedError(
            f"Product is recognized but not implemented yet: {product_name}"
        )

    render_format = str(product_spec.get("render", {}).get("format") or "png").lower()
    if render_format != "png":
        raise ProductNotImplementedError(
            f"Phase 1 supports only PNG rendering, received: {render_format}"
        )

    return handler(product_spec, frames, base_output_dir=Path(base_output_dir), dry_run=dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a single WRF post-processing product."
    )
    parser.add_argument("--wrfout", required=True)
    parser.add_argument("--product", required=True, choices=sorted(SUPPORTED_PRODUCTS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--time-index", type=int, default=0)
    parser.add_argument("--title")
    parser.add_argument("--colormap")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    wrfout_path = Path(args.wrfout)
    if not wrfout_path.exists():
        raise SystemExit(f"Missing wrfout file: {wrfout_path}")

    output_path = Path(args.out)
    product_spec = {
        "product": args.product,
        "label": None,
        "inputs": {
            "mode": "explicit_paths",
            "paths": [posix_path(wrfout_path)],
        },
        "selectors": {
            "domain": None,
            "time_indices": [int(args.time_index)],
            "time_range": {
                "start": None,
                "end": None,
            },
            "max_files": None,
        },
        "render": {
            "format": (output_path.suffix.lstrip(".") or "png").lower(),
            "title": args.title,
            "colormap": args.colormap,
            "dpi": int(args.dpi),
        },
        "output": {
            "subdir": "",
            "file_stem": output_path.stem,
            "sidecar_json": True,
            "overwrite": True,
            "path": posix_path(output_path),
        },
        "options": {},
    }

    frames = enumerate_wrfout_frames([wrfout_path])
    selected_frames = select_wrfout_frames(frames, product_spec["selectors"])
    if not selected_frames:
        raise SystemExit(
            f"No matching timesteps for {wrfout_path} at time_index={args.time_index}"
        )

    payload = {
        "dry_run": bool(args.dry_run),
        "artifacts": run_product_request(
            product_spec,
            selected_frames,
            output_path.parent,
            dry_run=bool(args.dry_run),
        ),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
