import json
import shutil
import unittest
from pathlib import Path

from scripts.download_gfs import build_manifest
from scripts.wrf_config import configure_project
from scripts.wrf_data import prepare_data
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


def create_source_tree(source_root: Path, manifest: dict) -> None:
    for request in manifest["requests"]:
        target = source_root / request["remote_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{request['file_name']}\n", encoding="utf-8")


class WrfDataTests(unittest.TestCase):
    def init_and_configure_project(self, runs_dir: Path) -> None:
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
            domain_presets=["east_china", "shanghai_inner"],
            physics_preset="tropical_cyclone",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_12:00:00",
            dry_run=False,
        )

    def test_dry_run_reports_missing_inventory_without_writing_files(self) -> None:
        runs_dir = make_test_dir("_test_wrf_data_dry_run")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_and_configure_project(runs_dir)

        project_json = runs_dir / "demo" / "project.json"
        before = project_json.read_text(encoding="utf-8")

        payload = prepare_data("demo", runs_dir=runs_dir, dry_run=True)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["project"]["current_step"], "wrf-data")
        self.assertFalse(payload["project"]["status"] == "data_ready")
        self.assertGreater(payload["plan"]["missing_count"], 0)
        self.assertFalse((runs_dir / "demo" / "data" / "data_manifest.json").exists())
        self.assertEqual(before, project_json.read_text(encoding="utf-8"))

    def test_prepare_data_writes_manifest_and_script(self) -> None:
        runs_dir = make_test_dir("_test_wrf_data_real")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_and_configure_project(runs_dir)
        source_root = runs_dir / "_source"
        source_root.mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(
            start="2024-07-20_00:00:00",
            end="2024-07-20_12:00:00",
            interval_hours=3,
            resolution="0p25",
            base_url=source_root.as_uri(),
        )
        create_source_tree(source_root, manifest)

        payload = prepare_data(
            "demo",
            runs_dir=runs_dir,
            base_url=source_root.as_uri(),
            max_workers=1,
            dry_run=False,
        )

        project_root = runs_dir / "demo"
        manifest_path = project_root / "data" / "data_manifest.json"
        download_script = project_root / "data" / "download_gfs.sh"
        log_path = project_root / "logs" / "wrf-data.log"
        project_json = project_root / "project.json"

        self.assertTrue(manifest_path.exists())
        self.assertTrue(download_script.exists())
        self.assertTrue(log_path.exists())
        self.assertEqual(payload["project"]["current_step"], "wrf-data")
        self.assertEqual(payload["project"]["status"], "data_ready")
        self.assertEqual(payload["download"]["failed_count"], 0)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state = json.loads(project_json.read_text(encoding="utf-8"))
        log_text = log_path.read_text(encoding="utf-8")
        self.assertEqual(manifest["source"], "gfs")
        self.assertIn("summary", manifest)
        self.assertIn("download", manifest)
        self.assertEqual(state["artifacts"]["data_manifest"], manifest_path.as_posix())
        self.assertEqual(state["status"], "data_ready")
        self.assertIn("phase=starting", log_text)
        self.assertIn("progress completed=", log_text)
        self.assertIn("phase=finished", log_text)

    def test_prepare_data_marks_project_ready_when_all_files_exist(self) -> None:
        runs_dir = make_test_dir("_test_wrf_data_complete")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_and_configure_project(runs_dir)

        project_root = runs_dir / "demo"
        data_dir = project_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(
            start="2024-07-20_00:00:00",
            end="2024-07-20_12:00:00",
            interval_hours=3,
            resolution="0p25",
        )
        for request in manifest["requests"]:
            (data_dir / request["file_name"]).write_text("stub\n", encoding="utf-8")

        payload = prepare_data("demo", runs_dir=runs_dir, dry_run=False)
        project_json = json.loads((project_root / "project.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["project"]["status"], "data_ready")
        self.assertEqual(project_json["status"], "data_ready")
        self.assertEqual(
            len(project_json["artifacts"]["forcing_files"]),
            len(manifest["requests"]),
        )

    def test_prepare_data_raises_when_remote_files_missing(self) -> None:
        runs_dir = make_test_dir("_test_wrf_data_missing_remote")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_and_configure_project(runs_dir)

        source_root = runs_dir / "_source"
        source_root.mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(
            start="2024-07-20_00:00:00",
            end="2024-07-20_12:00:00",
            interval_hours=3,
            resolution="0p25",
            base_url=source_root.as_uri(),
        )
        create_source_tree(source_root, {"requests": manifest["requests"][:2]})

        with self.assertRaises(RuntimeError):
            prepare_data(
                "demo",
                runs_dir=runs_dir,
                base_url=source_root.as_uri(),
                max_workers=1,
                retries=0,
                dry_run=False,
            )

        project_json = json.loads((runs_dir / "demo" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(project_json["status"], "failed")
        self.assertEqual(project_json["last_error"]["code"], "DOWNLOAD_INCOMPLETE")

    def test_unsupported_data_source_raises(self) -> None:
        runs_dir = make_test_dir("_test_wrf_data_source")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_and_configure_project(runs_dir)

        spec_path = runs_dir / "demo" / "simulation_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["data_source"] = "era5"
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(NotImplementedError):
            prepare_data("demo", runs_dir=runs_dir, dry_run=True)


if __name__ == "__main__":
    unittest.main()
