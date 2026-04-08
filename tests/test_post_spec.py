import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.post_spec import default_post_spec, normalize_post_spec, validate_post_spec

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
        self.assertIn("t2_c", payload["layer_defs"])
        self.assertIn("terrain", payload["layer_defs"])
        self.assertEqual(payload["figures"][0]["figure_id"], "surface_temperature")
        self.assertEqual(payload["figures"][0]["layers"][0]["layer_id"], "t2_c")

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


if __name__ == "__main__":
    unittest.main()
