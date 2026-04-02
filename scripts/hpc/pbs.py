from __future__ import annotations

import re

from .base import HpcSchedulerAdapter, extract_json_if_possible, map_pbs_state


class PbsSchedulerAdapter(HpcSchedulerAdapter):
    backend_name = "pbs"
    template_name = "pbs_wrf.sh.template"
    wps_template_name = "pbs_wps.sh.template"

    def parse_submit_output(self, output: str) -> str | None:
        match = re.search(r"(\d+(?:\.[A-Za-z0-9._-]+)?)", output)
        return match.group(1) if match else None

    def parse_query_output(self, output: str, job_id: str) -> dict[str, str]:
        payload = extract_json_if_possible(output)
        if payload:
            jobs = payload.get("Jobs", payload.get("jobs", {}))
            if isinstance(jobs, dict):
                job = jobs.get(job_id) or next(iter(jobs.values()), {})
                raw_state = str(job.get("job_state") or job.get("state") or "")
                exit_status = job.get("Exit_status")
                unified = map_pbs_state(raw_state, exit_status=exit_status)
                if unified:
                    return {
                        "job_id": job_id,
                        "state": unified,
                        "raw_state": raw_state,
                        "detail": output,
                    }

        state_match = re.search(r"job_state\s*=\s*([A-Z])", output)
        exit_match = re.search(r"Exit_status\s*=\s*(-?\d+)", output)
        if state_match:
            exit_status = int(exit_match.group(1)) if exit_match else None
            raw_state = state_match.group(1)
            unified = map_pbs_state(raw_state, exit_status=exit_status)
            if unified:
                return {
                    "job_id": job_id,
                    "state": unified,
                    "raw_state": raw_state,
                    "detail": output,
                }

        for line in output.splitlines():
            tokens = line.split()
            if not tokens:
                continue
            if tokens[0].lower() in {"job", "jobid"}:
                continue
            if tokens[0] != job_id:
                continue
            for token in reversed(tokens[1:]):
                unified = map_pbs_state(token)
                if unified:
                    return {
                        "job_id": job_id,
                        "state": unified,
                        "raw_state": token,
                        "detail": output,
                    }

        raise RuntimeError(f"Unable to parse pbs query output for job {job_id}: {output}")
