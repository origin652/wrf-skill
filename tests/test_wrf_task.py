import json
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.download_gfs import build_manifest
from scripts.project_state import load_project
from scripts.wrf_config import configure_project
from scripts.wrf_data import prepare_data
from scripts.wrf_init import initialize_project
from scripts.wrf_task import (
    cancel_task,
    collect_task,
    create_task_metadata,
    save_task,
    start_task,
    status_task,
    store_active_task,
    task_json_path,
    task_dir,
    wait_for_task,
)

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


def build_slow_wps_root(root: Path) -> None:
    (root / "geogrid" / "GEOGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "geogrid" / "GEOGRID.TBL.ARW").write_text("geogrid\n", encoding="utf-8")
    (root / "metgrid" / "METGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "metgrid" / "METGRID.TBL.ARW").write_text("metgrid\n", encoding="utf-8")
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").parent.mkdir(parents=True, exist_ok=True)
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").write_text("vtable\n", encoding="utf-8")
    write_executable(root / "geogrid.exe", "#!/usr/bin/env bash\nset -euo pipefail\nsleep 10\nprintf 'geo\\n' > geo_em.d01.nc\n")
    write_executable(root / "link_grib.csh", "#!/usr/bin/env bash\nset -euo pipefail\ntouch GRIBFILE.AAA\n")
    write_executable(root / "ungrib.exe", "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'ungrib\\n' > GFS:2024-07-20_00\n")
    write_executable(root / "metgrid.exe", "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'met\\n' > met_em.d01.2024-07-20_00:00:00.nc\n")


def write_config_copy(source: Path, target: Path, *, wps_dir: Path | None = None, wrf_dir: Path | None = None) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    if wps_dir is not None:
        payload["wps_dir"] = wps_dir.as_posix()
    if wrf_dir is not None:
        payload["wrf_dir"] = wrf_dir.as_posix()
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


class WrfTaskTests(unittest.TestCase):
    def init_configured_project(self, runs_dir: Path, *, run_mode: str = "local", config_path: Path = CONFIG_PATH) -> None:
        initialize_project(
            "demo",
            runs_dir=runs_dir,
            config_path=config_path,
            templates_dir=TEMPLATES_DIR,
            dry_run=False,
            skip_env_check=True,
        )
        configure_project(
            "demo",
            runs_dir=runs_dir,
            config_path=config_path,
            domains_config=DOMAINS_CONFIG,
            physics_config=PHYSICS_CONFIG,
            domain_presets=["east_china"],
            physics_preset="tropical_cyclone",
            start_time="2024-07-20_00:00:00",
            end_time="2024-07-20_01:00:00",
            run_mode=run_mode,
            dry_run=False,
        )

    def init_data_ready_project(self, runs_dir: Path, *, run_mode: str = "local", config_path: Path = CONFIG_PATH) -> None:
        self.init_configured_project(runs_dir, run_mode=run_mode, config_path=config_path)
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

    def test_local_start_returns_immediately_and_completes_outside_session(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_local_async")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

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

        start_payload = start_task(
            "demo",
            "wrf-data",
            runs_dir=runs_dir,
            task_kwargs={"base_url": source_root.as_uri(), "max_workers": 1},
        )
        self.assertTrue(start_payload["accepted"])
        self.assertIn(start_payload["task"]["state"], {"running", "completed"})
        self.assertTrue(str(start_payload["task"]["log_path"]).endswith("/logs/wrf-data.log"))

        final_payload = wait_for_task(
            "demo",
            task_id=start_payload["task"]["id"],
            runs_dir=runs_dir,
            timeout_seconds=30,
        )
        state = load_project(runs_dir / "demo" / "project.json")

        self.assertEqual(final_payload["task"]["state"], "completed")
        self.assertEqual(state["status"], "data_ready")
        self.assertEqual(state["execution"]["active_task"]["state"], "completed")
        self.assertEqual(state["execution"]["last_task"]["state"], "completed")

    def test_status_reads_live_wrf_data_progress_from_project_log(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_data_progress")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

        project_dir = runs_dir / "demo"
        project_json = project_dir / "project.json"
        task = create_task_metadata(
            "demo",
            "wrf-data",
            "local",
            project_dir,
            "task-data-progress",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            params={},
        )
        task["pid"] = 12345
        task["state"] = "running"
        task["log_path"] = (project_dir / "logs" / "wrf-data.log").as_posix()
        save_task(task)
        store_active_task(project_json, "wrf-data", task)
        (project_dir / "logs" / "wrf-data.log").write_text(
            "phase=starting\nprogress completed=1/3 remaining=2 downloaded=1 skipped=0 failed=0 file=gfs.t00z.pgrb2.0p25.f000 status=downloaded attempts=1 size_bytes=16\n",
            encoding="utf-8",
        )

        with patch("scripts.wrf_task.process_alive", return_value=True):
            payload = status_task("demo", task_id="task-data-progress", runs_dir=runs_dir)

        refreshed_task = json.loads(task_json_path(project_dir, "task-data-progress").read_text(encoding="utf-8"))
        self.assertEqual(payload["task"]["state"], "running")
        self.assertIn("progress completed=1/3", payload["task"]["last_progress"])
        self.assertIn("progress completed=1/3", refreshed_task["last_progress"])

    def test_hpc_start_records_admission_then_submits(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_hpc_start")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

        project_json = runs_dir / "demo" / "project.json"
        state = load_project(project_json)
        state["execution"]["mode"] = "hpc"
        project_json.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

        admission = {
            "decision": "admissible_with_queue",
            "reason_codes": ["QUEUE_EXPECTED"],
            "requested_layout": {"nodes": 1, "tasks_per_node": 8, "total_tasks": 8, "walltime_hours": 1},
            "recommended_layout": {"nodes": 1, "tasks_per_node": 8, "total_tasks": 8, "walltime_hours": 1},
            "static_limits": {},
            "live_cluster": {},
            "alternatives": [],
        }
        dummy_adapter = Mock()
        dummy_adapter.backend_name = "slurm"
        dummy_adapter.render_job.return_value = {
            "backend": "slurm",
            "script_path": (runs_dir / "demo" / "hpc" / "demo.slurm.job.sh").as_posix(),
            "remote_project_dir": "/scratch/user/wrf_runs/demo",
            "remote_log_dir": "/scratch/user/wrf_runs/demo/logs",
            "plan": admission["recommended_layout"],
        }
        dummy_adapter.submit.return_value = {"job_id": "12345", "submit_output": "Submitted batch job 12345", "state": "queued"}

        with patch("scripts.wrf_task.evaluate_admission", return_value=admission), patch(
            "scripts.wrf_task.get_scheduler_adapter", return_value=dummy_adapter
        ), patch(
            "scripts.wrf_task.run_sync_hpc",
            return_value=subprocess.CompletedProcess(["sync_hpc.sh"], 0, "ok", ""),
        ):
            payload = start_task("demo", "wrf-run", runs_dir=runs_dir)

        state = load_project(project_json)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["task"]["state"], "queued")
        self.assertEqual(payload["task"]["job_id"], "12345")
        self.assertEqual(state["execution"]["last_admission"]["decision"], "admissible_with_queue")

    def test_hpc_wps_start_records_admission_then_submits(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_hpc_wps_start")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        fake_wps_root = runs_dir / "_fake_wps"
        build_slow_wps_root(fake_wps_root)
        config_copy = write_config_copy(CONFIG_PATH, runs_dir / "wrf_env.wps.json", wps_dir=fake_wps_root)
        self.init_data_ready_project(runs_dir, config_path=config_copy)

        project_json = runs_dir / "demo" / "project.json"
        state = load_project(project_json)
        state["execution"]["mode"] = "hpc"
        project_json.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

        admission = {
            "decision": "admissible_now",
            "reason_codes": [],
            "requested_layout": {"nodes": 1, "tasks_per_node": 2, "total_tasks": 2, "walltime_hours": 1},
            "recommended_layout": {"nodes": 1, "tasks_per_node": 2, "total_tasks": 2, "walltime_hours": 1},
            "static_limits": {},
            "live_cluster": {},
            "alternatives": [],
        }
        dummy_adapter = Mock()
        dummy_adapter.backend_name = "slurm"
        dummy_adapter.render_job.return_value = {
            "backend": "slurm",
            "step": "wrf-wps",
            "script_path": (runs_dir / "demo" / "hpc" / "demo.wrf-wps.slurm.job.sh").as_posix(),
            "remote_project_dir": "/scratch/user/wrf_runs/demo",
            "remote_log_dir": "/scratch/user/wrf_runs/demo/logs",
            "plan": admission["recommended_layout"],
        }
        dummy_adapter.submit.return_value = {"job_id": "22334", "submit_output": "Submitted batch job 22334", "state": "queued"}

        with patch("scripts.wrf_task.evaluate_admission", return_value=admission), patch(
            "scripts.wrf_task.get_scheduler_adapter", return_value=dummy_adapter
        ), patch(
            "scripts.wrf_task.run_sync_hpc",
            return_value=subprocess.CompletedProcess(["sync_hpc.sh"], 0, "ok", ""),
        ):
            payload = start_task("demo", "wrf-wps", runs_dir=runs_dir, config_path=config_copy)

        state = load_project(project_json)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["task"]["state"], "queued")
        self.assertEqual(payload["task"]["job_id"], "22334")
        self.assertEqual(state["execution"]["last_admission"]["decision"], "admissible_now")
        self.assertTrue((runs_dir / "demo" / "wps" / "geogrid" / "GEOGRID.TBL").exists())
        self.assertTrue((runs_dir / "demo" / "wps" / "metgrid" / "METGRID.TBL").exists())
        self.assertTrue((runs_dir / "demo" / "wps" / "Vtable").exists())

    def test_status_queries_hpc_adapter_for_live_state(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_hpc_status")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

        project_dir = runs_dir / "demo"
        project_json = project_dir / "project.json"
        task = create_task_metadata(
            "demo",
            "wrf-run",
            "slurm",
            project_dir,
            "task-status",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            params={},
        )
        task["job_id"] = "123"
        task["state"] = "queued"
        save_task(task)
        store_active_task(project_json, "wrf-run", task)

        dummy_adapter = Mock()
        dummy_adapter.query.return_value = {"job_id": "123", "state": "running", "raw_state": "RUNNING", "detail": "RUNNING"}

        with patch("scripts.wrf_task.get_scheduler_adapter", return_value=dummy_adapter):
            payload = status_task("demo", task_id="task-status", runs_dir=runs_dir)

        self.assertEqual(payload["task"]["state"], "running")
        self.assertEqual(payload["project"]["execution"]["active_task"]["state"], "running")

    def test_cancel_marks_local_task_canceled(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_cancel")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

        project_dir = runs_dir / "demo"
        project_json = project_dir / "project.json"
        task = create_task_metadata(
            "demo",
            "wrf-wps",
            "local",
            project_dir,
            "task-cancel",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            params={},
        )
        task["pid"] = 99999
        task["state"] = "running"
        save_task(task)
        store_active_task(project_json, "wrf-wps", task)

        with patch("scripts.wrf_task.process_alive", return_value=True), patch("scripts.wrf_task.os.killpg") as mock_kill:
            payload = cancel_task("demo", task_id="task-cancel", runs_dir=runs_dir)

        self.assertEqual(payload["task"]["state"], "canceled")
        self.assertTrue(mock_kill.called)

    def test_collect_registers_hpc_wps_outputs(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_collect_wps")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

        project_dir = runs_dir / "demo"
        project_json = project_dir / "project.json"
        state = load_project(project_json)
        state["status"] = "data_ready"
        project_json.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

        met_em_path = project_dir / "wps" / "met_em.d01.2024-07-20_00:00:00.nc"
        met_em_path.parent.mkdir(parents=True, exist_ok=True)
        met_em_path.write_text("met\n", encoding="utf-8")

        task = create_task_metadata(
            "demo",
            "wrf-wps",
            "slurm",
            project_dir,
            "task-collect-wps",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            params={},
        )
        task["state"] = "completed"
        task["job_id"] = "22334"
        task["finished_at"] = "2024-07-20T00:00:00+00:00"
        save_task(task)
        store_active_task(project_json, "wrf-wps", task)
        (task_dir(project_dir, "task-collect-wps") / "rendered_job.json").write_text(
            json.dumps({"remote_project_dir": "/scratch/user/wrf_runs/demo"}, indent=2) + "\n",
            encoding="utf-8",
        )

        with patch("scripts.wrf_task.subprocess.run", return_value=subprocess.CompletedProcess(["collect_hpc.sh"], 0, "ok", "")):
            payload = collect_task("demo", task_id="task-collect-wps", runs_dir=runs_dir)

        state = load_project(project_json)
        self.assertEqual(payload["task"]["state"], "completed")
        self.assertEqual(state["status"], "wps_ready")
        self.assertEqual(state["artifacts"]["met_em_files"], [met_em_path.as_posix()])

    def test_collect_registers_hpc_outputs(self) -> None:
        runs_dir = make_test_dir("_test_wrf_task_collect")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        self.init_configured_project(runs_dir)

        project_dir = runs_dir / "demo"
        project_json = project_dir / "project.json"
        state = load_project(project_json)
        state["status"] = "wps_ready"
        project_json.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

        wrf_dir = project_dir / "wrf"
        output_dir = project_dir / "output"
        (wrf_dir / "wrfinput_d01").write_text("input\n", encoding="utf-8")
        (wrf_dir / "wrfbdy_d01").write_text("bdy\n", encoding="utf-8")
        wrfout_path = output_dir / "wrfout_d01_2024-07-20_00:00:00"
        output_dir.mkdir(parents=True, exist_ok=True)
        wrfout_path.write_text("out\n", encoding="utf-8")

        task = create_task_metadata(
            "demo",
            "wrf-run",
            "slurm",
            project_dir,
            "task-collect",
            runs_dir=runs_dir,
            config_path=CONFIG_PATH,
            params={},
        )
        task["state"] = "completed"
        task["job_id"] = "123"
        task["finished_at"] = "2024-07-20T00:00:00+00:00"
        save_task(task)
        store_active_task(project_json, "wrf-run", task)
        (task_dir(project_dir, "task-collect") / "rendered_job.json").write_text(
            json.dumps({"remote_project_dir": "/scratch/user/wrf_runs/demo"}, indent=2) + "\n",
            encoding="utf-8",
        )

        with patch("scripts.wrf_task.subprocess.run", return_value=subprocess.CompletedProcess(["collect_hpc.sh"], 0, "ok", "")):
            payload = collect_task("demo", task_id="task-collect", runs_dir=runs_dir)

        state = load_project(project_json)
        self.assertEqual(payload["task"]["state"], "completed")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["artifacts"]["wrfout_files"], [wrfout_path.as_posix()])


if __name__ == "__main__":
    unittest.main()
