import json
import shutil
import unittest
from pathlib import Path

from scripts.wrf_init import initialize_project

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "wrf_env.json"
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class WrfInitTests(unittest.TestCase):
    def test_dry_run_reports_plan_without_creating_project(self) -> None:
        runs_dir = make_test_dir("_test_wrf_init_dry_run")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        payload = initialize_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            templates_dir=TEMPLATES_DIR,
            dry_run=True,
            skip_env_check=True,
        )

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["project"]["project_name"], "demo")
        self.assertEqual(
            payload["plan"]["seed_files"][0]["target"],
            (runs_dir / "demo" / "wps" / "namelist.wps").as_posix(),
        )
        self.assertFalse((runs_dir / "demo").exists())

    def test_initialize_project_seeds_expected_files(self) -> None:
        runs_dir = make_test_dir("_test_wrf_init_real")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))

        payload = initialize_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            templates_dir=TEMPLATES_DIR,
            dry_run=False,
            skip_env_check=True,
        )

        project_root = runs_dir / "demo"
        project_json = project_root / "project.json"
        spec_json = project_root / "simulation_spec.json"
        namelist_wps = project_root / "wps" / "namelist.wps"
        namelist_input = project_root / "wrf" / "namelist.input"
        init_log = project_root / "logs" / "wrf-init.log"

        self.assertTrue(project_json.exists())
        self.assertTrue(spec_json.exists())
        self.assertTrue(namelist_wps.exists())
        self.assertTrue(namelist_input.exists())
        self.assertTrue(init_log.exists())
        self.assertEqual(payload["project"]["status"], "created")

        state = json.loads(project_json.read_text(encoding="utf-8"))
        self.assertEqual(state["artifacts"]["namelist_wps"], namelist_wps.as_posix())
        self.assertEqual(state["artifacts"]["namelist_input"], namelist_input.as_posix())
        self.assertEqual(state["current_step"], "wrf-init")

    def test_initialize_project_rejects_non_empty_target(self) -> None:
        runs_dir = make_test_dir("_test_wrf_init_existing")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        project_root = runs_dir / "demo"
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / "already-there.txt").write_text("x\n", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            initialize_project(
                "demo",
                runs_dir=runs_dir,
                config_path=CONFIG_PATH,
                templates_dir=TEMPLATES_DIR,
                dry_run=False,
                skip_env_check=True,
            )


if __name__ == "__main__":
    unittest.main()
