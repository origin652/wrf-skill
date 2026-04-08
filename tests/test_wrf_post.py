import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from scripts.project_state import create_project_state, save_project
from scripts.wrf_post import run_postprocess

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"
PLOT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "plot_wrfout.py"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_times(dataset: Dataset, times: list[str]) -> None:
    times_var = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
    encoded = np.empty((len(times), 19), dtype="S1")
    for index, token in enumerate(times):
        encoded[index] = np.array(list(token.encode("ascii")), dtype="S1")
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
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = np.asarray(t2[0], dtype=float)
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

        write_field("T2", t2, "K")
        write_field("U10", u10, "m s-1")
        write_field("V10", v10, "m s-1")
        write_field("RAINC", rainc, "mm")
        write_field("RAINNC", rainnc, "mm")


class WrfPostTests(unittest.TestCase):
    def test_plot_wrfout_legacy_cli_creates_png_and_sidecar(self) -> None:
        runs_dir = make_test_dir("_test_plot_wrfout_cli")
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
        )

        output_path = runs_dir / "t2.png"
        completed = subprocess.run(
            [
                sys.executable,
                str(PLOT_SCRIPT),
                "--wrfout",
                str(wrfout_path),
                "--product",
                "t2",
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
        sidecar = output_path.with_suffix(".json")
        self.assertTrue(sidecar.exists())
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(payload["product"], "t2")
        self.assertEqual(payload["units"], "C")

    def _create_project(self, runs_dir: Path, wrfout_paths: list[Path]) -> Path:
        project_root = runs_dir / "demo"
        state = create_project_state("demo", project_root)
        Path(state["paths"]["output_dir"]).mkdir(parents=True, exist_ok=True)
        Path(state["paths"]["log_dir"]).mkdir(parents=True, exist_ok=True)
        Path(state["paths"]["wrf_dir"]).mkdir(parents=True, exist_ok=True)
        state["artifacts"]["wrfout_files"] = [path.as_posix() for path in wrfout_paths]
        save_project(state, project_root / "project.json")
        return project_root

    def test_run_postprocess_registers_t2_and_wind10m_plots(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_scalar_products")
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
        )
        project_root = self._create_project(runs_dir, [wrfout_path])

        post_spec_path = project_root / "post_spec.json"
        post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project_name": "demo",
                    "products": [
                        {
                            "product": "t2",
                            "selectors": {"time_indices": [0]},
                            "output": {"file_stem": "surface-temp"},
                        },
                        {
                            "product": "wind10m",
                            "selectors": {"time_indices": [1]},
                            "output": {"file_stem": "surface-wind"},
                        },
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = run_postprocess("demo", runs_dir=runs_dir, post_spec_path=post_spec_path)

        self.assertEqual(len(payload["artifacts"]), 2)
        state = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["artifacts"]["plots"]), 2)
        for artifact in payload["artifacts"]:
            path = Path(artifact["path"])
            self.assertTrue(path.exists())
            self.assertTrue(Path(artifact["sidecar_path"]).exists())
            self.assertIn(path.as_posix(), state["artifacts"]["plots"])

    def test_run_postprocess_accumulated_precipitation_tracks_range(self) -> None:
        runs_dir = make_test_dir("_test_wrf_post_precip")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        wrfout_path = runs_dir / "demo" / "wrf" / "wrfout_d01_2024-07-20_00:00:00"
        write_wrfout_netcdf(
            wrfout_path,
            times=["2024-07-20_00:00:00", "2024-07-20_01:00:00"],
            t2=[np.full((2, 2), 300.0), np.full((2, 2), 301.0)],
            u10=[np.ones((2, 2)), np.ones((2, 2))],
            v10=[np.ones((2, 2)), np.ones((2, 2))],
            rainc=[np.zeros((2, 2)), np.zeros((2, 2))],
            rainnc=[np.full((2, 2), 1.0), np.full((2, 2), 5.0)],
        )
        project_root = self._create_project(runs_dir, [wrfout_path])

        post_spec_path = project_root / "post_spec.json"
        post_spec_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project_name": "demo",
                    "products": [
                        {
                            "product": "accumulated_precipitation",
                            "selectors": {"time_indices": [0, 1]},
                            "output": {"file_stem": "accum-rain"},
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = run_postprocess("demo", runs_dir=runs_dir, post_spec_path=post_spec_path)

        self.assertEqual(len(payload["artifacts"]), 1)
        artifact = payload["artifacts"][0]
        self.assertTrue(Path(artifact["path"]).exists())
        sidecar = json.loads(Path(artifact["sidecar_path"]).read_text(encoding="utf-8"))
        self.assertAlmostEqual(sidecar["summary"]["mean"], 4.0)
        self.assertEqual(len(sidecar["selected_frames"]), 2)


if __name__ == "__main__":
    unittest.main()
