import json
import shutil
import subprocess
import sys
import unittest
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from netCDF4 import Dataset

from scripts.chart_wrfout import run_chart_request
from scripts.plot_wrfout import (
    BinaryOpNode,
    CallNode,
    LayerResolutionError,
    NameNode,
    NumberNode,
    _build_field_cube,
    _reduce_cube_for_view,
    enumerate_wrfout_frames,
    layer_uses_current,
    parse_formula,
    resolve_layer_dependencies,
    run_figure_request,
    select_wrfout_frames,
)
from scripts.post_spec import default_post_spec
from scripts.project_state import create_project_state, save_project
from scripts.wrf_post import run_postprocess

REPO_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = REPO_ROOT / "runs"
PLOT_SCRIPT = REPO_ROOT / "scripts" / "plot_wrfout.py"
REAL_PROJECT_JSON = REPO_ROOT / "runs" / "mini_gfs_real_20240720" / "project.json"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_repo_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def pcolormesh_warning_messages(caught: list) -> list[str]:
    return [
        str(item.message)
        for item in caught
        if "pcolormesh" in str(item.message).lower()
    ]


def _write_times(dataset: Dataset, times: list[str]) -> None:
    times_var = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
    encoded = np.empty((len(times), 19), dtype="S1")
    for index, token in enumerate(times):
        encoded[index] = np.array(list(token), dtype="S1")
    times_var[:, :] = encoded


def write_wrfout_netcdf(
    path: Path,
    *,
    times: list[str],
    t2: list[np.ndarray],
    u10: list[np.ndarray],
    v10: list[np.ndarray],
    rainc: list[np.ndarray],
    rainnc: list[np.ndarray],
    hgt: np.ndarray | None = None,
    landmask: np.ndarray | None = None,
    lat: np.ndarray | None = None,
    lon: np.ndarray | None = None,
    extra_2d_fields: dict[str, tuple[list[np.ndarray], str | None]] | None = None,
    extra_3d_fields: dict[str, tuple[list[np.ndarray], str | None]] | None = None,
    extra_stag_3d_fields: dict[str, tuple[list[np.ndarray], str | None]] | None = None,
    extra_dim_3d_fields: dict[str, tuple[list[np.ndarray], tuple[str, str, str], str | None]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = np.asarray(t2[0], dtype=float)
    terrain = np.asarray(hgt if hgt is not None else np.zeros_like(sample), dtype=float)
    mask = np.asarray(landmask if landmask is not None else np.ones_like(sample), dtype=float)
    latitude = np.asarray(lat if lat is not None else np.zeros_like(sample), dtype=float)
    longitude = np.asarray(lon if lon is not None else np.zeros_like(sample), dtype=float)
    field_2d_defs = extra_2d_fields or {}
    field_3d_defs = extra_3d_fields or {}
    field_stag_3d_defs = extra_stag_3d_fields or {}
    field_dim_3d_defs = extra_dim_3d_fields or {}

    with Dataset(path, "w", format="NETCDF4") as dataset:
        dataset.createDimension("Time", len(times))
        dataset.createDimension("south_north", sample.shape[0])
        dataset.createDimension("west_east", sample.shape[1])
        dataset.createDimension("DateStrLen", 19)
        if field_3d_defs:
            level_count = None
            for values, _ in field_3d_defs.values():
                shape = np.asarray(values[0], dtype=float).shape
                if len(shape) != 3:
                    raise ValueError("extra_3d_fields values must have shape (levels, y, x)")
                if level_count is None:
                    level_count = int(shape[0])
                elif level_count != int(shape[0]):
                    raise ValueError("extra_3d_fields must share the same vertical level count")
            dataset.createDimension("bottom_top", int(level_count or 0))
        if field_stag_3d_defs:
            stag_level_count = None
            for values, _ in field_stag_3d_defs.values():
                shape = np.asarray(values[0], dtype=float).shape
                if len(shape) != 3:
                    raise ValueError("extra_stag_3d_fields values must have shape (levels, y, x)")
                if stag_level_count is None:
                    stag_level_count = int(shape[0])
                elif stag_level_count != int(shape[0]):
                    raise ValueError("extra_stag_3d_fields must share the same vertical level count")
            dataset.createDimension("bottom_top_stag", int(stag_level_count or 0))
        if field_dim_3d_defs:
            dim_sizes: dict[str, int] = {}
            for values, dims, _ in field_dim_3d_defs.values():
                shape = np.asarray(values[0], dtype=float).shape
                if len(dims) != 3:
                    raise ValueError("extra_dim_3d_fields dims must have length 3")
                if len(shape) != 3:
                    raise ValueError("extra_dim_3d_fields values must have shape (d0, d1, d2)")
                for dim_name, size in zip(dims, shape):
                    existing_size = dim_sizes.get(dim_name)
                    if existing_size is None:
                        dim_sizes[dim_name] = int(size)
                    elif existing_size != int(size):
                        raise ValueError("extra_dim_3d_fields dimensions must use consistent sizes")
            for dim_name, size in dim_sizes.items():
                existing_dim = dataset.dimensions.get(dim_name)
                if existing_dim is None:
                    dataset.createDimension(dim_name, size)
                elif len(existing_dim) != size:
                    raise ValueError(f"Dimension {dim_name} expected size {size}, found {len(existing_dim)}")
        _write_times(dataset, times)

        def write_field(name: str, values: list[np.ndarray], units: str) -> None:
            variable = dataset.createVariable(
                name,
                "f4",
                ("Time", "south_north", "west_east"),
            )
            variable.units = units
            variable[:, :, :] = np.stack(values, axis=0)

        def write_static_field(name: str, values: np.ndarray, units: str | None) -> None:
            variable = dataset.createVariable(
                name,
                "f4",
                ("south_north", "west_east"),
            )
            if units is not None:
                variable.units = units
            variable[:, :] = values

        def write_field_3d(name: str, values: list[np.ndarray], units: str | None) -> None:
            variable = dataset.createVariable(
                name,
                "f4",
                ("Time", "bottom_top", "south_north", "west_east"),
            )
            if units is not None:
                variable.units = units
            variable[:, :, :, :] = np.stack(values, axis=0)

        def write_field_3d_stag(name: str, values: list[np.ndarray], units: str | None) -> None:
            variable = dataset.createVariable(
                name,
                "f4",
                ("Time", "bottom_top_stag", "south_north", "west_east"),
            )
            if units is not None:
                variable.units = units
            variable[:, :, :, :] = np.stack(values, axis=0)

        def write_field_3d_dims(
            name: str,
            values: list[np.ndarray],
            dims: tuple[str, str, str],
            units: str | None,
        ) -> None:
            variable = dataset.createVariable(
                name,
                "f4",
                ("Time", *dims),
            )
            if units is not None:
                variable.units = units
            variable[:, :, :, :] = np.stack(values, axis=0)

        write_field("T2", t2, "K")
        write_field("U10", u10, "m s-1")
        write_field("V10", v10, "m s-1")
        write_field("RAINC", rainc, "mm")
        write_field("RAINNC", rainnc, "mm")
        write_field("XLAT", [latitude for _ in times], "degrees_north")
        write_field("XLONG", [longitude for _ in times], "degrees_east")
        for field_name, (values, units) in field_2d_defs.items():
            write_field(field_name, values, units or "")
        for field_name, (values, units) in field_3d_defs.items():
            write_field_3d(field_name, values, units)
        for field_name, (values, units) in field_stag_3d_defs.items():
            write_field_3d_stag(field_name, values, units)
        for field_name, (values, dims, units) in field_dim_3d_defs.items():
            write_field_3d_dims(field_name, values, dims, units)
        write_static_field("HGT", terrain, "m")
        write_static_field("LANDMASK", mask, None)


def build_layer_defs() -> dict[str, dict]:
    return {
        "terrain": {
            "source": {"kind": "wrf_native"},
            "expr": "first(HGT)",
            "units": "m",
            "metadata": {"description": "Terrain height"},
        },
        "landmask": {
            "source": {"kind": "wrf_native"},
            "expr": "first(LANDMASK)",
            "units": None,
            "metadata": {"description": "Land-sea mask"},
        },
        "t2_c": {
            "source": {"kind": "wrf_native"},
            "expr": "T2 - 273.15",
            "units": "C",
            "metadata": {"description": "2m temperature in Celsius"},
        },
        "wind10m": {
            "source": {"kind": "wrf_native"},
            "expr": "sqrt(U10**2 + V10**2)",
            "units": "m s-1",
            "metadata": {"description": "10m wind speed"},
        },
        "u10": {
            "source": {"kind": "wrf_native"},
            "expr": "U10",
            "units": "m s-1",
            "metadata": {"description": "10m zonal wind"},
        },
        "v10": {
            "source": {"kind": "wrf_native"},
            "expr": "V10",
            "units": "m s-1",
            "metadata": {"description": "10m meridional wind"},
        },
        "accum_precip": {
            "source": {"kind": "wrf_native"},
            "expr": "last(RAINC + RAINNC) - first(RAINC + RAINNC)",
            "units": "mm",
            "metadata": {"description": "Accumulated precipitation"},
        },
        "qvapor_cube_gkg": {
            "source": {"kind": "wrf_native_3d_full"},
            "expr": "QVAPOR * 1000",
            "units": "g kg-1",
            "metadata": {"description": "Full-column water vapor mixing ratio"},
        },
    }


def build_style_defs() -> dict[str, dict]:
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
            "alpha": 0.8,
            "zorder": 20,
            "style": {
                "levels": [100, 200],
                "colors": "black",
                "linewidths": 0.5,
            },
        },
        "landmask_fill": {
            "kind": "categorical_fill",
            "alpha": 0.5,
            "zorder": 1,
            "style": {
                "categories": [
                    {"value": 0, "color": "#1f77b4", "label": "water"},
                    {"value": 1, "color": "#d8b365", "label": "land"},
                ]
            },
        },
        "precip_raster": {
            "kind": "raster",
            "alpha": 0.9,
            "zorder": 10,
            "style": {
                "colormap": "Blues",
                "show_colorbar": True,
            },
        },
        "wind_quiver": {
            "kind": "vector",
            "alpha": 0.9,
            "zorder": 30,
            "style": {
                "mode": "quiver",
                "stride": 1,
                "scale": 30,
                "color": "black",
                "pivot": "mid",
            },
        },
    }


def create_project(runs_dir: Path, project_name: str, wrfout_paths: list[Path]) -> Path:
    project_root = runs_dir / project_name
    state = create_project_state(project_name, project_root)
    for key in ("output_dir", "log_dir", "wrf_dir"):
        Path(state["paths"][key]).mkdir(parents=True, exist_ok=True)
    state["artifacts"]["wrfout_files"] = [path.resolve().as_posix() for path in wrfout_paths]
    save_project(state, project_root / "project.json")
    return project_root


