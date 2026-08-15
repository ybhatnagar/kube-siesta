"""Stage 6a — Cost & savings.

savings = cost × idle_fraction, where cost = node_fraction × node_$/hr × replicas.
node_fraction comes from requests vs node capacity when known (disc_workloads), else
from observed active usage as a proxy. OpenCost integration is a later swap-in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ...analysis_core.config import EngineConfig


@dataclass
class Cost:
    monthly_cost: float
    monthly_savings: float
    idle_fraction: float
    node_fraction: float


def estimate(
    idle_fraction: float,
    cfg: EngineConfig,
    *,
    requests_cpu_m: Optional[float] = None,
    requests_mem_bytes: Optional[float] = None,
    replicas: Optional[int] = None,
    usage_cpu_cores: Optional[float] = None,
    usage_mem_bytes: Optional[float] = None,
) -> Cost:
    if requests_cpu_m and requests_mem_bytes:
        node_fraction = 0.5 * (requests_cpu_m / cfg.node_cpu_m + requests_mem_bytes / cfg.node_mem_bytes)
    else:
        # Fall back to observed active usage as the node-fraction proxy.
        parts = []
        if usage_cpu_cores:
            parts.append(usage_cpu_cores * 1000.0 / cfg.node_cpu_m)
        if usage_mem_bytes:
            parts.append(usage_mem_bytes / cfg.node_mem_bytes)
        node_fraction = sum(parts) / len(parts) if parts else 0.05

    node_fraction = min(max(node_fraction, 0.001), 1.0)
    hourly = node_fraction * cfg.node_hourly_cost * (replicas or 1)
    monthly = hourly * cfg.hours_per_month
    return Cost(
        monthly_cost=round(monthly, 2),
        monthly_savings=round(monthly * idle_fraction, 2),
        idle_fraction=idle_fraction,
        node_fraction=node_fraction,
    )
