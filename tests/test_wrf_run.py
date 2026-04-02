import json
import shutil
import stat
import unittest
from pathlib import Path

from scripts.download_gfs import build_manifest
from scripts.wrf_config import configure_project
from scripts.wrf_data import prepare_data
from scripts.wrf_init import initialize_project
from scripts.wrf_run import build_commands, run_project, stage_files
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



def create_source_tree(source_root: Path, manifest: dict) -> None:
    for request in manifest["requests"]:
        target = source_root / request["remote_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{request['file_name']}\n", encoding="utf-8")



def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)



def build_fake_wps_root(root: Path) -> None:
    (root / "geogrid" / "GEOGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "geogrid" / "GEOGRID.TBL.ARW").write_text("geogrid\n", encoding="utf-8")
    (root / "metgrid" / "METGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "metgrid" / "METGRID.TBL.ARW").write_text("metgrid\n", encoding="utf-8")
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").parent.mkdir(parents=True, exist_ok=True)
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").write_text("vtable\n", encoding="utf-8")

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
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'ungrib\\n' > GFS:2024-07-20_00\n",
    )
    write_executable(
        root / "metgrid.exe",
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'met\\n' > met_em.d01.2024-07-20_00:00:00.nc\n",
    )



def build_fake_wrf_root(root: Path) -> None:
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "LANDUSE.TBL").write_text("landuse\n", encoding="utf-8")
    write_executable(
        run_dir / "real.exe",
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'input\\n' > wrfinput_d01\nprintf 'bdy\\n' > wrfbdy_d01\nprintf 'real\\n' > rsl.out.0000\n",
    )
    write_executable(
        run_dir / "wrf.exe",
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'wrfout\\n' > wrfout_d01_2024-07-20_00:00:00\nprintf 'wrf\\n' > rsl.error.0000\n",
    )



def write_config_copy(source: Path, target: Path, *, wps_dir: Path, wrf_dir: Path) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["wps_dir"] = wps_dir.as_posix()
    payload["wrf_dir"] = wrf_dir.as_posix()
    payload.setdefault("local", {})["default_np"] = 1
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


