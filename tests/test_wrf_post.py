import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from scripts.plot_wrfout import (
    BinaryOpNode,
    CallNode,
    LayerResolutionError,
    NameNode,
    NumberNode,
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
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = np.asarray(t2[0], dtype=float)
    terrain = np.asarray(hgt if hgt is not None else np.zeros_like(sample), dtype=float)
    mask = np.asarray(landmask if landmask is not None else np.ones_like(sample), dtype=float)

    with Dataset(path, "w", format="NETCDF4") as dataset:
        dataset.createDimension("Time", len(times))
        dataset.createDimension("south_north", sample.shape[0])
        dataset.createDimension("west_east", sample.shape[1])
        dataset.createDimension("DateStrLen", 19)
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

        write_field("T2", t2, "K")
        write_field("U10", u10, "m s-1")
        write_field("V10", v10, "m s-1")
        write_field("RAINC", rainc, "mm")
        write_field("RAINNC", rainnc, "mm")
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
        "accum_precip": {
            "source": {"kind": "wrf_native"},
            "expr": "last(RAINC + RAINNC) - first(RAINC + RAINNC)",
            "units": "mm",
            "metadata": {"description": "Accumulated precipitation"},
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

        artifacts = run_figure_request(
            figure_spec,
            build_layer_defs(),
            selected_frames,
            runs_dir,
            dry_run=False,
        )

        self.assertEqual(len(artifacts), 1)
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


class WrfPostProjectTests(unittest.TestCase):
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
                    "schema_version": 2,
                    "project_name": "demo",
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
                                    "draw": {
                                        "kind": "raster",
                                        "alpha": 1.0,
                                        "zorder": 10,
                                        "style": {"colormap": "coolwarm", "show_colorbar": True},
                                    },
                                },
                                {
                                    "layer_id": "terrain",
                                    "draw": {
                                        "kind": "contour",
                                        "alpha": 0.7,
                                        "zorder": 20,
                                        "style": {
                                            "levels": [100, 200],
                                            "colors": "black",
                                            "linewidths": 0.5,
                                        },
                                    },
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

    def test_run_postprocess_real_project_v2_smoke(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_real_v2_smoke")
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
                    "schema_version": 2,
                    "project_name": "real-smoke",
                    "layer_defs": build_layer_defs(),
                    "figures": [
                        {
                            "figure_id": "real_v2_smoke",
                            "selectors": {
                                "domain": "d01",
                                "time_indices": [0, 6],
                            },
                            "render": {"title": "Real V2 Smoke", "dpi": 120},
                            "output": {
                                "subdir": "plots",
                                "file_stem": "real-v2-smoke",
                                "sidecar_json": True,
                                "overwrite": True,
                            },
                            "layers": [
                                {
                                    "layer_id": "landmask",
                                    "draw": {
                                        "kind": "categorical_fill",
                                        "alpha": 0.4,
                                        "zorder": 1,
                                        "style": {
                                            "categories": [
                                                {"value": 0, "color": "#4c78a8", "label": "water"},
                                                {"value": 1, "color": "#f2cf5b", "label": "land"},
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
                                        "style": {
                                            "levels": [0, 500, 1000, 1500],
                                            "colors": "black",
                                            "linewidths": 0.5,
                                        },
                                    },
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
        self.assertEqual(sidecar["figure_id"], "real_v2_smoke")
        self.assertIsNone(sidecar["current_frame"])
        self.assertEqual(len(sidecar["selected_frames"]), 2)
        self.assertEqual(
            [layer["draw"]["kind"] for layer in sidecar["resolved_layers"]],
            ["categorical_fill", "raster", "contour"],
        )

        state = load_json(project_root / "project.json")
        self.assertEqual(state["artifacts"]["plots"], [artifact["path"]])
        log_path = Path(payload["log_path"])
        self.assertTrue(log_path.exists())
        log_text = log_path.read_text(encoding="utf-8")
        self.assertIn("wrf-post project=real-smoke", log_text)
        self.assertIn("[figure 1] id=real_v2_smoke", log_text)
        self.assertIn(f"output={artifact['path']}", log_text)


if __name__ == "__main__":
    unittest.main()
