import json
import shutil
import unittest
from pathlib import Path

from scripts.project_state import (
    create_project_state,
    load_project,
    record_error,
    register_artifact,
    seed_project,
    transition,
)

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class ProjectStateTests(unittest.TestCase):
    def test_create_project_state_sets_expected_paths(self) -> None:
        state = create_project_state("demo", Path("runs") / "demo")
        self.assertEqual(state["project_name"], "demo")
        self.assertEqual(state["paths"]["wps_dir"], "runs/demo/wps")
        self.assertEqual(state["status"], "created")

    def test_transition_rejects_invalid_state_changes(self) -> None:
        state = create_project_state("demo", Path("runs") / "demo")
        with self.assertRaises(ValueError):
            transition(state, "wps_ready")

    def test_register_artifact_updates_list_fields(self) -> None:
        state = create_project_state("demo", Path("runs") / "demo")
        register_artifact(state, "wrfout_files", "runs/demo/output/wrfout_d01")
        register_artifact(state, "wrfout_files", "runs/demo/output/wrfout_d01")
        self.assertEqual(len(state["artifacts"]["wrfout_files"]), 1)

    def test_record_error_marks_state_failed(self) -> None:
        state = create_project_state("demo", Path("runs") / "demo")
        record_error(state, "wrf-run", "MPI_FAIL", "mpi failed", "runs/demo/logs/wrf-run.log")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["last_error"]["code"], "MPI_FAIL")

    def test_seed_project_writes_files(self) -> None:
        tmp_dir = make_test_dir("_test_project_state")
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))

        state, spec = seed_project("demo", tmp_dir)
        project_root = tmp_dir / "demo"
        project_json = project_root / "project.json"
        spec_json = project_root / "simulation_spec.json"

        self.assertTrue(project_json.exists())
        self.assertTrue(spec_json.exists())
        self.assertEqual(load_project(project_json)["project_name"], "demo")
        loaded_spec = json.loads(spec_json.read_text(encoding="utf-8"))
        self.assertEqual(loaded_spec["project_name"], spec["project_name"])
        self.assertEqual(state["paths"]["project_root"], project_root.as_posix())


if __name__ == "__main__":
    unittest.main()
