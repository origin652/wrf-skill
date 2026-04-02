import sys
import unittest
from unittest.mock import patch

from scripts.hpc.admission import evaluate_admission


def json_cmd(payload: dict) -> list[str]:
    return [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"]


class HpcAdmissionTests(unittest.TestCase):
    def make_spec(self, *, end_time: str = "2024-07-20_12:00:00", domains: list[dict] | None = None) -> dict:
        return {
            "project_name": "demo",
            "data_source": "gfs",
            "start_time": "2024-07-20_00:00:00",
            "end_time": end_time,
            "run_mode": "hpc",
            "domains": domains
            or [
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
                }
            ],
            "physics": {"mp_physics": 6, "cu_physics": 1, "ra_lw_physics": 4, "ra_sw_physics": 4, "bl_pbl_physics": 1},
        }

    def make_config(self, *, cluster_payload: dict, account_payload: dict, limits: dict | None = None) -> dict:
        return {
            "local": {"default_np": 8, "mpi_cmd": "mpirun"},
            "hpc": {
                "enabled": True,
                "backend": "slurm",
                "access_mode": "login",
                "remote_host": "login.cluster.example",
                "remote_base_dir": "/scratch/user/wrf_runs",
                "partition": "normal",
                "account": "myproject",
                "cores_per_node": 32,
                "submit_cmd": "sbatch",
                "status_cmd": "squeue -j {job_id}",
                "cancel_cmd": "scancel {job_id}",
                "limits": {
                    "max_nodes": 4,
                    "max_total_tasks": 128,
                    "max_forecast_hours": 72,
                    "max_domains": 3,
                    "max_total_grid_points": 400000,
                    "max_walltime_hours": 24,
                    **(limits or {}),
                },
                "probes": {
                    "cluster_state_cmd": json_cmd(cluster_payload),
                    "account_state_cmd": json_cmd(account_payload),
                },
            },
        }

    def cluster_payload(self, **overrides: object) -> dict:
        return {
            "partitions": {
                "normal": {
                    "available": True,
                    "free_nodes": 4,
                    "free_tasks": 128,
                    "queue_allowed": True,
                    "max_nodes": 4,
                    "max_total_tasks": 128,
                    **overrides,
                }
            }
        }

    def account_payload(self, *, allowed: bool = True, partition_allowed: bool = True, **overrides: object) -> dict:
        return {
            "accounts": {
                "myproject": {
                    "allowed": allowed,
                    "partitions": {
                        "normal": {
                            "allowed": partition_allowed,
                            "queue_allowed": True,
                            "max_nodes": 4,
                            "max_total_tasks": 128,
                            **overrides,
                        }
                    },
                }
            }
        }

    def test_static_forecast_limit_rejects_and_suggests_shorter_window(self) -> None:
        spec = self.make_spec(end_time="2024-07-20_12:00:00")
        config = self.make_config(
            cluster_payload=self.cluster_payload(),
            account_payload=self.account_payload(),
            limits={"max_forecast_hours": 6},
        )

        admission = evaluate_admission(spec, config)

        self.assertEqual(admission["decision"], "rejected")
        self.assertIn("FORECAST_HOURS_EXCEEDED", admission["reason_codes"])
        self.assertEqual(admission["alternatives"][0]["kind"], "shorten_forecast_window")

    def test_permission_denied_rejects_hpc_submission(self) -> None:
        spec = self.make_spec()
        config = self.make_config(
            cluster_payload=self.cluster_payload(),
            account_payload=self.account_payload(allowed=False),
        )

        admission = evaluate_admission(spec, config)

        self.assertEqual(admission["decision"], "rejected")
        self.assertIn("PARTITION_OR_ACCOUNT_DENIED", admission["reason_codes"])

    def test_insufficient_free_capacity_but_queue_allowed_returns_admissible_with_queue(self) -> None:
        spec = self.make_spec(domains=[{**self.make_spec()["domains"][0], "e_we": 400, "e_sn": 400}])
        config = self.make_config(
            cluster_payload=self.cluster_payload(free_nodes=1, free_tasks=2, queue_allowed=True),
            account_payload=self.account_payload(),
        )

        admission = evaluate_admission(spec, config)

        self.assertEqual(admission["decision"], "admissible_with_queue")
        self.assertIn("QUEUE_EXPECTED", admission["reason_codes"])

    @patch("scripts.hpc.base.run_scheduler_command")
    def test_builtin_probe_commands_are_supported_end_to_end(self, mock_run_scheduler_command) -> None:
        spec = self.make_spec()
        config = self.make_config(
            cluster_payload=self.cluster_payload(),
            account_payload=self.account_payload(),
        )
        config["hpc"]["probes"] = {
            "cluster_state_cmd": ["builtin:slurm_cluster_probe", "--partition", "{partition}"],
            "account_state_cmd": ["builtin:slurm_account_probe", "--account", "{account}", "--partition", "{partition}"],
        }
        mock_run_scheduler_command.side_effect = [
            __import__("subprocess").CompletedProcess(
                ["sinfo"],
                0,
                "normal*|up|idle|4|0/128/0/128\n",
                "",
            ),
            __import__("subprocess").CompletedProcess(
                ["sacctmgr"],
                0,
                "myproject|normal|cpu=128,node=4|cpu=128,node=4|1-00:00:00\n",
                "",
            ),
        ]

        admission = evaluate_admission(spec, config)

        self.assertEqual(admission["decision"], "admissible_now")
        self.assertEqual(
            mock_run_scheduler_command.call_args_list[0].args[0],
            ["sinfo", "-h", "-p", "normal", "-o", "%P|%a|%T|%D|%C"],
        )
        self.assertEqual(
            mock_run_scheduler_command.call_args_list[1].args[0],
            [
                "sacctmgr",
                "-n",
                "-P",
                "show",
                "assoc",
                "where",
                "account=myproject",
                "format=Account,Partition,GrpTRES,MaxTRES,MaxWall",
            ],
        )

    def test_probe_failure_returns_unverified(self) -> None:
        spec = self.make_spec()
        config = self.make_config(
            cluster_payload=self.cluster_payload(),
            account_payload=self.account_payload(),
        )
        config["hpc"]["probes"]["cluster_state_cmd"] = [sys.executable, "-c", "print('not-json')"]

        admission = evaluate_admission(spec, config)

        self.assertEqual(admission["decision"], "unverified")
        self.assertIn("PROBE_UNAVAILABLE", admission["reason_codes"])

    def test_grid_size_limit_rejects_and_suggests_reducing_grid(self) -> None:
        domains = [
            {**self.make_spec()["domains"][0], "e_we": 220, "e_sn": 220},
            {**self.make_spec()["domains"][0], "name": "d02", "parent_id": 1, "parent_grid_ratio": 3, "e_we": 220, "e_sn": 220},
        ]
        spec = self.make_spec(domains=domains)
        config = self.make_config(
            cluster_payload=self.cluster_payload(),
            account_payload=self.account_payload(),
            limits={"max_total_grid_points": 50000, "max_domains": 1},
        )

        admission = evaluate_admission(spec, config)

        self.assertEqual(admission["decision"], "rejected")
        self.assertIn("GRID_POINTS_EXCEEDED", admission["reason_codes"])
        self.assertIn("DOMAIN_COUNT_EXCEEDED", admission["reason_codes"])
        self.assertEqual(admission["alternatives"][0]["kind"], "reduce_grid_size")


if __name__ == "__main__":
    unittest.main()
