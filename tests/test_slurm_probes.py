import subprocess
import unittest
from unittest.mock import patch

from scripts.hpc.base import run_scheduler_json_command
from scripts.hpc.slurm_probes import (
    SACCTMGR_FORMAT,
    SINFO_FORMAT,
    probe_account_state,
    probe_cluster_state,
)


class SlurmProbeTests(unittest.TestCase):
    def hpc_config(self, *, access_mode: str = "login") -> dict:
        return {
            "hpc": {
                "enabled": True,
                "backend": "slurm",
                "access_mode": access_mode,
                "partition": "normal",
                "account": "myproject",
                "remote_base_dir": "/scratch/user/wrf_runs",
                "submit_cmd": "sbatch",
                "status_cmd": "squeue -j {job_id}",
                "cancel_cmd": "scancel {job_id}",
            }
        }

    def test_probe_cluster_state_aggregates_idle_and_mixed_rows(self) -> None:
        commands: list[list[str] | str] = []

        def runner(command: list[str] | str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                "Disk quotas for grp cxp (gid 30314):\nnormal*|up|idle|2|0/64/0/64\nnormal|up|mixed|2|32/32/0/64\n",
                "",
            )

        payload = probe_cluster_state("normal", runner=runner)
        partition = payload["partitions"]["normal"]

        self.assertTrue(partition["available"])
        self.assertEqual(partition["free_nodes"], 2)
        self.assertEqual(partition["free_tasks"], 96)
        self.assertEqual(partition["max_nodes"], 4)
        self.assertEqual(partition["max_total_tasks"], 128)
        self.assertEqual(commands[0], ["sinfo", "-h", "-p", "normal", "-o", SINFO_FORMAT])

    def test_probe_account_state_extracts_partition_limits(self) -> None:
        commands: list[list[str] | str] = []

        def runner(command: list[str] | str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                "Disk quotas for grp cxp (gid 30314):\nmyproject||cpu=256,node=8||2-00:00:00\nmyproject|normal|cpu=128,node=4|cpu=96,node=3|1-12:00:00\n",
                "",
            )

        payload = probe_account_state("myproject", "normal", runner=runner)
        account = payload["accounts"]["myproject"]
        partition = account["partitions"]["normal"]

        self.assertTrue(account["allowed"])
        self.assertTrue(partition["allowed"])
        self.assertEqual(account["max_nodes"], 3)
        self.assertEqual(account["max_total_tasks"], 96)
        self.assertEqual(partition["max_nodes"], 3)
        self.assertEqual(partition["max_total_tasks"], 96)
        self.assertEqual(partition["max_walltime_hours"], 36)
        self.assertEqual(
            commands[0],
            [
                "sacctmgr",
                "-n",
                "-P",
                "show",
                "assoc",
                "where",
                "account=myproject",
                f"format={SACCTMGR_FORMAT}",
            ],
        )

    @patch("scripts.hpc.base.run_scheduler_command")
    def test_builtin_probe_commands_render_partition_and_account_context(self, mock_run_scheduler_command) -> None:
        mock_run_scheduler_command.side_effect = [
            subprocess.CompletedProcess(
                ["sinfo"],
                0,
                "normal*|up|idle|2|0/64/0/64\n",
                "",
            ),
            subprocess.CompletedProcess(
                ["sacctmgr"],
                0,
                "myproject|normal|cpu=64,node=2|cpu=64,node=2|1-00:00:00\n",
                "",
            ),
        ]
        config = self.hpc_config()

        cluster_payload = run_scheduler_json_command(
            ["builtin:slurm_cluster_probe", "--partition", "{partition}"],
            config=config,
            context={"partition": "normal", "account": "myproject"},
        )
        account_payload = run_scheduler_json_command(
            ["builtin:slurm_account_probe", "--account", "{account}", "--partition", "{partition}"],
            config=config,
            context={"partition": "normal", "account": "myproject"},
        )

        self.assertEqual(cluster_payload["partitions"]["normal"]["free_tasks"], 64)
        self.assertTrue(account_payload["accounts"]["myproject"]["partitions"]["normal"]["allowed"])
        self.assertEqual(
            mock_run_scheduler_command.call_args_list[0].args[0],
            ["sinfo", "-h", "-p", "normal", "-o", SINFO_FORMAT],
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
                f"format={SACCTMGR_FORMAT}",
            ],
        )


if __name__ == "__main__":
    unittest.main()
