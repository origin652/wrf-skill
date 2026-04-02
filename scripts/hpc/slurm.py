from __future__ import annotations

import re
from typing import Any

from .base import (
    HpcSchedulerAdapter,
    _command_with_target,
    extract_json_if_possible,
    first_match,
    hpc_config,
    map_slurm_state,
    run_scheduler_command,
)


class SlurmSchedulerAdapter(HpcSchedulerAdapter):
    backend_name = "slurm"
    template_name = "slurm_wrf.sh.template"
    wps_template_name = "slurm_wps.sh.template"

    def parse_submit_output(self, output: str) -> str | None:
        match = re.search(r"Submitted batch job\s+(\d+)", output)
        if match:
            return match.group(1)
        match = re.search(r"\b(\d+)\b", output)
        return match.group(1) if match else None

    def _terminal_status_command(self, job_id: str, config: dict[str, Any]) -> list[str]:
        fallback_command = hpc_config(config).get("terminal_status_cmd") or [
            "sacct",
            "-n",
            "-P",
            "-j",
            "{job_id}",
            "-o",
            "JobIDRaw,State,ExitCode",
        ]
        return _command_with_target(fallback_command, job_id, placeholder="job_id")

    def _map_terminal_state(self, raw_state: str, exit_code: str) -> str | None:
        normalized_state = raw_state.strip().split()[0].upper()
        unified = map_slurm_state(normalized_state)
        if unified != "completed":
            return unified

        exit_token = exit_code.strip().split(":", 1)[0]
        if exit_token and exit_token not in {"0", "0.0"}:
            return "failed"
        return unified

    def parse_terminal_query_output(self, output: str, job_id: str) -> dict[str, str]:
        for line in output.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            fields = [field.strip() for field in line.split("|", 2)]
            if len(fields) != 3:
                continue
            row_job_id, raw_state, exit_code = fields
            normalized_job_id = row_job_id.split(".", 1)[0]
            if normalized_job_id != job_id:
                continue
            unified = self._map_terminal_state(raw_state, exit_code)
            if unified:
                return {
                    "job_id": job_id,
                    "state": unified,
                    "raw_state": raw_state,
                    "detail": output,
                }
        raise RuntimeError(f"Unable to parse slurm terminal query output for job {job_id}: {output}")

    def query(self, job_handle: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
        job_id = str(job_handle["job_id"])
        primary_command = _command_with_target(
            hpc_config(config)["status_cmd"],
            job_id,
            placeholder="job_id",
        )
        primary = run_scheduler_command(primary_command, config=config)
        primary_output = primary.stdout.strip() or primary.stderr.strip()
        if primary.returncode == 0:
            try:
                return self.parse_query_output(primary_output, job_id)
            except RuntimeError:
                pass

        fallback_command = self._terminal_status_command(job_id, config)
        fallback = run_scheduler_command(fallback_command, config=config)
        fallback_output = fallback.stdout.strip() or fallback.stderr.strip()
        if fallback.returncode == 0:
            try:
                return self.parse_terminal_query_output(fallback_output, job_id)
            except RuntimeError:
                pass

        raise RuntimeError(primary_output or fallback_output or "HPC job query failed")

    def parse_query_output(self, output: str, job_id: str) -> dict[str, str]:
        payload = extract_json_if_possible(output)
        if payload:
            jobs = payload.get("jobs", [])
            for job in jobs:
                raw_state = str(job.get("job_state") or job.get("state") or "")
                unified = map_slurm_state(raw_state)
                if unified:
                    return {
                        "job_id": str(job.get("job_id") or job_id),
                        "state": unified,
                        "raw_state": raw_state,
                        "detail": output,
                    }

        for line in output.splitlines():
            tokens = line.split()
            if not tokens:
                continue
            if tokens[0].upper() == "JOBID":
                continue
            if tokens[0] != str(job_id):
                continue
            for token in tokens[1:]:
                unified = map_slurm_state(token)
                if unified:
                    return {
                        "job_id": job_id,
                        "state": unified,
                        "raw_state": token,
                        "detail": output,
                    }

        raw_state = first_match(r"\b(PENDING|RUNNING|COMPLETED|FAILED|CANCELLED|CANCELED|TIMEOUT|NODE_FAIL|OUT_OF_MEMORY)\b", output)
        unified = map_slurm_state(raw_state or "")
        if unified:
            return {
                "job_id": job_id,
                "state": unified,
                "raw_state": raw_state,
                "detail": output,
            }
        raise RuntimeError(f"Unable to parse slurm query output for job {job_id}: {output}")
