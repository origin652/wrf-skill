import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.hpc import get_scheduler_adapter, register_scheduler_adapter
from scripts.hpc.base import HpcSchedulerAdapter


class DummyAdapter(HpcSchedulerAdapter):
    backend_name = "dummy-test"
    template_name = "slurm_wrf.sh.template"

    def parse_submit_output(self, output: str) -> str | None:
        return "dummy"

    def parse_query_output(self, output: str, job_id: str) -> dict[str, str]:
        return {"job_id": job_id, "state": "completed", "raw_state": "DONE", "detail": output}


class HpcAdapterTests(unittest.TestCase):
    def slurm_config(
        self,
        *,
        access_mode: str = "login",
        remote_host: str | None = None,
        scheduler_host: str | None = None,
        scheduler_ssh_cmd: str | None = "ssh -o BatchMode=yes",
    ) -> dict:
        hpc = {
            "enabled": True,
            "backend": "slurm",
            "access_mode": access_mode,
            "remote_base_dir": "/scratch/user/wrf_runs",
            "partition": "normal",
            "account": "myproject",
            "submit_cmd": "sbatch",
            "status_cmd": "squeue -j {job_id}",
            "cancel_cmd": "scancel {job_id}",
        }
        if remote_host is not None:
            hpc["remote_host"] = remote_host
        if scheduler_host is not None:
            hpc["scheduler_host"] = scheduler_host
        if scheduler_ssh_cmd is not None:
            hpc["scheduler_ssh_cmd"] = scheduler_ssh_cmd
        return {"hpc": hpc}

    def pbs_config(
        self,
        *,
        access_mode: str = "login",
        remote_host: str | None = None,
        scheduler_host: str | None = None,
        scheduler_ssh_cmd: str | None = "ssh -o BatchMode=yes",
    ) -> dict:
        hpc = {
            "enabled": True,
            "backend": "pbs",
            "access_mode": access_mode,
            "remote_base_dir": "/scratch/user/wrf_runs",
            "partition": "normal",
            "account": "myproject",
            "submit_cmd": "qsub",
            "status_cmd": "qstat -f {job_id}",
            "cancel_cmd": "qdel {job_id}",
        }
        if remote_host is not None:
            hpc["remote_host"] = remote_host
        if scheduler_host is not None:
            hpc["scheduler_host"] = scheduler_host
        if scheduler_ssh_cmd is not None:
            hpc["scheduler_ssh_cmd"] = scheduler_ssh_cmd
        return {"hpc": hpc}

    def render_fixture(self) -> tuple[dict, dict, Path]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        project_root = Path(tmpdir.name) / "demo"
        project_root.mkdir(parents=True, exist_ok=True)
        project_state = {
            "project_name": "demo",
            "paths": {"project_root": str(project_root)},
        }
        plan = {
            "nodes": 1,
            "tasks_per_node": 8,
            "total_tasks": 8,
            "walltime_hours": 1,
        }
        return project_state, plan, project_root

    @patch("scripts.hpc.base.subprocess.run")
    def test_slurm_submit_query_cancel_are_normalized(self, mock_run) -> None:
        adapter = get_scheduler_adapter("slurm")
        rendered_job = {
            "script_path": "/tmp/demo.sh",
            "remote_script_path": "/scratch/user/wrf_runs/demo/hpc/demo.slurm.job.sh",
        }
        mock_run.side_effect = [
            subprocess.CompletedProcess(["sbatch"], 0, "Submitted batch job 12345\n", ""),
            subprocess.CompletedProcess(["squeue"], 0, "JOBID ST\n12345 R\n", ""),
            subprocess.CompletedProcess(["scancel"], 0, "", ""),
        ]

        submit_result = adapter.submit(rendered_job, self.slurm_config())
        status_result = adapter.query({"job_id": submit_result["job_id"]}, self.slurm_config())
        cancel_result = adapter.cancel({"job_id": submit_result["job_id"]}, self.slurm_config())

        self.assertEqual(submit_result["job_id"], "12345")
        self.assertEqual(status_result["state"], "running")
        self.assertTrue(cancel_result["canceled"])
        self.assertEqual(mock_run.call_args_list[0].args[0], ["sbatch", "/scratch/user/wrf_runs/demo/hpc/demo.slurm.job.sh"])

    @patch("scripts.hpc.slurm.run_scheduler_command")
    def test_slurm_query_falls_back_to_sacct_for_terminal_jobs(self, mock_run_scheduler_command) -> None:
        adapter = get_scheduler_adapter("slurm")
        mock_run_scheduler_command.side_effect = [
            subprocess.CompletedProcess(["squeue"], 1, "", "slurm_load_jobs error: Invalid job id specified\n"),
            subprocess.CompletedProcess(["sacct"], 0, "12345|COMPLETED|0:0\n12345.batch|COMPLETED|0:0\n", ""),
        ]

        status_result = adapter.query({"job_id": "12345"}, self.slurm_config())

        self.assertEqual(status_result["state"], "completed")
        self.assertEqual(status_result["raw_state"], "COMPLETED")
        self.assertEqual(
            mock_run_scheduler_command.call_args_list[1].args[0],
            ["sacct", "-n", "-P", "-j", "12345", "-o", "JobIDRaw,State,ExitCode"],
        )

    @patch("scripts.hpc.base.subprocess.run")
    def test_pbs_submit_query_cancel_are_normalized(self, mock_run) -> None:
        adapter = get_scheduler_adapter("pbs")
        rendered_job = {
            "script_path": "/tmp/demo.sh",
            "remote_script_path": "/scratch/user/wrf_runs/demo/hpc/demo.pbs.job.sh",
        }
        mock_run.side_effect = [
            subprocess.CompletedProcess(["qsub"], 0, "42.server\n", ""),
            subprocess.CompletedProcess(["qstat"], 0, "Job Id: 42.server\n    job_state = F\n    Exit_status = 0\n", ""),
            subprocess.CompletedProcess(["qdel"], 0, "", ""),
        ]

        submit_result = adapter.submit(rendered_job, self.pbs_config())
        status_result = adapter.query({"job_id": submit_result["job_id"]}, self.pbs_config())
        cancel_result = adapter.cancel({"job_id": submit_result["job_id"]}, self.pbs_config())

        self.assertEqual(submit_result["job_id"], "42.server")
        self.assertEqual(status_result["state"], "completed")
        self.assertTrue(cancel_result["canceled"])
        self.assertEqual(mock_run.call_args_list[0].args[0], ["qsub", "/scratch/user/wrf_runs/demo/hpc/demo.pbs.job.sh"])

    def test_validate_config_allows_login_scheduler_without_remote_host(self) -> None:
        adapter = get_scheduler_adapter("slurm")

        summary = adapter.validate_config(self.slurm_config(access_mode="login", remote_host=None))

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["access_mode"], "login")
        self.assertIsNone(summary["scheduler_host"])

    def test_validate_config_maps_legacy_local_alias_to_login(self) -> None:
        adapter = get_scheduler_adapter("slurm")

        summary = adapter.validate_config(self.slurm_config(access_mode="local", remote_host=None))

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["access_mode"], "login")

    @patch("scripts.hpc.base.subprocess.run")
    def test_slurm_ssh_mode_wraps_scheduler_commands(self, mock_run) -> None:
        adapter = get_scheduler_adapter("slurm")
        config = self.slurm_config(access_mode="ssh", remote_host="login.cluster.example")
        rendered_job = {
            "script_path": "/tmp/demo.sh",
            "remote_script_path": "/scratch/user/wrf_runs/demo/hpc/demo.slurm.job.sh",
        }
        mock_run.side_effect = [
            subprocess.CompletedProcess(["ssh"], 0, "Submitted batch job 12345\n", ""),
            subprocess.CompletedProcess(["ssh"], 0, "JOBID ST\n12345 R\n", ""),
            subprocess.CompletedProcess(["ssh"], 0, "", ""),
        ]

        submit_result = adapter.submit(rendered_job, config)
        adapter.query({"job_id": submit_result["job_id"]}, config)
        adapter.cancel({"job_id": submit_result["job_id"]}, config)

        submit_call = mock_run.call_args_list[0].args[0]
        query_call = mock_run.call_args_list[1].args[0]
        cancel_call = mock_run.call_args_list[2].args[0]

        self.assertEqual(submit_call[:4], ["ssh", "-o", "BatchMode=yes", "login.cluster.example"])
        self.assertEqual(submit_call[4:6], ["sh", "-c"])
        self.assertIn("sbatch", submit_call[6])
        self.assertIn("/scratch/user/wrf_runs/demo/hpc/demo.slurm.job.sh", submit_call[6])
        self.assertIn("squeue -j 12345", query_call[6])
        self.assertIn("scancel 12345", cancel_call[6])

    def test_render_job_supports_remote_runtime_via_config(self) -> None:
        adapter = get_scheduler_adapter("slurm")
        config = self.slurm_config()
        config["hpc"]["runtime"] = {
            "mode": "remote_run_dir",
            "remote_run_dir": "$WRF_HOME/run",
            "launcher_cmd": "mpiexec",
            "tasks_flag": "-n",
            "setup_commands": [
                "source /etc/profile",
                "module purge",
            ],
            "modules": ["wrf/4.7.1"],
            "python_env": None,
        }
        project_state, plan, project_root = self.render_fixture()

        rendered = adapter.render_job(project_state, plan, config)
        script = Path(rendered["script_path"]).read_text(encoding="utf-8")

        self.assertEqual(rendered["runtime_mode"], "remote_run_dir")
        self.assertIn("source /etc/profile", script)
        self.assertIn("module purge", script)
        self.assertIn("module load wrf/4.7.1", script)
        self.assertIn("mpiexec -n 8 $WRF_HOME/run/real.exe", script)
        self.assertIn("mpiexec -n 8 $WRF_HOME/run/wrf.exe", script)
        self.assertIn("rm -f met_em.d*.nc", script)
        self.assertIn("met_em_sources=(../wps/met_em.d*.nc)", script)
        self.assertIn('ln -sfn "../wps/$name" "$name"', script)
        self.assertNotIn('ln -sfn "/scratch/user/wrf_runs/demo/wps/', script)
        self.assertNotIn("conda activate", script)
        self.assertTrue((project_root / "hpc" / "demo.slurm.job.sh").exists())

    def test_render_job_supports_remote_post_runtime_via_config(self) -> None:
        adapter = get_scheduler_adapter("slurm")
        config = self.slurm_config()
        config["hpc"]["post_runtime"] = {
            "setup_commands": ["module purge"],
            "modules": ["wrf-post/1.0"],
            "python_env": "wrf-post",
            "python_cmd": ["python3", "-u"],
        }
        project_state, plan, project_root = self.render_fixture()

        rendered = adapter.render_job(project_state, plan, config)
        script = Path(rendered["script_path"]).read_text(encoding="utf-8")

        self.assertIn("module load wrf-post/1.0", script)
        self.assertIn("conda activate wrf-post", script)
        self.assertIn(
            "python3 -u /scratch/user/wrf_runs/demo/.wrf-skill/scripts/wrf_post.py --project-name demo --runs-dir /scratch/user/wrf_runs",
            script,
        )
        self.assertTrue((project_root / "hpc" / "demo.slurm.job.sh").exists())

    def test_render_job_supports_remote_wps_runtime_via_config(self) -> None:
        adapter = get_scheduler_adapter("slurm")
        config = self.slurm_config()
        config["hpc"]["wps_runtime"] = {
            "mode": "remote_wps_dir",
            "setup_commands": ["module purge"],
            "modules": ["wps/4.7.1"],
            "python_env": None,
            "remote_wps_dir": "$WPS_HOME",
        }
        project_state, plan, project_root = self.render_fixture()
        forcing_path = project_root / "data" / "gfs.t00z.pgrb2.1p00.f000"
        forcing_path.parent.mkdir(parents=True, exist_ok=True)
        forcing_path.write_text("gfs\n", encoding="utf-8")
        wps_plan = {
            **plan,
            "step": "wrf-wps",
            "forcing_files": [forcing_path.as_posix()],
        }

        rendered = adapter.render_job(project_state, wps_plan, config)
        script = Path(rendered["script_path"]).read_text(encoding="utf-8")

        self.assertEqual(rendered["runtime_mode"], "remote_wps_dir")
        self.assertIn("module purge", script)
        self.assertIn("module load wps/4.7.1", script)
        self.assertIn('cd "/scratch/user/wrf_runs/demo/wps"', script)
        self.assertIn("$WPS_HOME/geogrid.exe", script)
        self.assertIn("$WPS_HOME/link_grib.csh /scratch/user/wrf_runs/demo/data/gfs.t00z.pgrb2.1p00.f000", script)
        self.assertIn("$WPS_HOME/ungrib.exe", script)
        self.assertIn("$WPS_HOME/metgrid.exe", script)
        self.assertTrue((project_root / "hpc" / "demo.wrf-wps.slurm.job.sh").exists())

    def test_render_job_supports_wps_substep_selection(self) -> None:
        adapter = get_scheduler_adapter("slurm")
        config = self.slurm_config()
        config["hpc"]["wps_runtime"] = {
            "mode": "remote_wps_dir",
            "remote_wps_dir": "$WPS_HOME",
        }
        project_state, plan, project_root = self.render_fixture()
        forcing_path = project_root / "data" / "gfs.t00z.pgrb2.1p00.f000"
        forcing_path.parent.mkdir(parents=True, exist_ok=True)
        forcing_path.write_text("gfs\n", encoding="utf-8")
        wps_plan = {
            **plan,
            "step": "wrf-wps",
            "forcing_files": [forcing_path.as_posix()],
            "selected_substeps": ["ungrib", "metgrid"],
        }

        rendered = adapter.render_job(project_state, wps_plan, config)
        script = Path(rendered["script_path"]).read_text(encoding="utf-8")

        self.assertIn('printf \'selected_substeps=%s\\n\' "ungrib,metgrid"', script)
        self.assertIn('run_logged_step "ungrib"', script)
        self.assertIn('run_logged_step "metgrid"', script)
        self.assertNotIn('run_logged_step "geogrid"', script)
        self.assertNotIn('run_logged_step "link_grib"', script)
        self.assertIn("rm -f FILE:* PFILE:* GFS:* met_em.d*.nc", script)

    def test_render_job_supports_run_substep_selection(self) -> None:
        adapter = get_scheduler_adapter("slurm")
        config = self.slurm_config()
        config["hpc"]["runtime"] = {
            "mode": "remote_run_dir",
            "remote_run_dir": "$WRF_HOME/run",
            "launcher_cmd": "mpiexec",
            "tasks_flag": "-n",
        }
        project_state, plan, _ = self.render_fixture()
        rendered = adapter.render_job(project_state, {**plan, "selected_substeps": ["wrf"]}, config)
        script = Path(rendered["script_path"]).read_text(encoding="utf-8")

        self.assertIn('printf \'selected_substeps=%s\\n\' "wrf"', script)
        self.assertIn('run_logged_step "wrf"', script)
        self.assertNotIn('run_logged_step "real"', script)
        self.assertIn("rm -f wrfout_d* rsl.*", script)
        self.assertIn("python3 /scratch/user/wrf_runs/demo/.wrf-skill/scripts/wrf_post.py", script)

    def test_validate_config_rejects_incomplete_custom_runtime(self) -> None:
        adapter = get_scheduler_adapter("slurm")
        config = self.slurm_config()
        config["hpc"]["runtime"] = {
            "mode": "custom",
            "real_cmd": "srun -n {total_tasks} /opt/wrf/run/real.exe",
        }

        summary = adapter.validate_config(config)

        self.assertFalse(summary["valid"])
        self.assertIn("hpc.runtime.wrf_cmd", summary["missing_fields"])
        self.assertEqual(summary["runtime_mode"], "custom")

    def test_pbs_render_job_includes_relative_met_em_staging_block(self) -> None:
        adapter = get_scheduler_adapter("pbs")
        config = self.pbs_config()
        project_state, plan, project_root = self.render_fixture()

        rendered = adapter.render_job(project_state, plan, config)
        script = Path(rendered["script_path"]).read_text(encoding="utf-8")

        self.assertIn("rm -f met_em.d*.nc", script)
        self.assertIn("met_em_sources=(../wps/met_em.d*.nc)", script)
        self.assertIn('ln -sfn "../wps/$name" "$name"', script)
        self.assertNotIn('ln -sfn "/scratch/user/wrf_runs/demo/wps/', script)
        self.assertTrue((project_root / "hpc" / "demo.pbs.job.sh").exists())

    def test_adapter_registry_accepts_new_backend_without_task_layer_changes(self) -> None:
        register_scheduler_adapter("dummy-test", DummyAdapter)
        adapter = get_scheduler_adapter("dummy-test")
        self.assertIsInstance(adapter, DummyAdapter)


if __name__ == "__main__":
    unittest.main()