class FormulaRuntimeTests(unittest.TestCase):
    def test_parse_formula_builds_expected_ast(self) -> None:
        ast = parse_formula("last(RAINC + RAINNC) - first(RAINC + RAINNC)")

        self.assertIsInstance(ast, BinaryOpNode)
        self.assertEqual(ast.op, "-")
        self.assertIsInstance(ast.left, CallNode)
        self.assertEqual(ast.left.name, "last")
        self.assertIsInstance(ast.right, CallNode)
        self.assertEqual(ast.right.name, "first")
        left_inner = ast.left.args[0]
        self.assertIsInstance(left_inner, BinaryOpNode)
        self.assertEqual(left_inner.op, "+")
        self.assertIsInstance(left_inner.left, NameNode)
        self.assertEqual(left_inner.left.name, "RAINC")
        self.assertIsInstance(left_inner.right, NameNode)
        self.assertEqual(left_inner.right.name, "RAINNC")

        power_ast = parse_formula("sqrt(U10**2 + V10**2)")
        self.assertIsInstance(power_ast, CallNode)
        self.assertEqual(power_ast.name, "sqrt")
        inner = power_ast.args[0]
        self.assertIsInstance(inner, BinaryOpNode)
        self.assertEqual(inner.op, "+")
        self.assertIsInstance(inner.left, BinaryOpNode)
        self.assertEqual(inner.left.op, "**")
        self.assertIsInstance(inner.left.right, NumberNode)
        self.assertEqual(inner.left.right.value, 2.0)

    def test_resolve_layer_dependencies_and_current_semantics(self) -> None:
        layer_defs = {
            "terrain": {"expr": "first(HGT)", "source": {"kind": "wrf_native"}, "units": "m", "metadata": {}},
            "t2_c": {"expr": "T2 - 273.15", "source": {"kind": "wrf_native"}, "units": "C", "metadata": {}},
            "capped_mix": {
                "expr": "maximum(t2_c, terrain)",
                "source": {"kind": "wrf_native"},
                "units": "C",
                "metadata": {},
            },
            "accum_precip": {
                "expr": "last(RAINC + RAINNC) - first(RAINC + RAINNC)",
                "source": {"kind": "wrf_native"},
                "units": "mm",
                "metadata": {},
            },
        }

        parsed_defs, order = resolve_layer_dependencies(layer_defs, ["capped_mix", "accum_precip"])

        self.assertEqual(set(order), {"terrain", "t2_c", "capped_mix", "accum_precip"})
        self.assertLess(order.index("terrain"), order.index("capped_mix"))
        self.assertLess(order.index("t2_c"), order.index("capped_mix"))
        self.assertFalse(layer_uses_current("terrain", parsed_defs))
        self.assertTrue(layer_uses_current("t2_c", parsed_defs))
        self.assertTrue(layer_uses_current("capped_mix", parsed_defs))
        self.assertFalse(layer_uses_current("accum_precip", parsed_defs))

    def test_resolve_layer_dependencies_rejects_cycles(self) -> None:
        layer_defs = {
            "a": {"expr": "b", "source": {"kind": "wrf_native"}, "units": None, "metadata": {}},
            "b": {"expr": "a", "source": {"kind": "wrf_native"}, "units": None, "metadata": {}},
        }

        with self.assertRaises(LayerResolutionError):
            resolve_layer_dependencies(layer_defs, ["a"])

    def test_reduce_cube_for_view_supports_richer_selector_modes(self) -> None:
        cube = _build_field_cube(
            np.array(
                [
                    [[1.0, 2.0], [3.0, 4.0]],
                    [[5.0, 6.0], [7.0, 8.0]],
                ],
                dtype=float,
            ),
            dims=("bottom_top", "south_north", "west_east"),
            units="g kg-1",
            coords={
                "bottom_top": np.array([0.0, 10.0], dtype=float),
                "south_north": np.array([0.0, 1.0], dtype=float),
                "west_east": np.array([0.0, 1.0], dtype=float),
            },
            metadata={},
            label="selector_test_cube",
        )

        nearest_index_cube, nearest_index_selected = _reduce_cube_for_view(
            cube,
            keep_dims={"bottom_top", "west_east"},
            selectors={"south_north": {"mode": "nearest_index", "index": 0.8}},
            current_time_index=None,
        )
        np.testing.assert_allclose(
            nearest_index_cube.values,
            np.array([[3.0, 4.0], [7.0, 8.0]], dtype=float),
        )
        self.assertEqual(nearest_index_selected, {"south_north": 1})

        value_cube, value_selected = _reduce_cube_for_view(
            cube,
            keep_dims={"south_north", "west_east"},
            selectors={"bottom_top": {"mode": "value", "value": 10.0}},
            current_time_index=None,
        )
        np.testing.assert_allclose(
            value_cube.values,
            np.array([[5.0, 6.0], [7.0, 8.0]], dtype=float),
        )
        self.assertEqual(value_selected, {"bottom_top": 1})

        nearest_value_cube, nearest_value_selected = _reduce_cube_for_view(
            cube,
            keep_dims={"south_north", "west_east"},
            selectors={"bottom_top": {"mode": "nearest_value", "value": 8.9}},
            current_time_index=None,
        )
        np.testing.assert_allclose(
            nearest_value_cube.values,
            np.array([[5.0, 6.0], [7.0, 8.0]], dtype=float),
        )
        self.assertEqual(nearest_value_selected, {"bottom_top": 1})

        mean_cube, mean_selected = _reduce_cube_for_view(
            cube,
            keep_dims={"bottom_top", "west_east"},
            selectors={"south_north": {"mode": "mean"}},
            current_time_index=None,
        )
        np.testing.assert_allclose(
            mean_cube.values,
            np.array([[2.0, 3.0], [6.0, 7.0]], dtype=float),
        )
        self.assertEqual(mean_selected, {})

        min_cube, _ = _reduce_cube_for_view(
            cube,
            keep_dims={"bottom_top", "west_east"},
            selectors={"south_north": {"mode": "min"}},
            current_time_index=None,
        )
        np.testing.assert_allclose(
            min_cube.values,
            np.array([[1.0, 2.0], [5.0, 6.0]], dtype=float),
        )

        max_cube, _ = _reduce_cube_for_view(
            cube,
            keep_dims={"bottom_top", "west_east"},
            selectors={"south_north": {"mode": "max"}},
            current_time_index=None,
        )
        np.testing.assert_allclose(
            max_cube.values,
            np.array([[3.0, 4.0], [7.0, 8.0]], dtype=float),
        )

        sum_cube, _ = _reduce_cube_for_view(
            cube,
            keep_dims={"bottom_top", "west_east"},
            selectors={"south_north": {"mode": "sum"}},
            current_time_index=None,
        )
        np.testing.assert_allclose(
            sum_cube.values,
            np.array([[4.0, 6.0], [12.0, 14.0]], dtype=float),
        )


