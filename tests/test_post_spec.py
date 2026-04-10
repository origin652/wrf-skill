import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.post_spec import (
    default_post_spec,
    interpret_post_spec,
    normalize_post_spec,
    validate_post_spec,
)

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "post_spec.py"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class PostSpecTests(unittest.TestCase):
    def test_default_post_spec_uses_v2_layers_shape(self) -> None:
        payload = default_post_spec("demo")

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["project_name"], "demo")
        self.assertEqual(payload["defaults"]["render"]["format"], "png")
        self.assertIn("temperature_raster", payload["style_defs"])
        self.assertIn("wind_quiver", payload["style_defs"])
        self.assertEqual(payload["view_defs"], {})
        self.assertIn("t2_c", payload["layer_defs"])
        self.assertIn("terrain", payload["layer_defs"])
        self.assertIn("u10", payload["layer_defs"])
        self.assertIn("v10", payload["layer_defs"])
        self.assertEqual(payload["figures"][0]["figure_id"], "surface_temperature")
        self.assertEqual(payload["figures"][0]["layers"][0]["layer_id"], "t2_c")
        self.assertEqual(payload["figures"][0]["layers"][0]["style_id"], "temperature_raster")

    def test_normalize_merges_global_defaults_into_each_figure(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-a",
                "defaults": {
                    "render": {"dpi": 180},
                    "output": {"subdir": "diagnostics"},
                },
                "layer_defs": {
                    "custom_t2": {"expr": "T2 - 273.15", "units": "C"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "layers": [
                            {
                                "layer_id": "custom_t2",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(set(payload["layer_defs"]), {"custom_t2"})
        self.assertEqual(payload["figures"][0]["render"]["dpi"], 180)
        self.assertEqual(payload["figures"][0]["output"]["subdir"], "diagnostics")
        self.assertEqual(payload["figures"][0]["inputs"]["mode"], "project_artifacts")

    def test_normalize_resolves_style_defs_into_render_layers(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-style",
                "style_defs": {
                    "temp_base": {
                        "kind": "raster",
                        "alpha": 0.7,
                        "zorder": 12,
                        "style": {
                            "colormap": "magma",
                            "show_colorbar": True,
                        },
                    }
                },
                "layer_defs": {
                    "t2_c": {"expr": "T2 - 273.15", "units": "C"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "layers": [
                            {
                                "layer_id": "t2_c",
                                "style_id": "temp_base",
                                "draw": {
                                    "alpha": 0.9,
                                    "style": {"vmin": -20, "vmax": 45},
                                },
                            }
                        ],
                    }
                ],
            }
        )

        layer = payload["figures"][0]["layers"][0]
        self.assertEqual(layer["style_id"], "temp_base")
        self.assertEqual(layer["draw"]["kind"], "raster")
        self.assertEqual(layer["draw"]["alpha"], 0.9)
        self.assertEqual(layer["draw"]["zorder"], 12)
        self.assertEqual(layer["draw"]["style"]["colormap"], "magma")
        self.assertEqual(layer["draw"]["style"]["vmin"], -20)
        self.assertEqual(layer["draw"]["style"]["vmax"], 45)

    def test_validate_rejects_unknown_layer_reference(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-b",
                "layer_defs": {
                    "terrain": {"expr": "HGT", "units": "m"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "layers": [
                            {
                                "layer_id": "missing_layer",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("unknown layer_defs key" in error for error in errors))

    def test_validate_rejects_unknown_style_reference(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-style-missing",
                "layer_defs": {
                    "t2_c": {"expr": "T2 - 273.15", "units": "C"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "layers": [
                            {
                                "layer_id": "t2_c",
                                "style_id": "missing_style",
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("unknown style_defs key" in error for error in errors))

    def test_validate_rejects_incomplete_vector_render_layer(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-vector-missing-v",
                "style_defs": {
                    "wind_quiver": {
                        "kind": "vector",
                        "style": {"mode": "quiver", "stride": 2},
                    }
                },
                "layer_defs": {
                    "u10": {"expr": "U10", "units": "m s-1"},
                    "v10": {"expr": "V10", "units": "m s-1"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "layers": [
                            {
                                "u_layer_id": "u10",
                                "style_id": "wind_quiver",
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any(".v_layer_id must be a non-empty string" in error for error in errors))

    def test_validate_rejects_vector_layer_in_non_map_view(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-vector-section",
                "view_defs": {
                    "time_x": {
                        "x_axis": {"name": "time"},
                        "y_axis": {"name": "west_east"},
                        "selectors": {
                            "south_north": {"mode": "index", "index": 0},
                        },
                    }
                },
                "style_defs": {
                    "wind_quiver": {
                        "kind": "vector",
                        "style": {"mode": "quiver", "stride": 2},
                    }
                },
                "layer_defs": {
                    "u10": {"expr": "U10", "units": "m s-1"},
                    "v10": {"expr": "V10", "units": "m s-1"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "time_x",
                        "layers": [
                            {
                                "u_layer_id": "u10",
                                "v_layer_id": "v10",
                                "style_id": "wind_quiver",
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("only supported for map views" in error for error in errors))

    def test_validate_rejects_path_vector_layer_without_axis_projection(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-path-vector-missing-projection",
                "view_defs": {
                    "distance_height": {
                        "x_axis": {"kind": "path_coord", "name": "distance_km"},
                        "y_axis": {"name": "bottom_top"},
                        "selectors": {"time": {"mode": "current"}},
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 32,
                            }
                        },
                    }
                },
                "style_defs": {
                    "wind_quiver": {
                        "kind": "vector",
                        "style": {"mode": "quiver", "stride": 2},
                    }
                },
                "layer_defs": {
                    "u_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "U_PATH", "units": "m s-1"},
                    "v_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "V_PATH", "units": "m s-1"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "distance_height",
                        "layers": [
                            {
                                "u_layer_id": "u_path",
                                "v_layer_id": "v_path",
                                "style_id": "wind_quiver",
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("axis_projection is required for vector layers in path views" in error for error in errors))

    def test_validate_accepts_path_vector_layer_with_explicit_axis_projection(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-path-vector",
                "view_defs": {
                    "distance_height": {
                        "x_axis": {"kind": "path_coord", "name": "distance_km"},
                        "y_axis": {"name": "bottom_top"},
                        "selectors": {"time": {"mode": "current"}},
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 32,
                            }
                        },
                    }
                },
                "style_defs": {
                    "section_quiver": {
                        "kind": "vector",
                        "style": {
                            "mode": "quiver",
                            "stride": 2,
                            "axis_projection": {
                                "kind": "path_section",
                                "x_component": "path_tangent",
                                "y_component": "vertical",
                            },
                        },
                    }
                },
                "layer_defs": {
                    "u_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "U_PATH", "units": "m s-1"},
                    "v_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "V_PATH", "units": "m s-1"},
                    "w_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "W_PATH", "units": "m s-1"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "distance_height",
                        "layers": [
                            {
                                "u_layer_id": "u_path",
                                "v_layer_id": "v_path",
                                "vertical_layer_id": "w_path",
                                "style_id": "section_quiver",
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertEqual(errors, [])

    def test_validate_rejects_path_vector_vertical_component_without_vertical_layer(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-path-vector-missing-vertical",
                "view_defs": {
                    "distance_height": {
                        "x_axis": {"kind": "path_coord", "name": "distance_km"},
                        "y_axis": {"name": "bottom_top"},
                        "selectors": {"time": {"mode": "current"}},
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 32,
                            }
                        },
                    }
                },
                "style_defs": {
                    "section_quiver": {
                        "kind": "vector",
                        "style": {
                            "mode": "quiver",
                            "axis_projection": {
                                "kind": "path_section",
                                "x_component": "path_tangent",
                                "y_component": "vertical",
                            },
                        },
                    }
                },
                "layer_defs": {
                    "u_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "U_PATH", "units": "m s-1"},
                    "v_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "V_PATH", "units": "m s-1"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "distance_height",
                        "layers": [
                            {
                                "u_layer_id": "u_path",
                                "v_layer_id": "v_path",
                                "style_id": "section_quiver",
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any(".vertical_layer_id must be a non-empty string" in error for error in errors))

    def test_validate_accepts_distance_height_path_view(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-distance-height",
                "view_defs": {
                    "distance_height": {
                        "x_axis": {"kind": "path_coord", "name": "distance_km"},
                        "y_axis": {"kind": "derived_coord", "name": "height_m"},
                        "selectors": {
                            "time": {"mode": "current"},
                        },
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 50,
                            }
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "distance_height",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertEqual(errors, [])

    def test_validate_accepts_distance_pressure_path_view(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-distance-pressure",
                "view_defs": {
                    "distance_pressure": {
                        "x_axis": {"kind": "path_coord", "name": "distance_km"},
                        "y_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                        "selectors": {
                            "time": {"mode": "current"},
                        },
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 50,
                            }
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "distance_pressure",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertEqual(errors, [])

    def test_validate_accepts_height_distance_path_view(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-height-distance",
                "view_defs": {
                    "height_distance": {
                        "x_axis": {"kind": "derived_coord", "name": "height_m"},
                        "y_axis": {"kind": "path_coord", "name": "distance_km"},
                        "selectors": {
                            "time": {"mode": "current"},
                        },
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 50,
                            }
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "height_distance",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertEqual(errors, [])

    def test_validate_accepts_pressure_distance_path_view(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-pressure-distance",
                "view_defs": {
                    "pressure_distance": {
                        "x_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                        "y_axis": {"kind": "path_coord", "name": "distance_km"},
                        "selectors": {
                            "time": {"mode": "current"},
                        },
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 50,
                            }
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "pressure_distance",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertEqual(errors, [])

    def test_validate_accepts_richer_selector_modes(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-rich-selectors",
                "view_defs": {
                    "time_x_mean": {
                        "x_axis": {"name": "time"},
                        "y_axis": {"name": "west_east"},
                        "selectors": {
                            "south_north": {"mode": "mean"},
                            "bottom_top": {"mode": "nearest_value", "value": 850.0},
                        },
                    },
                    "map_nearest_index": {
                        "x_axis": {"name": "west_east"},
                        "y_axis": {"name": "south_north"},
                        "selectors": {
                            "time": {"mode": "nearest_index", "index": 1.6},
                        },
                    },
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "time_x_mean",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertEqual(errors, [])

    def test_validate_accepts_height_time_view(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-height-time",
                "view_defs": {
                    "height_time": {
                        "x_axis": {"kind": "derived_coord", "name": "height_m"},
                        "y_axis": {"name": "time"},
                        "selectors": {
                            "south_north": {"mode": "index", "index": 0},
                            "west_east": {"mode": "index", "index": 1},
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "height_time",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertEqual(errors, [])

    def test_validate_rejects_nearest_value_without_numeric_value(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-bad-selector",
                "view_defs": {
                    "time_x": {
                        "x_axis": {"name": "time"},
                        "y_axis": {"name": "west_east"},
                        "selectors": {
                            "south_north": {"mode": "nearest_value", "value": "bad"},
                        },
                    }
                },
                "layer_defs": {
                    "t2_c": {"expr": "T2 - 273.15", "units": "C"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "time_x",
                        "layers": [
                            {
                                "layer_id": "t2_c",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any(".value must be numeric for mode=nearest_value" in error for error in errors))

    def test_validate_rejects_derived_vertical_view_without_time_axis(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-bad-derived-view",
                "view_defs": {
                    "height_x": {
                        "x_axis": {"kind": "derived_coord", "name": "height_m"},
                        "y_axis": {"name": "west_east"},
                        "selectors": {
                            "south_north": {"mode": "index", "index": 0},
                            "time": {"mode": "current"},
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "height_x",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("derived_coord views to use time with height_m or pressure_hpa" in error for error in errors))

    def test_normalize_distance_height_path_view_sets_axis_defaults(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-distance-height-defaults",
                "view_defs": {
                    "distance_height": {
                        "x_axis": {"kind": "path_coord", "name": "distance_km"},
                        "y_axis": {"kind": "derived_coord", "name": "height_m"},
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 50,
                            }
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "distance_height",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        view = payload["view_defs"]["distance_height"]
        self.assertEqual(view["x_axis"]["kind"], "path_coord")
        self.assertEqual(view["x_axis"]["label"], "distance_km")
        self.assertEqual(view["x_axis"]["units"], "km")
        self.assertEqual(view["y_axis"]["kind"], "derived_coord")
        self.assertEqual(view["y_axis"]["label"], "height_m")
        self.assertEqual(view["y_axis"]["units"], "m")

    def test_normalize_height_distance_path_view_sets_axis_defaults(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-height-distance-defaults",
                "view_defs": {
                    "height_distance": {
                        "x_axis": {"kind": "derived_coord", "name": "height_m"},
                        "y_axis": {"kind": "path_coord", "name": "distance_km"},
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 50,
                            }
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "height_distance",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        view = payload["view_defs"]["height_distance"]
        self.assertEqual(view["x_axis"]["kind"], "derived_coord")
        self.assertEqual(view["x_axis"]["label"], "height_m")
        self.assertEqual(view["x_axis"]["units"], "m")
        self.assertEqual(view["y_axis"]["kind"], "path_coord")
        self.assertEqual(view["y_axis"]["label"], "distance_km")
        self.assertEqual(view["y_axis"]["units"], "km")

    def test_normalize_pressure_distance_path_view_sets_axis_defaults(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-pressure-distance-defaults",
                "view_defs": {
                    "pressure_distance": {
                        "x_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
                        "y_axis": {"kind": "path_coord", "name": "distance_km"},
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 50,
                            }
                        },
                    }
                },
                "layer_defs": {
                    "qvapor_cube_gkg": {
                        "source": {"kind": "wrf_native_3d_full"},
                        "expr": "QVAPOR * 1000",
                        "units": "g kg-1",
                    }
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "view_id": "pressure_distance",
                        "layers": [
                            {
                                "layer_id": "qvapor_cube_gkg",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        view = payload["view_defs"]["pressure_distance"]
        self.assertEqual(view["x_axis"]["kind"], "derived_coord")
        self.assertEqual(view["x_axis"]["label"], "pressure_hpa")
        self.assertEqual(view["x_axis"]["units"], "hPa")
        self.assertEqual(view["y_axis"]["kind"], "path_coord")
        self.assertEqual(view["y_axis"]["label"], "distance_km")
        self.assertEqual(view["y_axis"]["units"], "km")

    def test_validate_rejects_wrf_native_3d_without_level_selector(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-3d-missing-level",
                "layer_defs": {
                    "qvapor_lvl1": {
                        "source": {"kind": "wrf_native_3d"},
                        "expr": "QVAPOR",
                        "units": "kg kg-1",
                    },
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "layers": [
                            {
                                "layer_id": "qvapor_lvl1",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("level_selector" in error for error in errors))

    def test_validate_rejects_invalid_explicit_paths_configuration(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-c",
                "layer_defs": {
                    "t2_c": {"expr": "T2 - 273.15", "units": "C"},
                },
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "inputs": {"mode": "explicit_paths", "paths": []},
                        "layers": [
                            {
                                "layer_id": "t2_c",
                                "draw": {"kind": "raster", "style": {}},
                            }
                        ],
                    }
                ],
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("explicit_paths" in error for error in errors))

    def test_cli_writes_normalized_v2_spec(self) -> None:
        runs_dir = make_test_dir("_test_post_spec_cli")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        input_path = runs_dir / "post_spec.in.json"
        output_path = runs_dir / "post_spec.out.json"
        input_path.write_text(
            json.dumps(
                {
                    "project_name": "case-d",
                    "defaults": {"render": {"dpi": 240}},
                    "layer_defs": {
                        "wind10m": {"expr": "sqrt(U10**2 + V10**2)", "units": "m s-1"},
                    },
                    "figures": [
                        {
                            "figure_id": "surface-wind",
                            "layers": [
                                {
                                    "layer_id": "wind10m",
                                    "draw": {
                                        "kind": "raster",
                                        "style": {"colormap": "viridis"},
                                    },
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

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["figures"][0]["render"]["dpi"], 240)
        self.assertEqual(payload["figures"][0]["layers"][0]["layer_id"], "wind10m")

    def test_interpret_resolves_vector_render_layer(self) -> None:
        payload = interpret_post_spec(
            {
                "project_name": "case-vector",
                "style_defs": {
                    "wind_quiver": {
                        "kind": "vector",
                        "alpha": 0.85,
                        "style": {"mode": "quiver", "stride": 3, "scale": 50},
                    }
                },
                "layer_defs": {
                    "u10": {"expr": "U10", "units": "m s-1"},
                    "v10": {"expr": "V10", "units": "m s-1"},
                },
                "figures": [
                    {
                        "figure_id": "surface-wind-vectors",
                        "layers": [
                            {
                                "u_layer_id": "u10",
                                "v_layer_id": "v10",
                                "style_id": "wind_quiver",
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(payload["project_name"], "case-vector")
        figure = payload["figures"][0]
        self.assertEqual(figure["figure_id"], "surface-wind-vectors")
        self.assertEqual(figure["output_mode"], "per_frame")
        self.assertEqual(figure["render_layer_order"], ["u10", "v10"])
        resolved = figure["resolved_layers"][0]
        self.assertEqual(resolved["u_layer_id"], "u10")
        self.assertEqual(resolved["v_layer_id"], "v10")
        self.assertEqual(resolved["draw"]["kind"], "vector")
        self.assertEqual(resolved["draw"]["style"]["mode"], "quiver")
        self.assertTrue(resolved["uses_current"])

    def test_interpret_resolves_path_section_vector_render_layer(self) -> None:
        payload = interpret_post_spec(
            {
                "project_name": "case-path-vector",
                "view_defs": {
                    "distance_height": {
                        "x_axis": {"kind": "path_coord", "name": "distance_km"},
                        "y_axis": {"name": "bottom_top"},
                        "selectors": {"time": {"mode": "current"}},
                        "sampling": {
                            "path": {
                                "kind": "polyline",
                                "points": [
                                    {"lat": 31.20, "lon": 121.40},
                                    {"lat": 31.80, "lon": 122.10},
                                ],
                                "samples": 32,
                            }
                        },
                    }
                },
                "style_defs": {
                    "section_quiver": {
                        "kind": "vector",
                        "style": {
                            "mode": "quiver",
                            "axis_projection": {
                                "kind": "path_section",
                                "x_component": "path_normal",
                                "y_component": "vertical",
                            },
                        },
                    }
                },
                "layer_defs": {
                    "u_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "U_PATH", "units": "m s-1"},
                    "v_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "V_PATH", "units": "m s-1"},
                    "w_path": {"source": {"kind": "wrf_native_3d_full"}, "expr": "W_PATH", "units": "m s-1"},
                },
                "figures": [
                    {
                        "figure_id": "path-section-vectors",
                        "view_id": "distance_height",
                        "layers": [
                            {
                                "u_layer_id": "u_path",
                                "v_layer_id": "v_path",
                                "vertical_layer_id": "w_path",
                                "style_id": "section_quiver",
                            }
                        ],
                    }
                ],
            }
        )

        figure = payload["figures"][0]
        self.assertEqual(figure["render_layer_order"], ["u_path", "v_path", "w_path"])
        resolved = figure["resolved_layers"][0]
        self.assertEqual(resolved["vertical_layer_id"], "w_path")
        self.assertEqual(resolved["draw"]["style"]["axis_projection"]["kind"], "path_section")
        self.assertEqual(resolved["draw"]["style"]["axis_projection"]["x_component"], "path_normal")
        self.assertEqual(resolved["draw"]["style"]["axis_projection"]["y_component"], "vertical")
        self.assertTrue(resolved["uses_current"])

    def test_interpret_time_axis_view_uses_frame_range_output_mode(self) -> None:
        payload = interpret_post_spec(
            {
                "project_name": "case-time-axis",
                "view_defs": {
                    "time_x": {
                        "x_axis": {"name": "time"},
                        "y_axis": {"name": "west_east"},
                        "selectors": {
                            "south_north": {"mode": "index", "index": 0},
                        },
                    }
                },
                "style_defs": {
                    "temp_style": {
                        "kind": "raster",
                        "style": {"colormap": "viridis", "show_colorbar": False},
                    }
                },
                "layer_defs": {
                    "t2_c": {"expr": "T2 - 273.15", "units": "C"},
                },
                "figures": [
                    {
                        "figure_id": "time-x",
                        "view_id": "time_x",
                        "layers": [
                            {
                                "layer_id": "t2_c",
                                "style_id": "temp_style",
                            }
                        ],
                    }
                ],
            }
        )

        figure = payload["figures"][0]
        self.assertEqual(figure["output_mode"], "frame_range")
        self.assertEqual(figure["view"]["x_axis"]["name"], "time")
        self.assertEqual(figure["view"]["y_axis"]["name"], "west_east")

    def test_cli_interpret_outputs_execution_plan(self) -> None:
        runs_dir = make_test_dir("_test_post_spec_interpret_cli")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        input_path = runs_dir / "post_spec.in.json"
        input_path.write_text(
            json.dumps(
                {
                    "project_name": "case-e",
                    "style_defs": {
                        "temp_style": {
                            "kind": "raster",
                            "style": {"colormap": "viridis", "show_colorbar": True},
                        },
                        "terrain_style": {
                            "kind": "contour",
                            "style": {"levels": [0, 500, 1000], "colors": "black"},
                        },
                    },
                    "layer_defs": {
                        "terrain": {"expr": "first(HGT)", "units": "m"},
                        "t2_c": {"expr": "T2 - 273.15", "units": "C"},
                    },
                    "figures": [
                        {
                            "figure_id": "surface-temperature",
                            "layers": [
                                {"layer_id": "t2_c", "style_id": "temp_style"},
                                {"layer_id": "terrain", "style_id": "terrain_style"},
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--input",
                str(input_path),
                "--interpret",
                "--figure-id",
                "surface-temperature",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["project_name"], "case-e")
        self.assertIn("temp_style", payload["style_defs"])
        self.assertEqual(len(payload["figures"]), 1)
        figure = payload["figures"][0]
        self.assertEqual(figure["figure_id"], "surface-temperature")
        self.assertEqual(figure["output_mode"], "per_frame")
        self.assertEqual(figure["render_layer_order"], ["t2_c", "terrain"])
        self.assertEqual(figure["resolved_layers"][0]["style_id"], "temp_style")
        self.assertEqual(figure["resolved_layers"][0]["draw"]["kind"], "raster")
        self.assertEqual(figure["resolved_layers"][0]["source"]["kind"], "wrf_native")
        self.assertFalse(figure["resolved_layers"][1]["uses_current"])


if __name__ == "__main__":
    unittest.main()
