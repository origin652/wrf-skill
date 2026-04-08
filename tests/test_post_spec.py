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
    def test_default_post_spec_uses_canonical_shape(self) -> None:
        payload = default_post_spec("demo")

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["project_name"], "demo")
        self.assertEqual(payload["defaults"]["render"]["format"], "png")
        self.assertEqual(payload["products"][0]["product"], "t2")
        self.assertEqual(payload["products"][0]["output"]["subdir"], "plots")

    def test_normalize_accepts_single_product_shorthand(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-a",
                "product": "wind10m",
                "render": {"dpi": 220},
            }
        )

        self.assertEqual(payload["project_name"], "case-a")
        self.assertEqual(len(payload["products"]), 1)
        self.assertEqual(payload["products"][0]["product"], "wind10m")
        self.assertEqual(payload["products"][0]["render"]["dpi"], 220)
        self.assertEqual(payload["products"][0]["inputs"]["mode"], "project_artifacts")

    def test_normalize_merges_global_defaults_into_each_product(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-b",
                "defaults": {
                    "render": {"dpi": 180},
                    "output": {"subdir": "diagnostics"},
                },
                "products": [
                    {"product": "t2"},
                    {"product": "storm_track", "render": {"format": "json"}},
                ],
            }
        )

        self.assertEqual(payload["products"][0]["render"]["dpi"], 180)
        self.assertEqual(payload["products"][0]["output"]["subdir"], "diagnostics")
        self.assertEqual(payload["products"][1]["render"]["format"], "json")
        self.assertEqual(payload["products"][1]["render"]["dpi"], 180)
        self.assertEqual(payload["products"][1]["output"]["subdir"], "diagnostics")

    def test_validate_rejects_invalid_explicit_paths_configuration(self) -> None:
        payload = normalize_post_spec(
            {
                "project_name": "case-c",
                "product": "t2",
                "inputs": {"mode": "explicit_paths", "paths": []},
            }
        )

        errors = validate_post_spec(payload)

        self.assertTrue(any("explicit_paths" in error for error in errors))

    def test_cli_writes_normalized_spec(self) -> None:
        runs_dir = make_test_dir("_test_post_spec_cli")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        input_path = runs_dir / "post_spec.in.json"
        output_path = runs_dir / "post_spec.out.json"
        input_path.write_text(
            json.dumps(
                {
                    "project_name": "case-d",
                    "product": "accumulated_precipitation",
                    "render": {"dpi": 240},
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
        self.assertEqual(payload["products"][0]["product"], "accumulated_precipitation")
        self.assertEqual(payload["products"][0]["render"]["dpi"], 240)


if __name__ == "__main__":
    unittest.main()
