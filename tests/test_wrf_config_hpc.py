import json
import shutil
import unittest
from pathlib import Path

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


class WrfConfigHpcTests(unittest.TestCase):
    def init_project(self, runs_dir: Path) -> None:
        initialize_project(
            "demo",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            templates_dir=TEMPLATES_DIR,
            dry_run=False,
            skip_env_check=True,
        )

    def test_hpc_rejection_does_not_overwrite_config_outputs(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_hpc_reject")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        config_copy = runs_dir / "wrf_env_hpc_disabled.json"
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        payload["hpc"]["enabled"] = False
        config_copy.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        spec_path = runs_dir / "demo" / "simulation_spec.json"
        before = spec_path.read_text(encoding="utf-8")

        result = configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=config_copy,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china"],
            physics_preset="tropical_cyclone",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_12:00:00",
            run_mode="hpc",
            dry_run=False,
        )

        state = json.loads((runs_dir / "demo" / "project.json").read_text(encoding="utf-8"))
        self.assertFalse(result["accepted"])
        self.assertEqual(before, spec_path.read_text(encoding="utf-8"))
        self.assertEqual(state["execution"]["last_admission"]["decision"], "rejected")
        self.assertIn("HPC_DISABLED", state["execution"]["last_admission"]["reason_codes"])

    def test_reconfigure_is_blocked_while_active_task_is_running(self) -> None:
        runs_dir = make_test_dir("_test_wrf_config_active_task_block")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_project(runs_dir)

        project_json = runs_dir / "demo" / "project.json"
        state = json.loads(project_json.read_text(encoding="utf-8"))
        state["execution"]["active_task"] = {
            "id": "task-1",
            "step": "wrf-run",
            "state": "running",
        }
        project_json.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(RuntimeError):
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
                dry_run=False,
            )


if __name__ == "__main__":
    unittest.main()