class WrfRunTests(unittest.TestCase):
    def test_build_commands_uses_absolute_paths(self) -> None:
        commands, np = build_commands(
            Path("runs/demo/wrf"),
            {"local": {"mpi_cmd": "mpirun", "default_np": 2}},
        )

        self.assertEqual(np, 2)
        self.assertEqual(commands["real"], [str((Path("runs/demo/wrf") / "real.exe").resolve())])
        self.assertEqual(
            commands["wrf"],
            ["mpirun", "-np", "2", str((Path("runs/demo/wrf") / "wrf.exe").resolve())],
        )

    def test_stage_files_preserves_executable_mode(self) -> None:
        runs_dir = make_test_dir("_test_wrf_stage_permissions")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        source = runs_dir / "source" / "real.exe"
        write_executable(source, "#!/usr/bin/env bash\nexit 0\n")

        staged = stage_files([source], runs_dir / "target")
        target = Path(staged[0])

        self.assertTrue(target.exists())
        self.assertTrue(target.stat().st_mode & stat.S_IXUSR)

    def init_wps_ready_project(self, runs_dir: Path, config_copy: Path) -> None:
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
            dry_run=False,
        )
        source_root = runs_dir / "_source"
        source_root.mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(
            start="2024-07-20_00:00:00",
            end="2024-07-20_01:00:00",
            interval_hours=3,
            resolution="0p25",
            base_url=source_root.as_uri(),
        )
        create_source_tree(source_root, manifest)
        prepare_data(
            "demo",
            runs_dir=runs_dir,
            base_url=source_root.as_uri(),
            max_workers=1,
            dry_run=False,
        )
        prepare_wps("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=False)

    def test_dry_run_reports_plan_without_mutating_project(self) -> None:
        runs_dir = make_test_dir("_test_wrf_run_dry_run")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        fake_wps_root = runs_dir / "_fake_wps"
        fake_wrf_root = runs_dir / "_fake_wrf"
        build_fake_wps_root(fake_wps_root)
        build_fake_wrf_root(fake_wrf_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.json", wps_dir=fake_wps_root, wrf_dir=fake_wrf_root)
        self.init_wps_ready_project(runs_dir, config_copy)

        project_json = runs_dir / "demo" / "project.json"
        before = project_json.read_text(encoding="utf-8")

        payload = run_project("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=True)

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["project"]["status"], "wps_ready")
        self.assertEqual(payload["project"]["current_step"], "wrf-run")
        self.assertEqual(len(payload["plan"]["met_em_files"]), 1)
        self.assertEqual(before, project_json.read_text(encoding="utf-8"))

    def test_run_project_executes_fake_real_and_wrf(self) -> None:
        runs_dir = make_test_dir("_test_wrf_run_real")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        fake_wps_root = runs_dir / "_fake_wps"
        fake_wrf_root = runs_dir / "_fake_wrf"
        build_fake_wps_root(fake_wps_root)
        build_fake_wrf_root(fake_wrf_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.json", wps_dir=fake_wps_root, wrf_dir=fake_wrf_root)
        self.init_wps_ready_project(runs_dir, config_copy)

        payload = run_project("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=False)

        project_root = runs_dir / "demo"
        state = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
        wrf_dir = project_root / "wrf"

        self.assertEqual(payload["project"]["status"], "completed")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["artifacts"]["wrfinput_files"], [(wrf_dir / "wrfinput_d01").as_posix()])
        self.assertEqual(
            state["artifacts"]["wrfout_files"],
            [(wrf_dir / "wrfout_d01_2024-07-20_00:00:00").as_posix()],
        )
        self.assertTrue((project_root / "logs" / "wrf-run.log").exists())
        self.assertTrue((project_root / "logs" / "wrf-run-real.log").exists())
        self.assertTrue((project_root / "logs" / "wrf-run-wrf.log").exists())
        self.assertTrue((wrf_dir / "LANDUSE.TBL").exists())
        self.assertTrue((wrf_dir / "met_em.d01.2024-07-20_00:00:00.nc").exists())

    def test_run_project_reuses_existing_wrfout(self) -> None:
        runs_dir = make_test_dir("_test_wrf_run_existing")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        fake_wps_root = runs_dir / "_fake_wps"
        fake_wrf_root = runs_dir / "_fake_wrf"
        build_fake_wps_root(fake_wps_root)
        build_fake_wrf_root(fake_wrf_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.json", wps_dir=fake_wps_root, wrf_dir=fake_wrf_root)
        self.init_wps_ready_project(runs_dir, config_copy)

        wrf_dir = runs_dir / "demo" / "wrf"
        (wrf_dir / "wrfinput_d01").write_text("input\n", encoding="utf-8")
        (wrf_dir / "wrfbdy_d01").write_text("bdy\n", encoding="utf-8")
        wrfout_path = wrf_dir / "wrfout_d01_2024-07-20_00:00:00"
        wrfout_path.write_text("out\n", encoding="utf-8")

        payload = run_project("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=False)
        state = json.loads((runs_dir / "demo" / "project.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["project"]["status"], "completed")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["artifacts"]["wrfout_files"], [wrfout_path.as_posix()])

    def test_run_project_fails_without_met_em_files(self) -> None:
        runs_dir = make_test_dir("_test_wrf_run_missing_met_em")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

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
            dry_run=False,
        )

        project_json = runs_dir / "demo" / "project.json"
        state = json.loads(project_json.read_text(encoding="utf-8"))
        state["status"] = "wps_ready"
        state["current_step"] = "wrf-wps"
        project_json.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

        fake_wps_root = runs_dir / "_fake_wps"
        fake_wrf_root = runs_dir / "_fake_wrf"
        build_fake_wps_root(fake_wps_root)
        build_fake_wrf_root(fake_wrf_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.json", wps_dir=fake_wps_root, wrf_dir=fake_wrf_root)

        with self.assertRaises(RuntimeError):
            run_project("demo", runs_dir=runs_dir, config_path=config_copy, dry_run=False)

        failed_state = json.loads(project_json.read_text(encoding="utf-8"))
        self.assertEqual(failed_state["status"], "failed")
        self.assertEqual(failed_state["last_error"]["code"], "MET_EM_MISSING")


if __name__ == "__main__":
    unittest.main()