class FigureRenderingTests(unittest.TestCase):
    def test_plot_wrfout_cli_renders_named_figure(self) -> None:
        runs_dir = make_test_dir("_test_plot_wrfout_cli_v2")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.array([[300.0, 301.0], [302.0, 303.0]])],
            u10=[np.array([[1.0, 1.0], [1.0, 1.0]])],
            v10=[np.array([[2.0, 2.0], [2.0, 2.0]])],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            hgt=np.array([[100.0, 150.0], [200.0, 250.0]]),
            landmask=np.array([[1.0, 1.0], [0.0, 0.0]]),
        )

        post_spec_path = runs_dir / "post_spec.json"
        spec = default_post_spec("demo")
        spec["figures"][0]["figure_id"] = "surface_t2"
        spec["figures"][0]["selectors"]["time_indices"] = [0]
        post_spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

        output_path = runs_dir / "surface_t2.png"
        completed = subprocess.run(
            [
                sys.executable,
                str(PLOT_SCRIPT),
                "--wrfout",
                str(wrfout_path),
                "--figure-id",
                "surface_t2",
                "--post-spec",
                str(post_spec_path),
                "--out",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(output_path.exists())
        self.assertGreater(output_path.stat().st_size, 0)
        sidecar = load_json(output_path.with_suffix(".json"))
        self.assertEqual(sidecar["figure_id"], "surface_t2")
        self.assertEqual(sidecar["current_frame"]["valid_time"], "2024-07-20_00:00:00")
        self.assertEqual([layer["layer_id"] for layer in sidecar["resolved_layers"]], ["t2_c", "terrain"])
        self.assertEqual(sidecar["resolved_layers"][0]["style_id"], "temperature_raster")

    def test_run_figure_request_current_semantics_emit_per_frame_artifacts(self) -> None:
        runs_dir = make_test_dir("_test_v2_current_semantics")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[
                np.array([[300.0, 301.0], [302.0, 303.0]]),
                np.array([[304.0, 305.0], [306.0, 307.0]]),
            ],
            u10=[np.ones((2, 2)), np.ones((2, 2)) * 2.0],
            v10=[np.ones((2, 2)) * 2.0, np.ones((2, 2)) * 3.0],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        figure_spec = {
            "figure_id": "surface_t2",
            "render": {"format": "png", "title": "Surface T2", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "surface-t2",
                "sidecar_json": True,
                "overwrite": True,
            },
            "layers": [
                {
                    "layer_id": "t2_c",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "coolwarm", "show_colorbar": False},
                    },
                }
            ],
        }

        artifacts = run_figure_request(
            figure_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 2)
        self.assertEqual(
            [artifact["current_frame"]["valid_time"] for artifact in artifacts],
            ["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
        )
        self.assertTrue(all(len(artifact["selected_frames"]) == 2 for artifact in artifacts))

    def test_run_figure_request_range_only_supports_all_draw_kinds(self) -> None:
        runs_dir = make_test_dir("_test_v2_draw_kinds")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.full((2, 2), 1.0), np.full((2, 2), 5.0)],
            hgt=np.array([[100.0, 150.0], [200.0, 250.0]]),
            landmask=np.array([[1.0, 0.0], [1.0, 0.0]]),
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        output_path = runs_dir / "range-stack.png"
        figure_spec = {
            "figure_id": "range_stack",
            "render": {"format": "png", "title": "Range Stack", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "range-stack",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "landmask",
                    "draw": {
                        "kind": "categorical_fill",
                        "alpha": 0.5,
                        "zorder": 1,
                        "style": {
                            "categories": [
                                {"value": 0, "color": "#1f77b4", "label": "water"},
                                {"value": 1, "color": "#d8b365", "label": "land"},
                            ]
                        },
                    },
                },
                {
                    "layer_id": "accum_precip",
                    "draw": {
                        "kind": "raster",
                        "alpha": 0.9,
                        "zorder": 10,
                        "style": {"colormap": "Blues", "show_colorbar": True},
                    },
                },
                {
                    "layer_id": "terrain",
                    "draw": {
                        "kind": "contour",
                        "alpha": 0.8,
                        "zorder": 20,
                        "style": {"levels": [100, 200], "colors": "black", "linewidths": 0.7},
                    },
                },
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertIsNone(artifact["current_frame"])
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["figure_id"], "range_stack")
        self.assertEqual(len(sidecar["selected_frames"]), 2)
        self.assertEqual(
            [layer["draw"]["kind"] for layer in sidecar["resolved_layers"]],
            ["categorical_fill", "raster", "contour"],
        )
        self.assertAlmostEqual(sidecar["layer_summaries"]["accum_precip"]["mean"], 4.0)

    def test_run_figure_request_supports_wrf_native_3d_layers(self) -> None:
        runs_dir = make_test_dir("_test_v2_native_3d")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
            extra_3d_fields={
                "QVAPOR": (
                    [
                        np.array(
                            [
                                [[0.001, 0.001], [0.001, 0.001]],
                                [[0.003, 0.003], [0.003, 0.003]],
                            ]
                        ),
                        np.array(
                            [
                                [[0.0015, 0.0015], [0.0015, 0.0015]],
                                [[0.004, 0.004], [0.004, 0.004]],
                            ]
                        ),
                    ],
                    "kg kg-1",
                )
            },
        )

        layer_defs = {
            "qvapor_lvl1": {
                "source": {
                    "kind": "wrf_native_3d",
                    "level_selector": {"mode": "index", "index": 1},
                },
                "expr": "QVAPOR * 1000",
                "units": "g kg-1",
                "metadata": {},
            }
        }
        figure_spec = {
            "figure_id": "qvapor_lvl1",
            "render": {"format": "png", "title": "QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "qvapor-lvl1",
                "sidecar_json": True,
                "overwrite": True,
            },
            "layers": [
                {
                    "layer_id": "qvapor_lvl1",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": False},
                    },
                }
            ],
        }

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [1]})
        artifacts = run_figure_request(
            figure_spec,
            layer_defs,
            selected_frames,
            runs_dir,
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        self.assertAlmostEqual(artifacts[0]["layer_summaries"]["qvapor_lvl1"]["mean"], 4.0, places=6)

    def test_run_figure_request_supports_wrf_diag_layers(self) -> None:
        runs_dir = make_test_dir("_test_v2_wrf_diag")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2)) * 2.0],
            v10=[np.ones((2, 2)) * 2.0, np.ones((2, 2)) * 3.0],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.full((2, 2), 1.0), np.full((2, 2), 5.0)],
        )

        layer_defs = {
            "accum_precip_diag": {
                "source": {"kind": "wrf_diag"},
                "expr": "last(total_precip) - first(total_precip)",
                "units": "mm",
                "metadata": {},
            }
        }
        figure_spec = {
            "figure_id": "accum_precip_diag",
            "render": {"format": "png", "title": "Accum Diag", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "accum-precip-diag",
                "sidecar_json": True,
                "overwrite": True,
            },
            "layers": [
                {
                    "layer_id": "accum_precip_diag",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "Blues", "show_colorbar": False},
                    },
                }
            ],
        }

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        artifacts = run_figure_request(
            figure_spec,
            layer_defs,
            selected_frames,
            runs_dir,
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        self.assertIsNone(artifacts[0]["current_frame"])
        self.assertAlmostEqual(artifacts[0]["layer_summaries"]["accum_precip_diag"]["mean"], 4.0)

    def test_run_figure_request_supports_vector_quiver_layers(self) -> None:
        runs_dir = make_test_dir("_test_v2_vector_quiver")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.array([[300.0, 301.0], [302.0, 303.0]])],
            u10=[np.full((2, 2), 3.0)],
            v10=[np.full((2, 2), 4.0)],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            hgt=np.array([[100.0, 150.0], [200.0, 250.0]]),
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "surface-wind-vectors.png"
        figure_spec = {
            "figure_id": "surface_wind_vectors",
            "render": {"format": "png", "title": "Surface Wind Vectors", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "surface-wind-vectors",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "t2_c",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "coolwarm", "show_colorbar": False},
                    },
                },
                {
                    "u_layer_id": "u10",
                    "v_layer_id": "v10",
                    "style_id": "wind_quiver",
                    "draw": build_style_defs()["wind_quiver"],
                },
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())
        self.assertIsNotNone(artifact["current_frame"])
        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(
            [layer["draw"]["kind"] for layer in sidecar["resolved_layers"]],
            ["raster", "vector"],
        )
        self.assertEqual(sidecar["resolved_layers"][1]["u_layer_id"], "u10")
        self.assertEqual(sidecar["resolved_layers"][1]["v_layer_id"], "v10")
        self.assertAlmostEqual(sidecar["resolved_layers"][1]["magnitude_summary"]["mean"], 5.0)
        self.assertAlmostEqual(sidecar["layer_summaries"]["u10"]["mean"], 3.0)
        self.assertAlmostEqual(sidecar["layer_summaries"]["v10"]["mean"], 4.0)

    def test_run_figure_request_supports_path_section_vector_axis_projection(self) -> None:
        runs_dir = make_test_dir("_test_v2_path_section_vectors")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        lat = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 1.0, 1.0],
            ],
            dtype=float,
        )
        lon = np.array(
            [
                [0.0, 1.0, 2.0],
                [0.0, 1.0, 2.0],
            ],
            dtype=float,
        )
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.full((2, 3), 300.0)],
            u10=[np.full((2, 3), 3.0)],
            v10=[np.full((2, 3), 4.0)],
            rainc=[np.zeros((2, 3))],
            rainnc=[np.zeros((2, 3))],
            lat=lat,
            lon=lon,
            extra_3d_fields={
                "U_PATH": ([np.full((2, 2, 3), 3.0)], "m s-1"),
                "V_PATH": ([np.full((2, 2, 3), 4.0)], "m s-1"),
                "W_PATH": ([np.full((2, 2, 3), 2.0)], "m s-1"),
            },
        )

        layer_defs = {
            "u_path": {
                "source": {"kind": "wrf_native_3d_full"},
                "expr": "U_PATH",
                "units": "m s-1",
                "metadata": {"description": "Path-section eastward wind"},
            },
            "v_path": {
                "source": {"kind": "wrf_native_3d_full"},
                "expr": "V_PATH",
                "units": "m s-1",
                "metadata": {"description": "Path-section northward wind"},
            },
            "w_path": {
                "source": {"kind": "wrf_native_3d_full"},
                "expr": "W_PATH",
                "units": "m s-1",
                "metadata": {"description": "Path-section vertical velocity"},
            },
        }
        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "path-section-vectors.png"
        figure_spec = {
            "figure_id": "path_section_vectors",
            "view": {
                "x_axis": {"kind": "path_coord", "name": "distance_km"},
                "y_axis": {"name": "bottom_top"},
                "selectors": {"time": {"mode": "current"}},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 0.5, "lon": 0.0},
                            {"lat": 0.5, "lon": 2.0},
                        ],
                        "samples": 5,
                    }
                },
            },
            "render": {"format": "png", "title": "Path Section Vectors", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "path-section-vectors",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "u_layer_id": "u_path",
                    "v_layer_id": "v_path",
                    "vertical_layer_id": "w_path",
                    "draw": {
                        "kind": "vector",
                        "alpha": 0.9,
                        "zorder": 20,
                        "style": {
                            "mode": "quiver",
                            "stride": 1,
                            "scale": 25,
                            "color": "black",
                            "pivot": "mid",
                            "axis_projection": {
                                "kind": "path_section",
                                "x_component": "path_tangent",
                                "y_component": "vertical",
                            },
                        },
                    },
                },
                {
                    "u_layer_id": "u_path",
                    "v_layer_id": "v_path",
                    "vertical_layer_id": "w_path",
                    "draw": {
                        "kind": "vector",
                        "alpha": 0.75,
                        "zorder": 30,
                        "style": {
                            "mode": "quiver",
                            "stride": 1,
                            "scale": 25,
                            "color": "red",
                            "pivot": "mid",
                            "axis_projection": {
                                "kind": "path_section",
                                "x_component": "path_normal",
                                "y_component": "vertical",
                            },
                        },
                    },
                },
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                layer_defs,
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())
        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "distance_km")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "bottom_top")
        self.assertEqual(len(sidecar["resolved_layers"]), 2)
        tangent_layer = sidecar["resolved_layers"][0]
        normal_layer = sidecar["resolved_layers"][1]
        self.assertEqual(tangent_layer["axis_projection"]["x_component"], "path_tangent")
        self.assertEqual(tangent_layer["axis_projection"]["y_component"], "vertical")
        self.assertEqual(tangent_layer["vertical_layer_id"], "w_path")
        self.assertAlmostEqual(tangent_layer["u_summary"]["mean"], 3.0)
        self.assertAlmostEqual(tangent_layer["v_summary"]["mean"], 4.0)
        self.assertAlmostEqual(tangent_layer["vertical_summary"]["mean"], 2.0)
        self.assertAlmostEqual(tangent_layer["x_component_summary"]["mean"], 3.0)
        self.assertAlmostEqual(tangent_layer["y_component_summary"]["mean"], 2.0)
        self.assertAlmostEqual(tangent_layer["magnitude_summary"]["mean"], np.sqrt(13.0))
        self.assertEqual(normal_layer["axis_projection"]["x_component"], "path_normal")
        self.assertEqual(normal_layer["axis_projection"]["y_component"], "vertical")
        self.assertAlmostEqual(normal_layer["x_component_summary"]["mean"], 4.0)
        self.assertAlmostEqual(normal_layer["y_component_summary"]["mean"], 2.0)
        self.assertAlmostEqual(sidecar["layer_summaries"]["u_path"]["mean"], 3.0)
        self.assertAlmostEqual(sidecar["layer_summaries"]["v_path"]["mean"], 4.0)
        self.assertAlmostEqual(sidecar["layer_summaries"]["w_path"]["mean"], 2.0)

    def test_run_figure_request_destaggers_real_like_path_section_vectors(self) -> None:
        runs_dir = make_test_dir("_test_v2_path_section_vectors_destaggered")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        lat = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 1.0, 1.0],
            ],
            dtype=float,
        )
        lon = np.array(
            [
                [0.0, 1.0, 2.0],
                [0.0, 1.0, 2.0],
            ],
            dtype=float,
        )
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.full((2, 3), 300.0)],
            u10=[np.full((2, 3), 3.0)],
            v10=[np.full((2, 3), 4.0)],
            rainc=[np.zeros((2, 3))],
            rainnc=[np.zeros((2, 3))],
            lat=lat,
            lon=lon,
            extra_dim_3d_fields={
                "U": ([np.full((2, 2, 4), 3.0)], ("bottom_top", "south_north", "west_east_stag"), "m s-1"),
                "V": ([np.full((2, 3, 3), 4.0)], ("bottom_top", "south_north_stag", "west_east"), "m s-1"),
                "W": ([np.full((3, 2, 3), 2.0)], ("bottom_top_stag", "south_north", "west_east"), "m s-1"),
            },
        )

        layer_defs = {
            "u_mass": {
                "source": {"kind": "wrf_native_3d_full"},
                "expr": "U",
                "units": "m s-1",
                "metadata": {"description": "Real-like staggered U wind"},
            },
            "v_mass": {
                "source": {"kind": "wrf_native_3d_full"},
                "expr": "V",
                "units": "m s-1",
                "metadata": {"description": "Real-like staggered V wind"},
            },
            "w_mass": {
                "source": {"kind": "wrf_native_3d_full"},
                "expr": "W",
                "units": "m s-1",
                "metadata": {"description": "Real-like staggered vertical wind"},
            },
        }
        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "path-section-vectors-destaggered.png"
        figure_spec = {
            "figure_id": "path_section_vectors_destaggered",
            "view": {
                "x_axis": {"kind": "path_coord", "name": "distance_km"},
                "y_axis": {"name": "bottom_top"},
                "selectors": {"time": {"mode": "current"}},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 0.5, "lon": 0.0},
                            {"lat": 0.5, "lon": 2.0},
                        ],
                        "samples": 5,
                    }
                },
            },
            "render": {"format": "png", "title": "Destaggered Path Section Vectors", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "path-section-vectors-destaggered",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "u_layer_id": "u_mass",
                    "v_layer_id": "v_mass",
                    "vertical_layer_id": "w_mass",
                    "draw": {
                        "kind": "vector",
                        "alpha": 0.9,
                        "zorder": 20,
                        "style": {
                            "mode": "quiver",
                            "stride": 1,
                            "scale": 25,
                            "color": "black",
                            "pivot": "mid",
                            "axis_projection": {
                                "kind": "path_section",
                                "x_component": "path_tangent",
                                "y_component": "vertical",
                            },
                        },
                    },
                },
                {
                    "u_layer_id": "u_mass",
                    "v_layer_id": "v_mass",
                    "vertical_layer_id": "w_mass",
                    "draw": {
                        "kind": "vector",
                        "alpha": 0.75,
                        "zorder": 30,
                        "style": {
                            "mode": "quiver",
                            "stride": 1,
                            "scale": 25,
                            "color": "red",
                            "pivot": "mid",
                            "axis_projection": {
                                "kind": "path_section",
                                "x_component": "path_normal",
                                "y_component": "vertical",
                            },
                        },
                    },
                },
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                layer_defs,
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())
        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "distance_km")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "bottom_top")
        self.assertEqual(len(sidecar["resolved_layers"]), 2)
        tangent_layer = sidecar["resolved_layers"][0]
        normal_layer = sidecar["resolved_layers"][1]
        self.assertEqual(tangent_layer["u_layer_id"], "u_mass")
        self.assertEqual(tangent_layer["v_layer_id"], "v_mass")
        self.assertEqual(tangent_layer["vertical_layer_id"], "w_mass")
        self.assertAlmostEqual(tangent_layer["u_summary"]["mean"], 3.0)
        self.assertAlmostEqual(tangent_layer["v_summary"]["mean"], 4.0)
        self.assertAlmostEqual(tangent_layer["vertical_summary"]["mean"], 2.0)
        self.assertAlmostEqual(tangent_layer["x_component_summary"]["mean"], 3.0)
        self.assertAlmostEqual(tangent_layer["y_component_summary"]["mean"], 2.0)
        self.assertAlmostEqual(normal_layer["x_component_summary"]["mean"], 4.0)
        self.assertAlmostEqual(normal_layer["y_component_summary"]["mean"], 2.0)

    def test_run_figure_request_supports_time_x_view(self) -> None:
        runs_dir = make_test_dir("_test_v2_time_x_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00", "2024-07-20_02:00:00"],
            t2=[
                np.array([[300.0, 301.0, 302.0], [303.0, 304.0, 305.0]]),
                np.array([[301.0, 302.0, 303.0], [304.0, 305.0, 306.0]]),
                np.array([[302.0, 303.0, 304.0], [305.0, 306.0, 307.0]]),
            ],
            u10=[np.ones((2, 3)), np.ones((2, 3)), np.ones((2, 3))],
            v10=[np.ones((2, 3)), np.ones((2, 3)), np.ones((2, 3))],
            rainc=[np.zeros((2, 3)), np.zeros((2, 3)), np.zeros((2, 3))],
            rainnc=[np.zeros((2, 3)), np.zeros((2, 3)), np.zeros((2, 3))],
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1, 2]})
        figure_spec = {
            "figure_id": "time_x_t2",
            "view_id": "time_x_line",
            "render": {"format": "png", "title": "Time-X T2", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "time-x-t2",
                "sidecar_json": True,
                "overwrite": True,
            },
            "layers": [
                {
                    "layer_id": "t2_c",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "coolwarm", "show_colorbar": False},
                    },
                }
            ],
        }
        view_defs = {
            "time_x_line": {
                "x_axis": {"name": "time"},
                "y_axis": {"name": "west_east"},
                "selectors": {
                    "south_north": {"mode": "index", "index": 1},
                },
            }
        }

        artifacts = run_figure_request(
            figure_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            view_defs=view_defs,
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertIsNone(artifact["current_frame"])
        self.assertEqual(artifact["view"]["x_axis"]["name"], "time")
        self.assertEqual(artifact["view"]["y_axis"]["name"], "west_east")
        self.assertAlmostEqual(artifact["layer_summaries"]["t2_c"]["mean"], 31.85, places=6)

    def test_run_figure_request_supports_time_x_view_with_mean_selector(self) -> None:
        runs_dir = make_test_dir("_test_v2_time_x_view_mean_selector")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00", "2024-07-20_02:00:00"],
            t2=[
                np.array([[300.0, 302.0, 304.0], [306.0, 308.0, 310.0]]),
                np.array([[301.0, 303.0, 305.0], [307.0, 309.0, 311.0]]),
                np.array([[302.0, 304.0, 306.0], [308.0, 310.0, 312.0]]),
            ],
            u10=[np.ones((2, 3)), np.ones((2, 3)), np.ones((2, 3))],
            v10=[np.ones((2, 3)), np.ones((2, 3)), np.ones((2, 3))],
            rainc=[np.zeros((2, 3)), np.zeros((2, 3)), np.zeros((2, 3))],
            rainnc=[np.zeros((2, 3)), np.zeros((2, 3)), np.zeros((2, 3))],
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1, 2]})
        figure_spec = {
            "figure_id": "time_x_t2_mean",
            "view_id": "time_x_line_mean",
            "render": {"format": "png", "title": "Time-X T2 Mean", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "time-x-t2-mean",
                "sidecar_json": True,
                "overwrite": True,
            },
            "layers": [
                {
                    "layer_id": "t2_c",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "coolwarm", "show_colorbar": False},
                    },
                }
            ],
        }
        view_defs = {
            "time_x_line_mean": {
                "x_axis": {"name": "time"},
                "y_axis": {"name": "west_east"},
                "selectors": {
                    "south_north": {"mode": "mean"},
                },
            }
        }

        artifacts = run_figure_request(
            figure_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            view_defs=view_defs,
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertIsNone(artifact["current_frame"])
        self.assertEqual(artifact["view"]["x_axis"]["name"], "time")
        self.assertEqual(artifact["view"]["y_axis"]["name"], "west_east")
        self.assertAlmostEqual(artifact["layer_summaries"]["t2_c"]["mean"], 32.85, places=6)

    def test_run_figure_request_supports_time_height_view_with_3d_full(self) -> None:
        runs_dir = make_test_dir("_test_v2_time_height_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
            extra_3d_fields={
                "QVAPOR": (
                    [
                        np.array(
                            [
                                [[0.001, 0.002], [0.003, 0.004]],
                                [[0.005, 0.006], [0.007, 0.008]],
                            ]
                        ),
                        np.array(
                            [
                                [[0.002, 0.003], [0.004, 0.005]],
                                [[0.006, 0.007], [0.008, 0.009]],
                            ]
                        ),
                    ],
                    "kg kg-1",
                )
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        figure_spec = {
            "figure_id": "time_height_qvapor",
            "view_id": "time_height_point",
            "render": {"format": "png", "title": "Time-Height QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "time-height-qvapor",
                "sidecar_json": True,
                "overwrite": True,
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": False},
                    },
                }
            ],
        }
        view_defs = {
            "time_height_point": {
                "x_axis": {"name": "time"},
                "y_axis": {"name": "bottom_top"},
                "selectors": {
                    "south_north": {"mode": "index", "index": 0},
                    "west_east": {"mode": "index", "index": 1},
                },
            }
        }

        artifacts = run_figure_request(
            figure_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            view_defs=view_defs,
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertIsNone(artifact["current_frame"])
        self.assertEqual(artifact["view"]["x_axis"]["name"], "time")
        self.assertEqual(artifact["view"]["y_axis"]["name"], "bottom_top")
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 4.5, places=6)

    def test_run_figure_request_supports_time_height_view_with_height_m(self) -> None:
        runs_dir = make_test_dir("_test_v2_time_height_height_m_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
            extra_3d_fields={
                "QVAPOR": (
                    [
                        np.array(
                            [
                                [[0.001, 0.002], [0.003, 0.004]],
                                [[0.005, 0.006], [0.007, 0.008]],
                            ]
                        ),
                        np.array(
                            [
                                [[0.002, 0.003], [0.004, 0.005]],
                                [[0.006, 0.007], [0.008, 0.009]],
                            ]
                        ),
                    ],
                    "kg kg-1",
                )
            },
            extra_stag_3d_fields={
                "PH": (
                    [
                        np.array(
                            [
                                [[0.0, 0.0], [0.0, 0.0]],
                                [[1000.0, 1000.0], [1000.0, 1000.0]],
                                [[3000.0, 3000.0], [3000.0, 3000.0]],
                            ]
                        )
                        * 9.81,
                        np.array(
                            [
                                [[0.0, 0.0], [0.0, 0.0]],
                                [[1200.0, 1200.0], [1200.0, 1200.0]],
                                [[3200.0, 3200.0], [3200.0, 3200.0]],
                            ]
                        )
                        * 9.81,
                    ],
                    "m2 s-2",
                ),
                "PHB": (
                    [
                        np.zeros((3, 2, 2), dtype=float),
                        np.zeros((3, 2, 2), dtype=float),
                    ],
                    "m2 s-2",
                ),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        output_path = runs_dir / "time-height-qvapor-height-m.png"
        figure_spec = {
            "figure_id": "time_height_qvapor_height_m",
            "view": {
                "x_axis": {"name": "time"},
                "y_axis": {"kind": "derived_coord", "name": "height_m"},
                "selectors": {
                    "south_north": {"mode": "index", "index": 0},
                    "west_east": {"mode": "index", "index": 1},
                },
            },
            "render": {"format": "png", "title": "Time-Height QVAPOR Height", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "time-height-qvapor-height-m",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": False},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 4.5, places=6)
        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "time")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "height_m")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "m")

    def test_run_figure_request_supports_height_time_view_with_height_m(self) -> None:
        runs_dir = make_test_dir("_test_v2_height_time_height_m_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
            extra_3d_fields={
                "QVAPOR": (
                    [
                        np.array(
                            [
                                [[0.001, 0.002], [0.003, 0.004]],
                                [[0.005, 0.006], [0.007, 0.008]],
                            ]
                        ),
                        np.array(
                            [
                                [[0.002, 0.003], [0.004, 0.005]],
                                [[0.006, 0.007], [0.008, 0.009]],
                            ]
                        ),
                    ],
                    "kg kg-1",
                )
            },
            extra_stag_3d_fields={
                "PH": (
                    [
                        np.array(
                            [
                                [[0.0, 0.0], [0.0, 0.0]],
                                [[1000.0, 1000.0], [1000.0, 1000.0]],
                                [[3000.0, 3000.0], [3000.0, 3000.0]],
                            ]
                        )
                        * 9.81,
                        np.array(
                            [
                                [[0.0, 0.0], [0.0, 0.0]],
                                [[1200.0, 1200.0], [1200.0, 1200.0]],
                                [[3200.0, 3200.0], [3200.0, 3200.0]],
                            ]
                        )
                        * 9.81,
                    ],
                    "m2 s-2",
                ),
                "PHB": (
                    [
                        np.zeros((3, 2, 2), dtype=float),
                        np.zeros((3, 2, 2), dtype=float),
                    ],
                    "m2 s-2",
                ),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        output_path = runs_dir / "height-time-qvapor-height-m.png"
        figure_spec = {
            "figure_id": "height_time_qvapor_height_m",
            "view": {
                "x_axis": {"kind": "derived_coord", "name": "height_m"},
                "y_axis": {"name": "time"},
                "selectors": {
                    "south_north": {"mode": "index", "index": 0},
                    "west_east": {"mode": "index", "index": 1},
                },
            },
            "render": {"format": "png", "title": "Height-Time QVAPOR Height", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "height-time-qvapor-height-m",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": False},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 4.5, places=6)
        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "height_m")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "m")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "time")

    def test_run_figure_request_supports_time_pressure_view_with_3d_full(self) -> None:
        runs_dir = make_test_dir("_test_v2_time_pressure_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
            extra_3d_fields={
                "QVAPOR": (
                    [
                        np.array(
                            [
                                [[0.001, 0.002], [0.003, 0.004]],
                                [[0.005, 0.006], [0.007, 0.008]],
                            ]
                        ),
                        np.array(
                            [
                                [[0.002, 0.003], [0.004, 0.005]],
                                [[0.006, 0.007], [0.008, 0.009]],
                            ]
                        ),
                    ],
                    "kg kg-1",
                ),
                "P": (
                    [
                        np.array(
                            [
                                [[90000.0, 90000.0], [90000.0, 90000.0]],
                                [[80000.0, 80000.0], [80000.0, 80000.0]],
                            ]
                        ),
                        np.array(
                            [
                                [[89000.0, 89000.0], [89000.0, 89000.0]],
                                [[79000.0, 79000.0], [79000.0, 79000.0]],
                            ]
                        ),
                    ],
                    "Pa",
                ),
                "PB": (
                    [
                        np.zeros((2, 2, 2), dtype=float),
                        np.zeros((2, 2, 2), dtype=float),
                    ],
                    "Pa",
                ),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        output_path = runs_dir / "time-pressure-qvapor.png"
        figure_spec = {
            "figure_id": "time_pressure_qvapor",
            "view": {
                "x_axis": {"name": "time"},
                "y_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                "selectors": {
                    "south_north": {"mode": "index", "index": 0},
                    "west_east": {"mode": "index", "index": 1},
                },
            },
            "render": {"format": "png", "title": "Time-Pressure QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "time-pressure-qvapor",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": False},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 4.5, places=6)
        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "time")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "pressure_hpa")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "hPa")

    def test_run_figure_request_supports_pressure_time_view_with_3d_full(self) -> None:
        runs_dir = make_test_dir("_test_v2_pressure_time_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
            extra_3d_fields={
                "QVAPOR": (
                    [
                        np.array(
                            [
                                [[0.001, 0.002], [0.003, 0.004]],
                                [[0.005, 0.006], [0.007, 0.008]],
                            ]
                        ),
                        np.array(
                            [
                                [[0.002, 0.003], [0.004, 0.005]],
                                [[0.006, 0.007], [0.008, 0.009]],
                            ]
                        ),
                    ],
                    "kg kg-1",
                ),
                "P": (
                    [
                        np.array(
                            [
                                [[90000.0, 90000.0], [90000.0, 90000.0]],
                                [[80000.0, 80000.0], [80000.0, 80000.0]],
                            ]
                        ),
                        np.array(
                            [
                                [[89000.0, 89000.0], [89000.0, 89000.0]],
                                [[79000.0, 79000.0], [79000.0, 79000.0]],
                            ]
                        ),
                    ],
                    "Pa",
                ),
                "PB": (
                    [
                        np.zeros((2, 2, 2), dtype=float),
                        np.zeros((2, 2, 2), dtype=float),
                    ],
                    "Pa",
                ),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1]})
        output_path = runs_dir / "pressure-time-qvapor.png"
        figure_spec = {
            "figure_id": "pressure_time_qvapor",
            "view": {
                "x_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                "y_axis": {"name": "time"},
                "selectors": {
                    "south_north": {"mode": "index", "index": 0},
                    "west_east": {"mode": "index", "index": 1},
                },
            },
            "render": {"format": "png", "title": "Pressure-Time QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "pressure-time-qvapor",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": False},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 4.5, places=6)
        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "pressure_hpa")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "hPa")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "time")

    def test_run_figure_request_supports_distance_height_path_view(self) -> None:
        runs_dir = make_test_dir("_test_v2_distance_height_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        lat = np.array([[10.0, 10.0, 10.0], [11.0, 11.0, 11.0]], dtype=float)
        lon = np.array([[100.0, 101.0, 102.0], [100.0, 101.0, 102.0]], dtype=float)
        qvapor_gkg = np.array(
            [
                [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
                [[11.0, 12.0, 13.0], [14.0, 15.0, 16.0]],
            ],
            dtype=float,
        )
        ph_interfaces = np.array(
            [
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[200.0, 300.0, 400.0], [500.0, 600.0, 700.0]],
                [[1800.0, 1800.0, 1800.0], [2000.0, 2000.0, 2000.0]],
            ],
            dtype=float,
        ) * 9.81

        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.full((2, 3), 300.0)],
            u10=[np.ones((2, 3))],
            v10=[np.ones((2, 3))],
            rainc=[np.zeros((2, 3))],
            rainnc=[np.zeros((2, 3))],
            lat=lat,
            lon=lon,
            extra_3d_fields={
                "QVAPOR": ([qvapor_gkg / 1000.0], "kg kg-1"),
            },
            extra_stag_3d_fields={
                "PH": ([ph_interfaces], "m2 s-2"),
                "PHB": ([np.zeros_like(ph_interfaces)], "m2 s-2"),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "distance-height-qvapor.png"
        figure_spec = {
            "figure_id": "distance_height_qvapor",
            "view": {
                "x_axis": {"kind": "path_coord", "name": "distance_km"},
                "y_axis": {"kind": "derived_coord", "name": "height_m"},
                "selectors": {},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 10.0, "lon": 100.0},
                            {"lat": 10.0, "lon": 102.0},
                        ],
                        "samples": 3,
                    }
                },
            },
            "render": {"format": "png", "title": "Distance-Height QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "distance-height-qvapor",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 7.0, places=6)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "distance_km")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "km")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "height_m")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "m")
        self.assertEqual(len(sidecar["resolved_layers"]), 1)

    def test_run_figure_request_path_view_uses_bilinear_sampling(self) -> None:
        runs_dir = make_test_dir("_test_v2_distance_height_bilinear")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        lat = np.array([[10.0, 10.0], [11.0, 11.0]], dtype=float)
        lon = np.array([[100.0, 101.0], [100.0, 101.0]], dtype=float)
        qvapor_gkg = np.array(
            [
                [[1.0, 3.0], [5.0, 7.0]],
                [[11.0, 13.0], [15.0, 17.0]],
            ],
            dtype=float,
        )
        ph_interfaces = np.array(
            [
                [[0.0, 0.0], [0.0, 0.0]],
                [[200.0, 200.0], [200.0, 200.0]],
                [[1000.0, 1000.0], [1000.0, 1000.0]],
            ],
            dtype=float,
        ) * 9.81

        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.full((2, 2), 300.0)],
            u10=[np.ones((2, 2))],
            v10=[np.ones((2, 2))],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            lat=lat,
            lon=lon,
            extra_3d_fields={
                "QVAPOR": ([qvapor_gkg / 1000.0], "kg kg-1"),
            },
            extra_stag_3d_fields={
                "PH": ([ph_interfaces], "m2 s-2"),
                "PHB": ([np.zeros_like(ph_interfaces)], "m2 s-2"),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "distance-height-qvapor-bilinear.png"
        figure_spec = {
            "figure_id": "distance_height_qvapor_bilinear",
            "view": {
                "x_axis": {"kind": "path_coord", "name": "distance_km"},
                "y_axis": {"kind": "derived_coord", "name": "height_m"},
                "selectors": {},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 10.0, "lon": 100.0},
                            {"lat": 11.0, "lon": 101.0},
                        ],
                        "samples": 3,
                    }
                },
            },
            "render": {"format": "png", "title": "Distance-Height QVAPOR Bilinear", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "distance-height-qvapor-bilinear",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 9.0, places=6)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["label"], "distance_km")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "km")
        self.assertEqual(sidecar["view"]["y_axis"]["label"], "height_m")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "m")

    def test_run_figure_request_supports_distance_pressure_path_view(self) -> None:
        runs_dir = make_test_dir("_test_v2_distance_pressure_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        lat = np.array([[10.0, 10.0], [11.0, 11.0]], dtype=float)
        lon = np.array([[100.0, 101.0], [100.0, 101.0]], dtype=float)
        qvapor_gkg = np.array(
            [
                [[1.0, 3.0], [5.0, 7.0]],
                [[11.0, 13.0], [15.0, 17.0]],
            ],
            dtype=float,
        )

        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.full((2, 2), 300.0)],
            u10=[np.ones((2, 2))],
            v10=[np.ones((2, 2))],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            lat=lat,
            lon=lon,
            extra_3d_fields={
                "QVAPOR": ([qvapor_gkg / 1000.0], "kg kg-1"),
                "P": (
                    [
                        np.array(
                            [
                                [[90000.0, 90000.0], [90000.0, 90000.0]],
                                [[80000.0, 80000.0], [80000.0, 80000.0]],
                            ]
                        )
                    ],
                    "Pa",
                ),
                "PB": ([np.zeros((2, 2, 2), dtype=float)], "Pa"),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "distance-pressure-qvapor.png"
        figure_spec = {
            "figure_id": "distance_pressure_qvapor",
            "view": {
                "x_axis": {"kind": "path_coord", "name": "distance_km"},
                "y_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                "selectors": {},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 10.0, "lon": 100.0},
                            {"lat": 11.0, "lon": 101.0},
                        ],
                        "samples": 3,
                    }
                },
            },
            "render": {"format": "png", "title": "Distance-Pressure QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "distance-pressure-qvapor",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 9.0, places=6)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["label"], "distance_km")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "km")
        self.assertEqual(sidecar["view"]["y_axis"]["label"], "pressure_hpa")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "hPa")

    def test_run_figure_request_supports_height_distance_path_view(self) -> None:
        runs_dir = make_test_dir("_test_v2_height_distance_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        lat = np.array([[10.0, 10.0], [11.0, 11.0]], dtype=float)
        lon = np.array([[100.0, 101.0], [100.0, 101.0]], dtype=float)
        qvapor_gkg = np.array(
            [
                [[1.0, 3.0], [5.0, 7.0]],
                [[11.0, 13.0], [15.0, 17.0]],
            ],
            dtype=float,
        )
        ph_interfaces = np.array(
            [
                [[0.0, 0.0], [0.0, 0.0]],
                [[200.0, 200.0], [200.0, 200.0]],
                [[1000.0, 1000.0], [1000.0, 1000.0]],
            ],
            dtype=float,
        ) * 9.81

        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.full((2, 2), 300.0)],
            u10=[np.ones((2, 2))],
            v10=[np.ones((2, 2))],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            lat=lat,
            lon=lon,
            extra_3d_fields={
                "QVAPOR": ([qvapor_gkg / 1000.0], "kg kg-1"),
            },
            extra_stag_3d_fields={
                "PH": ([ph_interfaces], "m2 s-2"),
                "PHB": ([np.zeros_like(ph_interfaces)], "m2 s-2"),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "height-distance-qvapor.png"
        figure_spec = {
            "figure_id": "height_distance_qvapor",
            "view": {
                "x_axis": {"kind": "derived_coord", "name": "height_m"},
                "y_axis": {"kind": "path_coord", "name": "distance_km"},
                "selectors": {},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 10.0, "lon": 100.0},
                            {"lat": 11.0, "lon": 101.0},
                        ],
                        "samples": 3,
                    }
                },
            },
            "render": {"format": "png", "title": "Height-Distance QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "height-distance-qvapor",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 9.0, places=6)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["label"], "height_m")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "m")
        self.assertEqual(sidecar["view"]["y_axis"]["label"], "distance_km")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "km")

    def test_run_figure_request_supports_pressure_distance_path_view(self) -> None:
        runs_dir = make_test_dir("_test_v2_pressure_distance_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        lat = np.array([[10.0, 10.0], [11.0, 11.0]], dtype=float)
        lon = np.array([[100.0, 101.0], [100.0, 101.0]], dtype=float)
        qvapor_gkg = np.array(
            [
                [[1.0, 3.0], [5.0, 7.0]],
                [[11.0, 13.0], [15.0, 17.0]],
            ],
            dtype=float,
        )

        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.full((2, 2), 300.0)],
            u10=[np.ones((2, 2))],
            v10=[np.ones((2, 2))],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            lat=lat,
            lon=lon,
            extra_3d_fields={
                "QVAPOR": ([qvapor_gkg / 1000.0], "kg kg-1"),
                "P": (
                    [
                        np.array(
                            [
                                [[90000.0, 90000.0], [90000.0, 90000.0]],
                                [[80000.0, 80000.0], [80000.0, 80000.0]],
                            ]
                        )
                    ],
                    "Pa",
                ),
                "PB": ([np.zeros((2, 2, 2), dtype=float)], "Pa"),
            },
        )

        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "pressure-distance-qvapor.png"
        figure_spec = {
            "figure_id": "pressure_distance_qvapor",
            "view": {
                "x_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                "y_axis": {"kind": "path_coord", "name": "distance_km"},
                "selectors": {},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 10.0, "lon": 100.0},
                            {"lat": 11.0, "lon": 101.0},
                        ],
                        "samples": 3,
                    }
                },
            },
            "render": {"format": "png", "title": "Pressure-Distance QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "pressure-distance-qvapor",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 9.0, places=6)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["label"], "pressure_hpa")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "hPa")
        self.assertEqual(sidecar["view"]["y_axis"]["label"], "distance_km")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "km")

    def test_run_figure_request_real_height_distance_view_smoke(self) -> None:
        runs_dir = make_test_dir("_test_real_height_distance_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        real_state = load_json(REAL_PROJECT_JSON)
        wrfout_path = resolve_repo_path(real_state["artifacts"]["wrfout_files"][0])
        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "height-distance-qvapor-real.png"
        figure_spec = {
            "figure_id": "height_distance_qvapor_real",
            "view": {
                "x_axis": {"kind": "derived_coord", "name": "height_m"},
                "y_axis": {"kind": "path_coord", "name": "distance_km"},
                "selectors": {},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 30.9, "lon": 121.0},
                            {"lat": 31.3, "lon": 121.6},
                        ],
                        "samples": 24,
                    }
                },
            },
            "render": {"format": "png", "title": "Real Height-Distance QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "height-distance-qvapor-real",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 6.7053302147327685, places=6)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "height_m")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "m")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "distance_km")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "km")

    def test_run_figure_request_real_path_view_avoids_pcolormesh_warning(self) -> None:
        runs_dir = make_test_dir("_test_real_distance_height_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        real_state = load_json(REAL_PROJECT_JSON)
        wrfout_path = resolve_repo_path(real_state["artifacts"]["wrfout_files"][0])
        frames = enumerate_wrfout_frames([wrfout_path])
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0]})
        output_path = runs_dir / "distance-height-qvapor-real.png"
        figure_spec = {
            "figure_id": "distance_height_qvapor_real",
            "view": {
                "x_axis": {"kind": "path_coord", "name": "distance_km"},
                "y_axis": {"kind": "derived_coord", "name": "height_m"},
                "selectors": {},
                "sampling": {
                    "path": {
                        "kind": "polyline",
                        "points": [
                            {"lat": 30.9, "lon": 121.0},
                            {"lat": 31.3, "lon": 121.6},
                        ],
                        "samples": 24,
                    }
                },
            },
            "render": {"format": "png", "title": "Real Distance-Height QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "distance-height-qvapor-real",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())
        self.assertAlmostEqual(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"], 6.7053302147327685, places=6)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "distance_km")
        self.assertEqual(sidecar["view"]["x_axis"]["units"], "km")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "height_m")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "m")
        self.assertEqual(sidecar["selected_frames"][0]["valid_time"], "2024-07-20_00:00:00")

    def test_run_figure_request_real_time_pressure_view_smoke(self) -> None:
        runs_dir = make_test_dir("_test_real_time_pressure_view")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        real_state = load_json(REAL_PROJECT_JSON)
        wrfout_paths = [
            resolve_repo_path(item)
            for item in real_state["artifacts"]["wrfout_files"][:3]
        ]
        frames = enumerate_wrfout_frames(wrfout_paths)
        selected_frames = select_wrfout_frames(frames, {"time_indices": [0, 1, 2]})
        output_path = runs_dir / "time-pressure-qvapor-real.png"
        figure_spec = {
            "figure_id": "time_pressure_qvapor_real",
            "view": {
                "x_axis": {"name": "time"},
                "y_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                "selectors": {
                    "south_north": {"mode": "index", "index": 0},
                    "west_east": {"mode": "index", "index": 0},
                },
            },
            "render": {"format": "png", "title": "Real Time-Pressure QVAPOR", "dpi": 120},
            "output": {
                "subdir": "",
                "file_stem": "time-pressure-qvapor-real",
                "sidecar_json": True,
                "overwrite": True,
                "path": output_path.as_posix(),
            },
            "layers": [
                {
                    "layer_id": "qvapor_cube_gkg",
                    "draw": {
                        "kind": "raster",
                        "alpha": 1.0,
                        "zorder": 10,
                        "style": {"colormap": "viridis", "show_colorbar": True},
                    },
                }
            ],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            artifacts = run_figure_request(
                figure_spec,
                build_layer_defs(),
                selected_frames,
                runs_dir,
                dry_run=False,
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(pcolormesh_warning_messages(caught), [])
        artifact = artifacts[0]
        self.assertIsNone(artifact["current_frame"])
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertTrue(Path(artifact["sidecar_path"]).exists())
        self.assertGreater(float(artifact["layer_summaries"]["qvapor_cube_gkg"]["mean"] or 0.0), 0.0)

        sidecar = load_json(Path(artifact["sidecar_path"]))
        self.assertEqual(sidecar["view"]["x_axis"]["name"], "time")
        self.assertEqual(sidecar["view"]["y_axis"]["name"], "pressure_hpa")
        self.assertEqual(sidecar["view"]["y_axis"]["units"], "hPa")
        self.assertEqual(len(sidecar["selected_frames"]), 3)


class ChartRenderingTests(unittest.TestCase):
    def _make_chart_frames(self, runs_dir: Path) -> list[dict[str, Any]]:
        wrfout_path = runs_dir / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00", "2024-07-20_02:00:00"],
            t2=[
                np.array([[300.0, 302.0, 304.0, 306.0], [308.0, 310.0, 312.0, 314.0]]),
                np.array([[301.0, 303.0, 305.0, 307.0], [309.0, 311.0, 313.0, 315.0]]),
                np.array([[302.0, 304.0, 306.0, 308.0], [310.0, 312.0, 314.0, 316.0]]),
            ],
            u10=[np.ones((2, 4)), np.ones((2, 4)), np.ones((2, 4))],
            v10=[np.ones((2, 4)), np.ones((2, 4)), np.ones((2, 4))],
            rainc=[np.zeros((2, 4)), np.zeros((2, 4)), np.zeros((2, 4))],
            rainnc=[np.zeros((2, 4)), np.zeros((2, 4)), np.zeros((2, 4))],
        )
        frames = enumerate_wrfout_frames([wrfout_path])
        return select_wrfout_frames(frames, {"time_indices": [0, 1, 2]})

    def _region_defs(self) -> dict[str, dict[str, Any]]:
        return {
            "west_box": {
                "label": "West Box",
                "south_north": {"mode": "index_range", "start": 0, "stop": 2},
                "west_east": {"mode": "index_range", "start": 0, "stop": 2},
            },
            "east_box": {
                "label": "East Box",
                "south_north": {"mode": "index_range", "start": 0, "stop": 2},
                "west_east": {"mode": "index_range", "start": 2, "stop": 4},
            },
        }

    def test_run_chart_request_supports_line_time_series(self) -> None:
        runs_dir = make_test_dir("_test_chart_line_time")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        selected_frames = self._make_chart_frames(runs_dir)
        chart_spec = {
            "chart_id": "west_mean_t2",
            "chart_kind": "line",
            "x": {"mode": "time", "label": "valid_time"},
            "render": {"format": "png", "title": "West Mean T2", "dpi": 120},
            "output": {"subdir": "", "file_stem": "west-mean-t2", "sidecar_json": True, "overwrite": True},
            "series": [
                {
                    "series_id": "west_mean",
                    "label": "West Mean T2",
                    "layer_id": "t2_c",
                    "region_id": "west_box",
                    "reduce": {"mode": "mean"},
                }
            ],
        }

        artifacts = run_chart_request(
            chart_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            region_defs=self._region_defs(),
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertEqual(artifact["chart_kind"], "line")
        values = [item["value"] for item in artifact["series"][0]["values"]]
        self.assertEqual([item["label"] for item in artifact["categories"]], [
            "2024-07-20_00:00:00",
            "2024-07-20_01:00:00",
            "2024-07-20_02:00:00",
        ])
        self.assertAlmostEqual(float(values[0]), 31.85, places=6)
        self.assertAlmostEqual(float(values[1]), 32.85, places=6)
        self.assertAlmostEqual(float(values[2]), 33.85, places=6)

    def test_run_chart_request_supports_grouped_bar_chart(self) -> None:
        runs_dir = make_test_dir("_test_chart_bar_group")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        selected_frames = self._make_chart_frames(runs_dir)
        chart_spec = {
            "chart_id": "grouped_t2",
            "chart_kind": "bar",
            "x": {"mode": "group", "group_ids": ["west_box", "east_box"], "label": "region"},
            "render": {"format": "png", "title": "Grouped T2", "dpi": 120},
            "output": {"subdir": "", "file_stem": "grouped-t2", "sidecar_json": True, "overwrite": True},
            "series": [
                {
                    "series_id": "group_mean",
                    "label": "Group Mean T2",
                    "layer_id": "t2_c",
                    "reduce": {"mode": "mean"},
                }
            ],
        }

        artifacts = run_chart_request(
            chart_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            region_defs=self._region_defs(),
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        values = [item["value"] for item in artifact["series"][0]["values"]]
        self.assertEqual([item["label"] for item in artifact["categories"]], ["West Box", "East Box"])
        self.assertAlmostEqual(float(values[0]), 33.85, places=6)
        self.assertAlmostEqual(float(values[1]), 37.85, places=6)

    def test_run_chart_request_supports_time_boxplot(self) -> None:
        runs_dir = make_test_dir("_test_chart_boxplot_time")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        selected_frames = self._make_chart_frames(runs_dir)
        chart_spec = {
            "chart_id": "time_box_t2",
            "chart_kind": "boxplot",
            "x": {"mode": "time", "label": "valid_time"},
            "render": {"format": "png", "title": "Time Boxplot T2", "dpi": 120},
            "output": {"subdir": "", "file_stem": "time-box-t2", "sidecar_json": True, "overwrite": True},
            "series": [
                {
                    "series_id": "west_dist",
                    "label": "West Distribution",
                    "layer_id": "t2_c",
                    "region_id": "west_box",
                }
            ],
        }

        artifacts = run_chart_request(
            chart_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            region_defs=self._region_defs(),
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        box = artifacts[0]["series"][0]["boxes"][0]
        self.assertEqual(int(box["count"]), 4)
        self.assertAlmostEqual(float(box["median"]), 31.85, places=6)
        self.assertAlmostEqual(float(box["mean"]), 31.85, places=6)

    def test_run_chart_request_supports_group_boxplot(self) -> None:
        runs_dir = make_test_dir("_test_chart_boxplot_group")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        selected_frames = self._make_chart_frames(runs_dir)
        chart_spec = {
            "chart_id": "group_box_t2",
            "chart_kind": "boxplot",
            "x": {"mode": "group", "group_ids": ["west_box", "east_box"], "label": "region"},
            "render": {"format": "png", "title": "Group Boxplot T2", "dpi": 120},
            "output": {"subdir": "", "file_stem": "group-box-t2", "sidecar_json": True, "overwrite": True},
            "series": [
                {
                    "series_id": "group_distribution",
                    "label": "Grouped Distribution",
                    "layer_id": "t2_c",
                    "reduce": {"mode": "mean"},
                }
            ],
        }

        artifacts = run_chart_request(
            chart_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            region_defs=self._region_defs(),
            dry_run=True,
        )

        self.assertEqual(len(artifacts), 1)
        west_box = artifacts[0]["series"][0]["boxes"][0]
        east_box = artifacts[0]["series"][0]["boxes"][1]
        self.assertEqual(int(west_box["count"]), 3)
        self.assertAlmostEqual(float(west_box["median"]), 32.85, places=6)
        self.assertAlmostEqual(float(east_box["median"]), 36.85, places=6)


class WrfPostProjectTests(unittest.TestCase):
    def test_run_postprocess_recovers_from_prior_post_failure(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_recover_after_failure")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "demo" / "wrf" / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.array([[300.0, 301.0], [302.0, 303.0]])],
            u10=[np.array([[1.0, 2.0], [3.0, 4.0]])],
            v10=[np.array([[2.0, 3.0], [4.0, 5.0]])],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            hgt=np.array([[100.0, 150.0], [200.0, 250.0]]),
        )
        project_root = create_project(runs_dir, "demo", [wrfout_path])

        failing_post_spec_path = project_root / "post_spec.fail.json"
        failing_post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "project_name": "demo",
                    "style_defs": build_style_defs(),
                    "layer_defs": build_layer_defs(),
                    "figures": [
                        {
                            "figure_id": "surface_temperature",
                            "selectors": {"time_indices": [0]},
                            "render": {"title": "Surface Temperature", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "surface-temperature",
                                "sidecar_json": True,
                                "overwrite": False,
                            },
                            "layers": [
                                {
                                    "layer_id": "t2_c",
                                    "style_id": "temperature_raster",
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        first_payload = run_postprocess("demo", runs_dir=runs_dir, post_spec_path=failing_post_spec_path)
        self.assertEqual(len(first_payload["artifacts"]), 1)

        with self.assertRaises(FileExistsError):
            run_postprocess("demo", runs_dir=runs_dir, post_spec_path=failing_post_spec_path)

        failed_state = load_json(project_root / "project.json")
        self.assertEqual(failed_state["status"], "failed")
        self.assertEqual(failed_state["current_step"], "wrf-post")
        self.assertEqual(failed_state["last_error"]["code"], "WRF_POST_FAILED")

        recovery_post_spec_path = project_root / "post_spec.recover.json"
        recovery_post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "project_name": "demo",
                    "style_defs": build_style_defs(),
                    "layer_defs": build_layer_defs(),
                    "figures": [
                        {
                            "figure_id": "surface_temperature_recover",
                            "selectors": {"time_indices": [0]},
                            "render": {"title": "Surface Temperature Recover", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "surface-temperature-recover",
                                "sidecar_json": True,
                                "overwrite": False,
                            },
                            "layers": [
                                {
                                    "layer_id": "t2_c",
                                    "style_id": "temperature_raster",
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        recovery_payload = run_postprocess("demo", runs_dir=runs_dir, post_spec_path=recovery_post_spec_path)

        self.assertEqual(len(recovery_payload["artifacts"]), 1)
        recovered_state = load_json(project_root / "project.json")
        self.assertEqual(recovered_state["status"], "completed")
        self.assertEqual(recovered_state["current_step"], "wrf-post")
        self.assertIsNone(recovered_state["last_error"])
        self.assertEqual(len(recovered_state["artifacts"]["plots"]), 2)

    def test_run_postprocess_refreshes_project_paths_and_discovers_local_wrfout(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_remote_portable_paths")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        project_root = runs_dir / "demo"
        wrfout_path = project_root / "wrf" / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00"],
            t2=[np.array([[300.0, 301.0], [302.0, 303.0]])],
            u10=[np.array([[1.0, 2.0], [3.0, 4.0]])],
            v10=[np.array([[2.0, 3.0], [4.0, 5.0]])],
            rainc=[np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2))],
            hgt=np.array([[100.0, 150.0], [200.0, 250.0]]),
        )

        state = create_project_state("demo", project_root)
        state["status"] = "completed"
        state["paths"]["project_root"] = "/tmp/stale/project"
        state["paths"]["wrf_dir"] = "/tmp/stale/project/wrf"
        state["paths"]["output_dir"] = "/tmp/stale/project/output"
        state["paths"]["log_dir"] = "/tmp/stale/project/logs"
        state["artifacts"]["wrfout_files"] = ["/tmp/stale/project/wrf/wrfout_d01_2024-07-20_00:00:00"]
        (project_root / "output").mkdir(parents=True, exist_ok=True)
        (project_root / "logs").mkdir(parents=True, exist_ok=True)
        save_project(state, project_root / "project.json")

        post_spec_path = project_root / "post_spec.json"
        post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "project_name": "demo",
                    "style_defs": build_style_defs(),
                    "layer_defs": build_layer_defs(),
                    "figures": [
                        {
                            "figure_id": "surface_temperature",
                            "selectors": {"time_indices": [0]},
                            "render": {"title": "Surface Temperature", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "surface-temperature",
                                "sidecar_json": True,
                                "overwrite": True,
                            },
                            "layers": [
                                {
                                    "layer_id": "t2_c",
                                    "style_id": "temperature_raster",
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = run_postprocess("demo", runs_dir=runs_dir, post_spec_path=post_spec_path)

        artifact = payload["artifacts"][0]
        refreshed_state = load_json(project_root / "project.json")
        self.assertTrue(Path(artifact["path"]).exists())
        self.assertEqual(refreshed_state["paths"]["project_root"], project_root.as_posix())
        self.assertEqual(refreshed_state["paths"]["wrf_dir"], (project_root / "wrf").as_posix())
        self.assertEqual(refreshed_state["paths"]["output_dir"], (project_root / "output").as_posix())
        self.assertEqual(refreshed_state["paths"]["log_dir"], (project_root / "logs").as_posix())
        self.assertEqual(refreshed_state["artifacts"]["plots"], [artifact["path"]])

    def test_run_postprocess_registers_current_figure_artifacts(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_v2_project")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "demo" / "wrf" / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[
                np.array([[300.0, 301.0], [302.0, 303.0]]),
                np.array([[304.0, 305.0], [306.0, 307.0]]),
            ],
            u10=[
                np.array([[1.0, 2.0], [3.0, 4.0]]),
                np.array([[2.0, 3.0], [4.0, 5.0]]),
            ],
            v10=[
                np.array([[2.0, 3.0], [4.0, 5.0]]),
                np.array([[3.0, 4.0], [5.0, 6.0]]),
            ],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.zeros((2, 2)), np.zeros((2, 2))],
            hgt=np.array([[100.0, 150.0], [200.0, 250.0]]),
        )
        project_root = create_project(runs_dir, "demo", [wrfout_path])

        post_spec_path = project_root / "post_spec.json"
        post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "project_name": "demo",
                    "style_defs": build_style_defs(),
                    "layer_defs": build_layer_defs(),
                    "figures": [
                        {
                            "figure_id": "surface_temperature",
                            "selectors": {"time_indices": [0, 1]},
                            "render": {"title": "Surface Temperature", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "surface-temperature",
                                "sidecar_json": True,
                                "overwrite": True,
                            },
                            "layers": [
                                {
                                    "layer_id": "t2_c",
                                    "style_id": "temperature_raster",
                                },
                                {
                                    "layer_id": "terrain",
                                    "style_id": "terrain_contours",
                                    "draw": {"alpha": 0.7},
                                },
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = run_postprocess("demo", runs_dir=runs_dir, post_spec_path=post_spec_path)

        self.assertEqual(len(payload["artifacts"]), 2)
        state = load_json(project_root / "project.json")
        self.assertEqual(len(state["artifacts"]["plots"]), 2)
        self.assertIsNone(state["last_error"])
        log_text = Path(payload["log_path"]).read_text(encoding="utf-8")
        self.assertIn("[figure 1] id=surface_temperature", log_text)
        for artifact in payload["artifacts"]:
            self.assertIsNotNone(artifact["current_frame"])
            self.assertTrue(Path(artifact["path"]).exists())
            self.assertTrue(Path(artifact["sidecar_path"]).exists())
            self.assertIn(artifact["path"], state["artifacts"]["plots"])
            self.assertIn(f"output={artifact['path']}", log_text)
            sidecar = load_json(Path(artifact["sidecar_path"]))
            self.assertEqual(sidecar["resolved_layers"][0]["style_id"], "temperature_raster")

    def test_run_postprocess_real_project_v3_smoke(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_real_v3_smoke")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        real_state = load_json(REAL_PROJECT_JSON)
        wrfout_paths = [
            resolve_repo_path(item)
            for item in real_state["artifacts"]["wrfout_files"]
        ]
        project_root = create_project(runs_dir, "real-smoke", wrfout_paths)

        post_spec_path = project_root / "post_spec.json"
        post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "project_name": "real-smoke",
                    "style_defs": build_style_defs(),
                    "layer_defs": build_layer_defs(),
                    "figures": [
                        {
                            "figure_id": "real_v3_smoke",
                            "selectors": {
                                "domain": "d01",
                                "time_indices": [0, 6],
                            },
                            "render": {"title": "Real V3 Smoke", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "real-v3-smoke",
                                "sidecar_json": True,
                                "overwrite": True,
                            },
                            "layers": [
                                {
                                    "layer_id": "landmask",
                                    "style_id": "landmask_fill",
                                    "draw": {"alpha": 0.4},
                                },
                                {
                                    "layer_id": "accum_precip",
                                    "style_id": "precip_raster",
                                },
                                {
                                    "layer_id": "terrain",
                                    "style_id": "terrain_contours",
                                    "draw": {"style": {"levels": [0, 500, 1000, 1500]}},
                                },
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = run_postprocess("real-smoke", runs_dir=runs_dir, post_spec_path=post_spec_path)

        self.assertEqual(len(payload["artifacts"]), 1)
        artifact = payload["artifacts"][0]
        plot_path = Path(artifact["path"])
        sidecar_path = Path(artifact["sidecar_path"])
        self.assertTrue(plot_path.exists())
        self.assertGreater(plot_path.stat().st_size, 0)
        self.assertTrue(sidecar_path.exists())

        sidecar = load_json(sidecar_path)
        self.assertEqual(sidecar["figure_id"], "real_v3_smoke")
        self.assertIsNone(sidecar["current_frame"])
        self.assertEqual(len(sidecar["selected_frames"]), 2)
        self.assertEqual(
            [layer["draw"]["kind"] for layer in sidecar["resolved_layers"]],
            ["categorical_fill", "raster", "contour"],
        )
        self.assertEqual(sidecar["resolved_layers"][0]["style_id"], "landmask_fill")

        state = load_json(project_root / "project.json")
        self.assertEqual(state["artifacts"]["plots"], [artifact["path"]])
        log_path = Path(payload["log_path"])
        self.assertTrue(log_path.exists())
        log_text = log_path.read_text(encoding="utf-8")
        self.assertIn("wrf-post project=real-smoke", log_text)
        self.assertIn("[figure 1] id=real_v3_smoke", log_text)
        self.assertIn(f"output={artifact['path']}", log_text)

    def test_run_postprocess_supports_mixed_figures_and_charts(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_figures_and_charts")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "demo" / "wrf" / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00", "2024-07-20_02:00:00"],
            t2=[
                np.array([[300.0, 302.0, 304.0, 306.0], [308.0, 310.0, 312.0, 314.0]]),
                np.array([[301.0, 303.0, 305.0, 307.0], [309.0, 311.0, 313.0, 315.0]]),
                np.array([[302.0, 304.0, 306.0, 308.0], [310.0, 312.0, 314.0, 316.0]]),
            ],
            u10=[np.ones((2, 4)), np.ones((2, 4)), np.ones((2, 4))],
            v10=[np.ones((2, 4)), np.ones((2, 4)), np.ones((2, 4))],
            rainc=[np.zeros((2, 4)), np.zeros((2, 4)), np.zeros((2, 4))],
            rainnc=[np.zeros((2, 4)), np.zeros((2, 4)), np.zeros((2, 4))],
            hgt=np.array([[100.0, 150.0, 200.0, 250.0], [120.0, 170.0, 220.0, 270.0]]),
        )
        project_root = create_project(runs_dir, "demo", [wrfout_path])

        post_spec_path = project_root / "post_spec.v4.json"
        post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 4,
                    "project_name": "demo",
                    "style_defs": build_style_defs(),
                    "layer_defs": build_layer_defs(),
                    "region_defs": {
                        "west_box": {
                            "label": "West Box",
                            "south_north": {"mode": "index_range", "start": 0, "stop": 2},
                            "west_east": {"mode": "index_range", "start": 0, "stop": 2},
                        },
                        "east_box": {
                            "label": "East Box",
                            "south_north": {"mode": "index_range", "start": 0, "stop": 2},
                            "west_east": {"mode": "index_range", "start": 2, "stop": 4},
                        },
                    },
                    "figures": [
                        {
                            "figure_id": "surface_temperature",
                            "selectors": {"time_indices": [2]},
                            "render": {"title": "Surface Temperature", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "surface-temperature",
                                "sidecar_json": True,
                                "overwrite": True,
                            },
                            "layers": [
                                {"layer_id": "t2_c", "style_id": "temperature_raster"},
                                {"layer_id": "terrain", "style_id": "terrain_contours"},
                            ],
                        }
                    ],
                    "charts": [
                        {
                            "chart_id": "grouped_t2",
                            "chart_kind": "bar",
                            "selectors": {"time_indices": [0, 1, 2]},
                            "x": {"mode": "group", "group_ids": ["west_box", "east_box"], "label": "region"},
                            "render": {"title": "Grouped T2", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "grouped-t2",
                                "sidecar_json": True,
                                "overwrite": True,
                            },
                            "series": [
                                {
                                    "series_id": "group_mean",
                                    "label": "Group Mean T2",
                                    "layer_id": "t2_c",
                                    "reduce": {"mode": "mean"},
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = run_postprocess("demo", runs_dir=runs_dir, post_spec_path=post_spec_path)

        self.assertEqual(len(payload["artifacts"]), 2)
        state = load_json(project_root / "project.json")
        self.assertEqual(len(state["artifacts"]["plots"]), 2)
        chart_artifact = next(item for item in payload["artifacts"] if item.get("chart_id") == "grouped_t2")
        self.assertTrue(Path(chart_artifact["path"]).exists())
        chart_sidecar = load_json(Path(chart_artifact["sidecar_path"]))
        self.assertEqual(chart_sidecar["chart_kind"], "bar")
        values = [item["value"] for item in chart_sidecar["series"][0]["values"]]
        self.assertAlmostEqual(float(values[0]), 33.85, places=6)
        self.assertAlmostEqual(float(values[1]), 37.85, places=6)
        log_text = Path(payload["log_path"]).read_text(encoding="utf-8")
        self.assertIn("[figure 1] id=surface_temperature", log_text)
        self.assertIn("[chart 1] id=grouped_t2", log_text)


if __name__ == "__main__":
    unittest.main()
