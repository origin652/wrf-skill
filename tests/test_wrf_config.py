import json
import shutil
import unittest
from pathlib import Path

from scripts.namelist_parser import read_namelist
from scripts.wrf_config import configure_project
from scripts.wrf_init import initialize_project

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "wrf_env.json"
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
DOMAINS_CONFIG = Path(__file__).resolve().parents[1] / "config" / "domains_presets.json"
PHYSICS_CONFIG = Path(__file__).resolve().parents[1] / "config" / "physics_schemes.json"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class WrfConfigTests(unittest.TestCase):
    def init_project(self, runs_dir: Path) -> None:
        initialize_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            templates_dir=TEMPLATES_DIR,
            dry_run=False,
            skip_env_check=True,
        )

    def test_dry_run_keeps_project_state_unchanged(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_dry_run")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        project_json = runs_dir / "demo" / "project.json"
        before = project_json.read_text(encoding="utf-8")

        payload = configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china", "shanghai_inner"],
            physics_preset="tropical_cyclone",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_12:00:00",
            override_entries=["domains.1.i_parent_start=25", "domains.1.j_parent_start=26"],
            dry_run=True,
        )

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["project"]["status"], "configured")
        self.assertEqual(len(payload["simulation_spec"]["domains"]), 2)
        self.assertEqual(
            payload["simulation_spec"]["domains"][1]["i_parent_start"],
            25,
        )
        self.assertEqual(before, project_json.read_text(encoding="utf-8"))

    def test_configure_project_writes_spec_and_namelists(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_real")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        payload = configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china", "shanghai_inner"],
            physics_preset="deep_convection",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_12:00:00",
            data_source="gfs",
            run_mode="local",
            override_entries=[
                "domains.1.i_parent_start=20",
                "domains.1.j_parent_start=22",
                "physics.cu_physics=0"
            ],
            dry_run=False,
        )

        project_root = runs_dir / "demo"
        project_json = project_root / "project.json"
        spec_json = project_root / "simulation_spec.json"
        log_path = project_root / "logs" / "wrf-config.log"
        namelist_wps = project_root / "wps" / "namelist.wps"
        namelist_input = project_root / "wrf" / "namelist.input"

        self.assertTrue(project_json.exists())
        self.assertTrue(spec_json.exists())
        self.assertTrue(log_path.exists())
        self.assertTrue(namelist_wps.exists())
        self.assertTrue(namelist_input.exists())
        self.assertEqual(payload["project"]["status"], "configured")

        state = json.loads(project_json.read_text(encoding="utf-8"))
        spec = json.loads(spec_json.read_text(encoding="utf-8"))
        input_namelist = read_namelist(namelist_input)
        wps_namelist = read_namelist(namelist_wps)

        self.assertEqual(state["status"], "configured")
        self.assertEqual(state["data_source"]["type"], "gfs")
        self.assertEqual(state["artifacts"]["namelist_wps"], namelist_wps.as_posix())
        self.assertEqual(state["artifacts"]["namelist_input"], namelist_input.as_posix())
        self.assertEqual(len(spec["domains"]), 2)
        self.assertEqual(spec["domains"][1]["parent_grid_ratio"], 3)
        self.assertEqual(spec["domains"][1]["i_parent_start"], 20)
        self.assertEqual(wps_namelist["share"]["max_dom"], 2)
        self.assertEqual(input_namelist["domains"]["max_dom"], 2)
        self.assertEqual(input_namelist["physics"]["cu_physics"], [0, 0])

    def test_configure_project_sets_era5_wps_prefix(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_era5_prefix")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china"],
            physics_preset="tropical_cyclone",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_12:00:00",
            data_source="era5",
            dry_run=False,
        )

        namelist_wps = read_namelist(runs_dir / "demo" / "wps" / "namelist.wps")
        self.assertEqual(namelist_wps["ungrib"]["prefix"], "ERA5")
        self.assertEqual(namelist_wps["metgrid"]["fg_name"], "ERA5")

    def test_configure_project_sets_fnl_interval_and_wps_prefix(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_fnl_prefix")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china"],
            physics_preset="tropical_cyclone",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_12:00:00",
            data_source="fnl",
            dry_run=False,
        )

        spec = json.loads((runs_dir / "demo" / "simulation_spec.json").read_text(encoding="utf-8"))
        namelist_wps = read_namelist(runs_dir / "demo" / "wps" / "namelist.wps")
        self.assertEqual(spec["data_source"], "fnl")
        self.assertEqual(spec["timing"]["forcing_interval_seconds"], 21600)
        self.assertEqual(spec["wps"]["share"]["interval_seconds"], 21600)
        self.assertEqual(namelist_wps["share"]["interval_seconds"], 21600)
        self.assertEqual(namelist_wps["ungrib"]["prefix"], "FNL")
        self.assertEqual(namelist_wps["metgrid"]["fg_name"], "FNL")

    def test_configure_project_rejects_fnl_start_time_off_cycle(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_fnl_bad_start")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        with self.assertRaises(ValueError):
            configure_project(
                "demo",
                runs_dir=runs_dir,
                config_path=CONFIG_PATH,
                domains_config=DOMAINS_CONFIG,
                physics_config=PHYSICS_CONFIG,
                domain_presets=["east_china"],
                physics_preset="tropical_cyclone",
                start_time="2024-07-20_03:00:00",
                end_time="2024-07-20_12:00:00",
                data_source="fnl",
                dry_run=True,
            )

    def test_unknown_preset_raises_error(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_bad_preset")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        with self.assertRaises(KeyError):
            configure_project(
                "demo",
                runs_dir=runs_dir,
                config_path=CONFIG_PATH,
                domains_config=DOMAINS_CONFIG,
                physics_config=PHYSICS_CONFIG,
                physics_preset="does-not-exist",
                dry_run=True,
            )

    def test_request_text_can_drive_common_configuration(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_request_text")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        payload = configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            request_text="用 GFS 数据模拟 2024-07-20 00:00 到 2024-07-20 12:00，区域华东到上海，台风，本地运行",
            dry_run=True,
        )

        spec = payload["simulation_spec"]
        self.assertEqual(spec["data_source"], "gfs")
        self.assertEqual(spec["execution"]["run_mode"], "local")
        self.assertEqual(spec["timing"]["start_time"], "2024-07-20_00:00:00")
        self.assertEqual(spec["timing"]["end_time"], "2024-07-20_12:00:00")
        self.assertEqual(len(spec["domains"]), 2)
        self.assertEqual(spec["domains"][0]["preset_name"], "east_china")
        self.assertEqual(spec["domains"][1]["preset_name"], "shanghai_inner")
        self.assertEqual(spec["physics"]["mp_physics"], 6)
        self.assertEqual(payload["plan"]["inferred"]["physics_preset"], "tropical_cyclone")

    def test_namelist_overrides_apply_to_rendered_input(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_namelist_override")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china", "shanghai_inner"],
            physics_preset="deep_convection",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_12:00:00",
            namelist_override_entries=[
                "time_control.history_interval=30",
                "domains.e_vert=45",
                "dynamics.w_damping=1"
            ],
            dry_run=False,
        )

        namelist_input = read_namelist(runs_dir / "demo" / "wrf" / "namelist.input")
        self.assertEqual(namelist_input["time_control"]["history_interval"], [30, 30])
        self.assertEqual(namelist_input["domains"]["e_vert"], [45, 45])
        self.assertEqual(namelist_input["dynamics"]["w_damping"], 1)

    def test_unsafe_spec_override_is_rejected(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_bad_override")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        with self.assertRaises(ValueError):
            configure_project(
                "demo",
                runs_dir=runs_dir,
                config_path=CONFIG_PATH,
                domains_config=DOMAINS_CONFIG,
                physics_config=PHYSICS_CONFIG,
                override_entries=["paths.project_root=bad"],
                dry_run=True,
            )

    def test_inconsistent_child_spacing_is_rejected(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_bad_ratio")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        with self.assertRaises(ValueError):
            configure_project(
                "demo",
                runs_dir=runs_dir,
                config_path=CONFIG_PATH,
                domains_config=DOMAINS_CONFIG,
                physics_config=PHYSICS_CONFIG,
                domain_presets=["east_china", "shanghai_inner"],
                override_entries=["domains.1.dx_km=10"],
                dry_run=True,
            )


if __name__ == "__main__":
    unittest.main()
