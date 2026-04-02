from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Sequence

SINFO_FORMAT = "%P|%a|%T|%D|%C"
SACCTMGR_FORMAT = "Account,Partition,GrpTRES,MaxTRES,MaxWall"
NON_SERVING_STATES = {
    "down",
    "drain",
    "draining",
    "drained",
    "fail",
    "failing",
    "maint",
    "maintenance",
    "power_down",
    "powered_down",
    "powering_down",
    "unknown",
}
FULLY_IDLE_STATES = {"idle"}

CommandRunner = Callable[[list[str] | str], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ClusterRow:
    partition: str
    availability: str
    state: str
    nodes: int
    allocated_cpus: int
    idle_cpus: int
    other_cpus: int
    total_cpus: int


@dataclass(frozen=True)
class AccountRow:
    account: str
    partitions: tuple[str, ...]
    grp_tres: dict[str, int]
    max_tres: dict[str, int]
    max_walltime_hours: int | None


def _run_text(command: list[str] | str, runner: CommandRunner | None = None) -> str:
    if runner is None:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    else:
        completed = runner(command)
    output = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0:
        raise RuntimeError(output or f"Command failed with exit code {completed.returncode}")
    return output


def normalize_partition_name(value: str) -> str:
    return value.replace("*", "").strip()


def normalize_state_token(value: str) -> str:
    token = value.strip().lower()
    token = token.split("+", 1)[0]
    token = re.sub(r"[^a-z_]+$", "", token)
    aliases = {
        "alloc": "allocated",
        "comp": "completing",
        "drng": "draining",
        "drain": "draining",
        "mix": "mixed",
    }
    return aliases.get(token, token)


def parse_cpu_state(value: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 4:
        raise ValueError(f"Invalid Slurm CPU summary: {value}")
    return tuple(int(part or 0) for part in parts)  # type: ignore[return-value]


def parse_sinfo_output(output: str, partition: str) -> list[ClusterRow]:
    rows: list[ClusterRow] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            continue
        fields = [field.strip() for field in line.split("|", 4)]
        if len(fields) != 5:
            continue
        raw_partition, availability, raw_state, nodes_raw, cpu_state = fields
        resolved_partition = normalize_partition_name(raw_partition)
        if resolved_partition != partition:
            continue
        allocated, idle, other, total = parse_cpu_state(cpu_state)
        rows.append(
            ClusterRow(
                partition=resolved_partition,
                availability=availability.lower(),
                state=normalize_state_token(raw_state),
                nodes=int(nodes_raw),
                allocated_cpus=allocated,
                idle_cpus=idle,
                other_cpus=other,
                total_cpus=total,
            )
        )
    return rows


def probe_cluster_state(
    partition: str,
    *,
    runner: CommandRunner | None = None,
    sinfo_cmd: str = "sinfo",
) -> dict[str, Any]:
    output = _run_text(
        [sinfo_cmd, "-h", "-p", partition, "-o", SINFO_FORMAT],
        runner=runner,
    )
    rows = parse_sinfo_output(output, partition)
    if not rows:
        raise RuntimeError(f"No sinfo rows returned for partition {partition}")

    available = any(row.availability == "up" and row.state not in NON_SERVING_STATES for row in rows)
    free_nodes = sum(
        row.nodes
        for row in rows
        if row.availability == "up" and row.state in FULLY_IDLE_STATES
    )
    free_tasks = sum(
        row.idle_cpus
        for row in rows
        if row.availability == "up" and row.state not in NON_SERVING_STATES
    )
    max_nodes = sum(row.nodes for row in rows)
    max_total_tasks = sum(row.total_cpus for row in rows)

    return {
        "partitions": {
            partition: {
                "available": available,
                "queue_allowed": available,
                "free_nodes": free_nodes,
                "free_tasks": free_tasks,
                "max_nodes": max_nodes,
                "max_total_tasks": max_total_tasks,
                "detail": [
                    {
                        "partition": row.partition,
                        "availability": row.availability,
                        "state": row.state,
                        "nodes": row.nodes,
                        "allocated_cpus": row.allocated_cpus,
                        "idle_cpus": row.idle_cpus,
                        "other_cpus": row.other_cpus,
                        "total_cpus": row.total_cpus,
                    }
                    for row in rows
                ],
            }
        }
    }


def parse_partition_values(value: str) -> tuple[str, ...]:
    token = value.strip()
    if not token:
        return ("",)
    parts = [item.strip() for item in token.split(",") if item.strip()]
    return tuple(parts or [""])


def parse_tres_limits(value: str) -> dict[str, int]:
    limits: dict[str, int] = {}
    token = value.strip()
    if not token:
        return limits
    for item in token.split(","):
        if "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        match = re.search(r"-?\d+", raw_value)
        if not match:
            continue
        try:
            limits[key.strip().lower()] = int(match.group(0))
        except ValueError:
            continue
    return limits


def parse_time_limit_hours(value: str) -> int | None:
    token = value.strip()
    if not token or token.lower() in {"none", "n/a", "infinite", "infinity", "unlimited"}:
        return None
    days = 0
    clock = token
    if "-" in token:
        day_token, clock = token.split("-", 1)
        days = int(day_token)
    parts = [int(part) for part in clock.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes = parts
        seconds = 0
    elif len(parts) == 1:
        hours = parts[0]
        minutes = 0
        seconds = 0
    else:
        raise ValueError(f"Invalid Slurm time limit: {value}")
    total_seconds = (((days * 24) + hours) * 60 + minutes) * 60 + seconds
    return max(1, int(math.ceil(total_seconds / 3600)))


def parse_sacctmgr_output(output: str, account: str) -> list[AccountRow]:
    rows: list[AccountRow] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            continue
        fields = [field.strip() for field in line.split("|", 4)]
        if len(fields) != 5:
            continue
        raw_account, partition_value, grp_tres, max_tres, max_wall = fields
        if raw_account != account:
            continue
        rows.append(
            AccountRow(
                account=raw_account,
                partitions=parse_partition_values(partition_value),
                grp_tres=parse_tres_limits(grp_tres),
                max_tres=parse_tres_limits(max_tres),
                max_walltime_hours=parse_time_limit_hours(max_wall),
            )
        )
    return rows


def _min_defined(values: Sequence[int | None]) -> int | None:
    defined = [value for value in values if value is not None]
    if not defined:
        return None
    return min(defined)


def _row_limit(row: AccountRow, key: str) -> int | None:
    return _min_defined([row.max_tres.get(key), row.grp_tres.get(key)])


def _rows_limit(rows: Sequence[AccountRow], key: str) -> int | None:
    return _min_defined([_row_limit(row, key) for row in rows])


def _rows_walltime(rows: Sequence[AccountRow]) -> int | None:
    return _min_defined([row.max_walltime_hours for row in rows])


def _matches_partition(row: AccountRow, partition: str) -> bool:
    if "" in row.partitions:
        return True
    return partition in row.partitions


def probe_account_state(
    account: str,
    partition: str,
    *,
    runner: CommandRunner | None = None,
    sacctmgr_cmd: str = "sacctmgr",
) -> dict[str, Any]:
    output = _run_text(
        [
            sacctmgr_cmd,
            "-n",
            "-P",
            "show",
            "assoc",
            "where",
            f"account={account}",
            f"format={SACCTMGR_FORMAT}",
        ],
        runner=runner,
    )
    rows = parse_sacctmgr_output(output, account)
    matching_rows = [row for row in rows if _matches_partition(row, partition)]
    account_allowed = bool(rows)
    partition_allowed = bool(matching_rows)

    return {
        "accounts": {
            account: {
                "allowed": account_allowed,
                "max_nodes": _rows_limit(rows, "node"),
                "max_total_tasks": _rows_limit(rows, "cpu"),
                "max_walltime_hours": _rows_walltime(rows),
                "partitions": {
                    partition: {
                        "allowed": partition_allowed,
                        "queue_allowed": partition_allowed,
                        "max_nodes": _rows_limit(matching_rows, "node"),
                        "max_total_tasks": _rows_limit(matching_rows, "cpu"),
                        "max_walltime_hours": _rows_walltime(matching_rows),
                    }
                },
                "detail": [
                    {
                        "account": row.account,
                        "partitions": list(row.partitions),
                        "grp_tres": row.grp_tres,
                        "max_tres": row.max_tres,
                        "max_walltime_hours": row.max_walltime_hours,
                    }
                    for row in rows
                ],
            }
        }
    }


def run_cluster_probe_from_args(args: Sequence[str], *, runner: CommandRunner | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Probe Slurm partition capacity")
    parser.add_argument("--partition", required=True)
    parser.add_argument("--sinfo-cmd", default="sinfo")
    parsed = parser.parse_args(list(args))
    return probe_cluster_state(parsed.partition, runner=runner, sinfo_cmd=parsed.sinfo_cmd)


def run_account_probe_from_args(args: Sequence[str], *, runner: CommandRunner | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Probe Slurm account capacity")
    parser.add_argument("--account", required=True)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--sacctmgr-cmd", default="sacctmgr")
    parsed = parser.parse_args(list(args))
    return probe_account_state(
        parsed.account,
        parsed.partition,
        runner=runner,
        sacctmgr_cmd=parsed.sacctmgr_cmd,
    )


def execute_builtin_probe(command: Sequence[str], *, runner: CommandRunner) -> dict[str, Any]:
    if not command:
        raise ValueError("Missing builtin probe command")
    probe_name = command[0]
    args = command[1:]
    if probe_name == "builtin:slurm_cluster_probe":
        return run_cluster_probe_from_args(args, runner=runner)
    if probe_name == "builtin:slurm_account_probe":
        return run_account_probe_from_args(args, runner=runner)
    raise KeyError(f"Unsupported builtin probe: {probe_name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Slurm probe helpers for WRF HPC admission")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cluster = subparsers.add_parser("cluster")
    cluster.add_argument("--partition", required=True)
    cluster.add_argument("--sinfo-cmd", default="sinfo")

    account = subparsers.add_parser("account")
    account.add_argument("--account", required=True)
    account.add_argument("--partition", required=True)
    account.add_argument("--sacctmgr-cmd", default="sacctmgr")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "cluster":
        payload = probe_cluster_state(args.partition, sinfo_cmd=args.sinfo_cmd)
    else:
        payload = probe_account_state(args.account, args.partition, sacctmgr_cmd=args.sacctmgr_cmd)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
