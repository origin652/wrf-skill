import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.namelist_parser import read_namelist
from scripts.namelist_to_spec import improve_namelists, spec_from_namelists
from scripts.project_state import load_project, save_project, seed_project
from scripts.render_config import render_from_spec, validate_spec, write_rendered_files, write_rendered_targets


def sample_spec() -> dict:
    return {
        "project_name": "demo",
        "data_source": "gfs",
        "start_time": "2024-07-20_00:00:00",
        "end_time": "2024-07-20_06:00:00",
        "run_mode": "local",
        "domains": [
            {
                "name": "d01",
                "parent_id": 1,
                "parent_grid_ratio": 1,
                "dx_km": 27,
                "dy_km": 27,
                "e_we": 100,
                "e_sn": 100,
                "i_parent_start": 1,
                "j_parent_start": 1,
                "ref_lat": 31.2,
                "ref_lon": 121.5,
            },
            {
                "name": "d02",
                "parent_id": 1,
                "parent_grid_ratio": 3,
                "dx_km": 9,
                "dy_km": 9,
                "e_we": 151,
                "e_sn": 151,
                "i_parent_start": 30,
                "j_parent_start": 30,
                "ref_lat": 31.2,
                "ref_lon": 121.5,
            },
        ],
        "physics": {
            "mp_physics": 6,
            "cu_physics": 1,
            "ra_lw_physics": 4,
            "ra_sw_physics": 4,
            "bl_pbl_physics": 1,
            "sf_sfclay_physics": 1,
            "sf_surface_physics": 2,
        },
    }


class NamelistToSpecTests(unittest.TestCase):
    def test_imported_spec_exposes_structured_fields_and_raw_namelists(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG")

        imported = spec_from_namelists(
            namelist_input=rendered["namelist.input"],
            namelist_wps=rendered["namelist.wps"],
            project_name="legacy_case",
        )

        self.assertEqual(validate_spec(imported), [])
        self.assertEqual(imported["schema_version"], 2)
        self.assertEqual(imported["project_name"], "legacy_case")
        self.assertEqual(imported["data_source"], "gfs")
        self.assertEqual(imported["timing"]["start_time"], "2024-07-20_00:00:00")
        self.assertEqual(imported["timing"]["end_time"], "2024-07-20_06:00:00")
        self.assertEqual(len(imported["domains"]), 2)
        self.assertEqual(imported["domains"][1]["dx_km"], 9)
        self.assertEqual(imported["domains"][1]["e_we"], 151)
        self.assertEqual(imported["physics"]["mp_physics"], 6)
        self.assertEqual(imported["experimental"]["raw_namelist_input"], {})
        self.assertEqual(imported["experimental"]["raw_namelist_wps"], {})
        self.assertIn("time_control", imported["experimental"]["imported_namelist_input"])
        self.assertIn("share", imported["experimental"]["imported_namelist_wps"])
        diagnostics = imported["experimental"]["import_diagnostics"]
        self.assertEqual(diagnostics["sources"]["timing.start_time"], "namelist.input")
        self.assertEqual(diagnostics["sources"]["data_source"], "namelist.wps.ungrib.prefix")

    def test_domain_specific_physics_becomes_domain_override(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG")
        rendered["namelist.input"]["physics"]["cu_physics"] = [1, 0]

        imported = spec_from_namelists(
            namelist_input=rendered["namelist.input"],
            namelist_wps=rendered["namelist.wps"],
        )

        self.assertEqual(imported["physics"]["cu_physics"], 1)
        self.assertEqual(imported["domains"][1]["physics"]["cu_physics"], 0)

    def test_improve_namelists_writes_updated_rendered_files(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG")
        tmp_root = Path(tempfile.mkdtemp(prefix="wrf-improve-namelists-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        source_dir = tmp_root / "source"
        out_dir = tmp_root / "improved"
        spec_out = tmp_root / "simulation_spec.json"
        write_rendered_files(rendered, source_dir)

        payload = improve_namelists(
            namelist_input_path=source_dir / "namelist.input",
            namelist_wps_path=source_dir / "namelist.wps",
            project_name="legacy_case",
            override_entries=["timing.history_interval_minutes=30"],
            namelist_override_entries=["dynamics.w_damping=1"],
            out_dir=out_dir,
            spec_out=spec_out,
            dry_run=False,
        )

        improved_input = read_namelist(out_dir / "namelist.input")
        self.assertEqual(improved_input["time_control"]["history_interval"], [30, 30])
        self.assertEqual(improved_input["dynamics"]["w_damping"], 1)
        self.assertTrue((out_dir / "namelist.wps").exists())
        self.assertTrue(spec_out.exists())
        self.assertEqual(len(payload["written"]), 3)
        changed_paths = {change["path"] for change in payload["diff"]}
        self.assertIn("namelist.input.time_control.history_interval", changed_paths)
        self.assertIn("namelist.input.dynamics.w_damping", changed_paths)

    def test_improve_namelists_dry_run_does_not_require_output_paths(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG")
        tmp_root = Path(tempfile.mkdtemp(prefix="wrf-improve-namelists-dry-run-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        write_rendered_files(rendered, tmp_root)

        payload = improve_namelists(
            namelist_input_path=tmp_root / "namelist.input",
            namelist_wps_path=tmp_root / "namelist.wps",
            override_entries=["physics.cu_physics=0"],
            dry_run=True,
        )

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["simulation_spec"]["physics"]["cu_physics"], 0)
        self.assertEqual(payload["rendered"]["namelist.input"]["physics"]["cu_physics"], [0, 0])
        self.assertTrue(payload["diff"])

    def test_improve_namelists_project_mode_updates_state_and_artifacts(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG")
        runs_dir = Path(tempfile.mkdtemp(prefix="wrf-improve-project-"))
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        seed_project("demo", runs_dir, dry_run=False)
        project_root = runs_dir / "demo"
        write_rendered_targets(
            rendered,
            {
                "namelist.input": project_root / "wrf" / "namelist.input",
                "namelist.wps": project_root / "wps" / "namelist.wps",
            },
        )

        payload = improve_namelists(
            namelist_input_path=None,
            namelist_wps_path=None,
            project_name="demo",
            runs_dir=runs_dir,
            override_entries=["timing.history_interval_minutes=45"],
            dry_run=False,
        )

        self.assertTrue(payload["project_updated"])
        state = load_project(project_root / "project.json")
        self.assertEqual(state["status"], "configured")
        self.assertEqual(state["artifacts"]["namelist_input"], (project_root / "wrf" / "namelist.input").as_posix())
        updated = read_namelist(project_root / "wrf" / "namelist.input")
        self.assertEqual(updated["time_control"]["history_interval"], [45, 45])
        self.assertTrue((project_root / "logs" / "wrf-improve-namelists.log").exists())

    def test_improve_namelists_project_mode_blocks_active_tasks(self) -> None:
        runs_dir = Path(tempfile.mkdtemp(prefix="wrf-improve-project-blocked-"))
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        state, _ = seed_project("demo", runs_dir, dry_run=False)
        state["execution"]["active_task"] = {"id": "task-1", "step": "wrf-run", "state": "running"}
        save_project(state, runs_dir / "demo" / "project.json")

        with self.assertRaises(RuntimeError):
            improve_namelists(
                namelist_input_path=None,
                namelist_wps_path=None,
                project_name="demo",
                runs_dir=runs_dir,
                dry_run=True,
            )


if __name__ == "__main__":
    unittest.main()
