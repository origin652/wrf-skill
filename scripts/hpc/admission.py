from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from . import get_scheduler_adapter
from .base import merge_hpc_limits, resolve_access_mode, resolve_backend_name

try:
    from spec_utils import normalize_spec, parse_time
except ImportError:  # pragma: no cover
    from ..spec_utils import normalize_spec, parse_time


def forecast_hours(spec: dict[str, Any]) -> int:
    normalized = normalize_spec(spec)
    delta = parse_time(normalized["timing"]["end_time"]) - parse_time(normalized["timing"]["start_time"])
    return max(0, int(delta.total_seconds() // 3600))


def total_grid_points(spec: dict[str, Any]) -> int:
    normalized = normalize_spec(spec)
    return sum(int(domain["e_we"]) * int(domain["e_sn"]) for domain in normalized.get("domains", []))


def estimate_walltime_hours(spec: dict[str, Any]) -> int:
    hours = max(1, forecast_hours(spec))
    grid_points = max(1, total_grid_points(spec))
    return max(1, math.ceil((hours * grid_points) / 2500000))


def build_request(spec: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_spec(spec)
    request = {
        "forecast_hours": forecast_hours(normalized),
        "domain_count": len(normalized.get("domains", [])),
        "total_grid_points": total_grid_points(normalized),
        "estimated_walltime_hours": estimate_walltime_hours(normalized),
        "backend": resolve_backend_name(config),
    }
    adapter = get_scheduler_adapter(config)
    request["recommended_layout"] = adapter.recommend_layout(request, config)
    request["requested_layout"] = deepcopy(request["recommended_layout"])
    return request


def generate_alternatives(
    request: dict[str, Any],
    static_limits: dict[str, Any],
    recommended_layout: dict[str, Any],
    reason_codes: list[str],
) -> list[dict[str, Any]]:
    alternatives: list[dict[str, Any]] = []
    if "FORECAST_HOURS_EXCEEDED" in reason_codes and static_limits.get("max_forecast_hours") is not None:
        alternatives.append(
            {
                "kind": "shorten_forecast_window",
                "message": f"Shorten forecast window to <= {static_limits['max_forecast_hours']} hours",
                "changes": {"forecast_hours": static_limits["max_forecast_hours"]},
            }
        )
    if any(code in reason_codes for code in ("GRID_POINTS_EXCEEDED", "DOMAIN_COUNT_EXCEEDED")):
        alternatives.append(
            {
                "kind": "reduce_grid_size",
                "message": "Reduce nesting count or increase dx/dy to shrink total grid points",
                "changes": {
                    "max_domains": static_limits.get("max_domains"),
                    "max_total_grid_points": static_limits.get("max_total_grid_points"),
                },
            }
        )
    if any(code in reason_codes for code in ("TOTAL_TASKS_EXCEEDED", "NODE_COUNT_EXCEEDED", "LIVE_MAX_TASKS_EXCEEDED", "LIVE_MAX_NODES_EXCEEDED")):
        alternatives.append(
            {
                "kind": "lower_parallelism",
                "message": "Lower nodes/tasks to fit the permitted resource envelope",
                "changes": {"recommended_layout": recommended_layout},
            }
        )
    if any(code in reason_codes for code in ("PARTITION_OR_ACCOUNT_DENIED", "HPC_DISABLED")):
        alternatives.append(
            {
                "kind": "fallback_execution_mode",
                "message": "Use local mode or request a lower-spec layout with an allowed account/partition",
                "changes": {"run_mode": "local"},
            }
        )
    if any(code in reason_codes for code in ("PROBE_UNAVAILABLE", "INSUFFICIENT_LIVE_CAPACITY", "QUEUE_EXPECTED")):
        alternatives.append(
            {
                "kind": "queue_or_retry_later",
                "message": "Retry later or choose a smaller domain/shorter forecast to reduce queue pressure",
                "changes": {"recommended_layout": recommended_layout},
            }
        )
    return alternatives


def evaluate_admission(spec: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    adapter = get_scheduler_adapter(config)
    static_limits = merge_hpc_limits(config)
    request = build_request(spec, config)
    requested_layout = request["requested_layout"]
    recommended_layout = request["recommended_layout"]
    reason_codes: list[str] = []

    if not config.get("hpc", {}).get("enabled", False):
        reason_codes.append("HPC_DISABLED")
    if static_limits.get("max_forecast_hours") is not None and request["forecast_hours"] > int(static_limits["max_forecast_hours"]):
        reason_codes.append("FORECAST_HOURS_EXCEEDED")
    if static_limits.get("max_domains") is not None and request["domain_count"] > int(static_limits["max_domains"]):
        reason_codes.append("DOMAIN_COUNT_EXCEEDED")
    if static_limits.get("max_total_grid_points") is not None and request["total_grid_points"] > int(static_limits["max_total_grid_points"]):
        reason_codes.append("GRID_POINTS_EXCEEDED")
    if static_limits.get("max_walltime_hours") is not None and recommended_layout["walltime_hours"] > int(static_limits["max_walltime_hours"]):
        reason_codes.append("WALLTIME_EXCEEDED")
    if static_limits.get("max_nodes") is not None and requested_layout["nodes"] > int(static_limits["max_nodes"]):
        reason_codes.append("NODE_COUNT_EXCEEDED")
    if static_limits.get("max_total_tasks") is not None and requested_layout["total_tasks"] > int(static_limits["max_total_tasks"]):
        reason_codes.append("TOTAL_TASKS_EXCEEDED")

    if reason_codes:
        decision = "rejected"
        probe_result = {"decision": decision, "reason_codes": [], "live_cluster": {}}
    else:
        probe_result = adapter.probe_resources(request, config)
        reason_codes.extend(probe_result.get("reason_codes", []))
        decision = probe_result["decision"]

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "requested_layout": requested_layout,
        "recommended_layout": recommended_layout,
        "static_limits": static_limits,
        "live_cluster": probe_result.get("live_cluster", {}),
        "alternatives": generate_alternatives(
            request,
            static_limits,
            recommended_layout,
            reason_codes,
        ),
        "request_summary": {
            "forecast_hours": request["forecast_hours"],
            "domain_count": request["domain_count"],
            "total_grid_points": request["total_grid_points"],
            "estimated_walltime_hours": request["estimated_walltime_hours"],
            "backend": resolve_backend_name(config),
            "access_mode": resolve_access_mode(config),
        },
    }
