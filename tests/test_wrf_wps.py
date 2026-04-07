import json
import shutil
import stat
import unittest
from pathlib import Path

from scripts.download_era5 import build_manifest as build_era5_manifest
from scripts.local_runtime import LocalRuntimeConfigError
from scripts.download_gfs import build_manifest as build_gfs_manifest
from scripts.wrf_config import configure_project
from scripts.wrf_data import prepare_data
from scripts.wrf_init import initialize_project
from scripts.wrf_wps import prepare_wps

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


def create_gfs_source_tree(source_root: Path, manifest: dict) -> None:
    for request in manifest["requests"]:
        target = source_root / request["remote_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{request['file_name']}\n", encoding="utf-8")


def create_era5_source_tree(source_root: Path, manifest: dict) -> None:
    for request in manifest["requests"]:
        target = source_root / request["remote_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{request['file_name']} {request['kind']}\n", encoding="utf-8")


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_passthrough_runner(path: Path) -> None:
    write_executable(
        path,
        "#!/usr/bin/env bash\nset -euo pipefail\ntarget=\"$1\"\nshift\nexec \"$target\" \"$@\"\n",
    )


def build_fake_wps_root(root: Path) -> None:
    (root / "geogrid" / "GEOGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "geogrid" / "GEOGRID.TBL.ARW").write_text("geogrid\n", encoding="utf-8")
    (root / "metgrid" / "METGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "metgrid" / "METGRID.TBL.ARW").write_text("metgrid\n", encoding="utf-8")
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").parent.mkdir(parents=True, exist_ok=True)
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").write_text("gfs\n", encoding="utf-8")
    (root / "ungrib" / "Variable_Tables" / "Vtable.ECMWF").parent.mkdir(parents=True, exist_ok=True)
    (root / "ungrib" / "Variable_Tables" / "Vtable.ECMWF").write_text("ecmwf\n", encoding="utf-8")

    write_executable(
        root / "geogrid.exe",
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'geo\\n' > geo_em.d01.nc\n",
    )
    write_executable(
        root / "link_grib.csh",
        "#!/usr/bin/env bash\nset -euo pipefail\nif [ \"$#\" -lt 1 ]; then exit 2; fi\ntouch GRIBFILE.AAA\n",
    )
    write_executable(
        root / "ungrib.exe",
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'ungrib\\n' > FILE:2024-07-20_00\n",
    )
    write_executable(
        root / "metgrid.exe",
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'met\\n' > met_em.d01.2024-07-20_00:00:00.nc\n",
    )


def write_config_copy(source: Path, target: Path, *, wps_dir: Path) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["wps_dir"] = wps_dir.as_posix()
    payload["wps_tables"] = {
        "geogrid": (wps_dir / "geogrid" / "GEOGRID.TBL.ARW").as_posix(),
        "metgrid": (wps_dir / "metgrid" / "METGRID.TBL.ARW").as_posix(),
        "vtable_by_source": {
            "gfs": (wps_dir / "ungrib" / "Variable_Tables" / "Vtable.GFS").as_posix(),
            "fnl": (wps_dir / "ungrib" / "Variable_Tables" / "Vtable.GFS").as_posix(),
            "era5": (wps_dir / "ungrib" / "Variable_Tables" / "Vtable.ECMWF").as_posix(),
        },
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


class WrfWpsTests(unittest.TestCase):
    def init_configured_project(self, runs_dir: Path, *, data_source: str = "gfs") -> None:
        initialize_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            templates_dir=TEMPLATES_DIR,
            dry_run=False,
            skip_env_check=True,
        )
        configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china"],
            physics_preset="tropical_cyclone",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_01:00:00",
            data_source=data_source,
            dry_run=False,
        )

    def init_data_ready_project(self, runs_dir: Path, *, data_source: str = "gfs") -> None:
        self.init_configured_project(runs_dir, data_source=data_source)
        source_root = runs_dir / "_source"
        source_root.mkdir(parents=True, exist_ok=True)
        if data_source == "era5":
            manifest = build_era5_manifest(
                start="2024-07-20_00:00:00",
                end="2024-07-20_01:00:00",
                interval_hours=3,
                base_url=source_root.as_uri(),
            )
            create_era5_source_tree(source_root, manifest)
        else:
            manifest = build_gfs_manifest(
                start="2024-07-20_00:00:00",
                end="2024-07-20_01:00:00",
                interval_hours=3,
                resolution="0p25",
                base_url=source_root.as_uri(),
            )
            create_gfs_source_tree(source_root, manifest)
        prepare_data(
            "demo",
            runs_dir=runs_dir,
            base_url=source_root.as_uri(),
            max_workers=1,
            dry_run=False,
        )

    def test_dry_run_reports_plan_without_mutating_project(self) -> None:
        runs_dir = make_test_dir("_test_wrf_wps_dry_run")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_data_ready_project(runs_dir)

        project_json = runs_dir / "demo" / "project.json"
        before = project_json.read_text(encoding="utf-8")

        payload = prepare_wps("demo", runs_dir=runs_dir, config_path=CONFIG_PATH, dry_run=True)

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["project"]["status"], "data_ready")
        self.assertEqual(payload["project"]["current_step"], "wrf-wps")
        self.assertEqual(payload["plan"]["forcing_count"], 1)
        self.assertEqual(len(payload["plan"]["expected_met_em_files"]), 1)
        self.assertEqual(before, project_json.read_text(encoding="utf-8"))

    def test_prepare_wps_runs_fake_commands_and_registers_outputs(self) -> None:
        runs_dir = make_test_dir("_test_wrf_wps_real")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_data_ready_project(runs_dir)

        fake_wps_root = runs_dir / "_fake_wps"
        build_fake_wps_root(fake_wps_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.json", wps_dir=fake_wps_root)

        payload = prepare_wps("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=False)

        project_root = runs_dir / "demo"
        project_json = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
        wps_dir = project_root / "wps"

        self.assertEqual(payload["project"]["status"], "wps_ready")
        self.assertEqual(project_json["status"], "wps_ready")
        self.assertEqual(
            project_json["artifacts"]["met_em_files"],
            [(wps_dir / "met_em.d01.2024-07-20_00:00:00.nc").as_posix()],
        )
        self.assertTrue((project_root / "logs" / "wrf-wps.log").exists())
        self.assertTrue((project_root / "logs" / "wrf-wps-geogrid.log").exists())
        self.assertTrue((project_root / "logs" / "wrf-wps-link-grib.log").exists())
        self.assertTrue((project_root / "logs" / "wrf-wps-ungrib.log").exists())
        self.assertTrue((project_root / "logs" / "wrf-wps-metgrid.log").exists())
        self.assertTrue((wps_dir / "geogrid" / "GEOGRID.TBL").exists())
        self.assertTrue((wps_dir / "metgrid" / "METGRID.TBL").exists())
        self.assertTrue((wps_dir / "Vtable").exists())

    def test_prepare_wps_uses_era5_vtable_when_requested(self) -> None:
        runs_dir = make_test_dir("_test_wrf_wps_era5_vtable")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_data_ready_project(runs_dir, data_source="era5")

        fake_wps_root = runs_dir / "_fake_wps_era5"
        build_fake_wps_root(fake_wps_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.era5.json", wps_dir=fake_wps_root)

        payload = prepare_wps("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=False)

        wps_dir = runs_dir / "demo" / "wps"
        self.assertEqual(payload["project"]["status"], "wps_ready")
        self.assertEqual((wps_dir / "Vtable").read_text(encoding="utf-8"), "ecmwf\n")

    def test_prepare_wps_supports_custom_safe_runtime(self) -> None:
        runs_dir = make_test_dir("_test_wrf_wps_custom_safe")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_data_ready_project(runs_dir)

        fake_wps_root = runs_dir / "_fake_wps"
        build_fake_wps_root(fake_wps_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.json", wps_dir=fake_wps_root)

        project_root = runs_dir / "demo"
        runner = project_root / "tools" / "safe-runner"
        write_passthrough_runner(runner)

        payload = json.loads(config_copy.read_text(encoding="utf-8"))
        payload.setdefault("local", {})["wps_runtime"] = {
            "mode": "custom_safe",
            "geogrid_cmd": [runner.as_posix(), "{geogrid_exe}"],
            "link_grib_cmd": [runner.as_posix(), "{link_grib_exe}", "{forcing_args}"],
            "ungrib_cmd": [runner.as_posix(), "{ungrib_exe}"],
            "metgrid_cmd": [runner.as_posix(), "{metgrid_exe}"],
        }
        config_copy.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        result = prepare_wps("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=False)

        self.assertEqual(result["project"]["status"], "wps_ready")
        self.assertEqual(result["plan"]["runtime_mode"], "custom_safe")
        self.assertIn(
            runner.as_posix(),
            (project_root / "logs" / "wrf-wps-link-grib.log").read_text(encoding="utf-8"),
        )

    def test_prepare_wps_rejects_invalid_custom_safe_runtime(self) -> None:
        runs_dir = make_test_dir("_test_wrf_wps_invalid_custom_safe")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_data_ready_project(runs_dir)

        fake_wps_root = runs_dir / "_fake_wps"
        build_fake_wps_root(fake_wps_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.json", wps_dir=fake_wps_root)

        payload = json.loads(config_copy.read_text(encoding="utf-8"))
        payload.setdefault("local", {})["wps_runtime"] = {
            "mode": "custom_safe",
            "geogrid_cmd": ["{geogrid_exe}"],
            "link_grib_cmd": "link_grib.csh",
            "ungrib_cmd": ["{ungrib_exe}"],
            "metgrid_cmd": ["{metgrid_exe}"],
        }
        config_copy.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(LocalRuntimeConfigError):
            prepare_wps("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=True)

    def test_prepare_wps_reuses_existing_met_em_outputs(self) -> None:
        runs_dir = make_test_dir("_test_wrf_wps_existing")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_data_ready_project(runs_dir)

        met_em_path = runs_dir / "demo" / "wps" / "met_em.d01.2024-07-20_00:00:00.nc"
        met_em_path.write_text("met\n", encoding="utf-8")

        payload = prepare_wps("demo", runs_dir=runs_dir, config_path=CONFIG_PATH, dry_run=False)
        state = json.loads((runs_dir / "demo" / "project.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["project"]["status"], "wps_ready")
        self.assertEqual(state["status"], "wps_ready")
        self.assertEqual(state["artifacts"]["met_em_files"], [met_em_path.as_posix()])
        self.assertTrue((runs_dir / "demo" / "logs" / "wrf-wps.log").exists())

    def test_prepare_wps_fails_when_forcing_files_are_missing(self) -> None:
        runs_dir = make_test_dir("_test_wrf_wps_missing_forcing")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

        with self.assertRaises(RuntimeError):
            prepare_wps("demo", runs_dir=runs_dir, config_path=CONFIG_PATH, dry_run=False)

        state = json.loads((runs_dir / "demo" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["last_error"]["code"], "FORCING_MISSING")


if __name__ == "__main__":
    unittest.main()
